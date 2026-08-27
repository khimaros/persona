"""a minimal, dependency-free hmux hub client for the eval.

the hub speaks a raw hello/welcome handshake followed by json-rpc 2.0 over a
websocket, fanning session activity out to every connected client as
`session.event` notifications. this module is a stdlib-only client for that
protocol so the eval drives the hmux NATIVE api directly, with no opencode-wire
face in between.

adapted from hmux's own e2e client (../hmux/e2e/hmuxhub.py) and the websocket
transport it reuses (../fake-openai clients/python), vendored here so the eval
stays self-contained.
"""

import base64
import json
import os
import socket
import struct
import threading
import time
from collections import deque

# hard cap per notification stream: a runaway turn can flood millions of session.event
# deltas, so bound storage (drop oldest) to keep a wedged model from exhausting memory. a
# normal run stays far under this; the eval also aborts a runaway early (RUNAWAY_MAX_EVENTS).
MAX_STORED = 500_000

_WS_TEXT, _WS_CLOSE, _WS_PING, _WS_PONG = 0x1, 0x8, 0x9, 0xA
PROTOCOL_VERSION = 1


class _WS:
    """minimal RFC6455 text websocket client over a raw socket."""

    def __init__(self, host, port, path="/ws", timeout=30):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake closed early")
            resp += chunk
        head, _, rest = resp.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"websocket handshake failed: {head.splitlines()[0]!r}")
        self._buf = rest

    def _send(self, opcode, payload):
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send_json(self, obj):
        self._send(_WS_TEXT, json.dumps(obj).encode())

    def _need(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket closed")
            self._buf += chunk

    def _frame(self):
        self._need(2)
        b1 = self._buf[1]
        opcode = self._buf[0] & 0x0F
        length = b1 & 0x7F
        off = 2
        if length == 126:
            self._need(4)
            length = struct.unpack(">H", self._buf[2:4])[0]
            off = 4
        elif length == 127:
            self._need(10)
            length = struct.unpack(">Q", self._buf[2:10])[0]
            off = 10
        mask = b""
        if b1 & 0x80:
            self._need(off + 4)
            mask = self._buf[off:off + 4]
            off += 4
        self._need(off + length)
        data = bytearray(self._buf[off:off + length])
        self._buf = self._buf[off + length:]
        if mask:
            for i in range(len(data)):
                data[i] ^= mask[i % 4]
        return opcode, bytes(data)

    def recv_json(self, timeout=None):
        if timeout is not None:
            self.sock.settimeout(timeout)
        while True:
            opcode, data = self._frame()
            if opcode == _WS_TEXT:
                return json.loads(data.decode())
            if opcode == _WS_CLOSE:
                raise ConnectionError("websocket closed by server")
            if opcode == _WS_PING:
                self._control(_WS_PONG, data)

    def _control(self, opcode, data=b""):
        mask = os.urandom(4)
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(data)]) + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def close(self):
        for step in (lambda: self._control(_WS_CLOSE), self.sock.close):
            try:
                step()
            except OSError:
                pass


def _split_ws(ws_url):
    """ws://host:port/path -> (host, port, /path)."""
    rest = ws_url.split("://", 1)[1]
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port), "/" + path


class HubClient:
    """a hub client: the hello/welcome handshake, json-rpc 2.0 request/response
    correlation, and fanned-out notifications collected by a background reader."""

    # THE ROLE IS `face`, NOT `client` (hmux roadmap 60e). `PeerRole` is `harness | face`; a
    # hello carrying a role the hub does not know is REFUSED -- and a refused hello presents as a
    # HANG, not an error, because the client sits waiting for a welcome that never comes. so a
    # stale value here reads as "the hub is slow" for the full handshake timeout.
    def __init__(self, ws_url, role="face", handshake_timeout=10):
        host, port, path = _split_ws(ws_url)
        self.ws = _WS(host, port, path)
        # hello + welcome are raw json frames; everything after is json-rpc.
        self.ws.send_json({"role": role, "protocol_version": PROTOCOL_VERSION})
        self.welcome = self.ws.recv_json(timeout=handshake_timeout)
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._responses = {}
        self._cv = threading.Condition()
        self._notifications = deque(maxlen=MAX_STORED)
        self._by_method = {}  # method -> bounded deque, so notifications(method) stays
        self._notif_lock = threading.Lock()  # O(matches) and memory stays bounded under a flood
        self._closed = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while not self._closed:
            try:
                frame = self.ws.recv_json()
            except TimeoutError:
                continue
            except (ConnectionError, OSError):
                break
            if not isinstance(frame, dict):
                continue
            if "method" in frame:
                if "id" not in frame:  # a notification (inbound requests are ignored)
                    entry = (frame["method"], frame.get("params"))
                    with self._notif_lock:
                        self._notifications.append(entry)
                        dq = self._by_method.get(frame["method"])
                        if dq is None:
                            dq = deque(maxlen=MAX_STORED)
                            self._by_method[frame["method"]] = dq
                        dq.append(entry)
            elif "id" in frame:
                with self._cv:
                    self._responses[frame["id"]] = frame
                    self._cv.notify_all()

    def request(self, method, params=None, timeout=30):
        with self._id_lock:
            rid = self._next_id
            self._next_id += 1
        self.ws.send_json({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        with self._cv:
            while rid not in self._responses:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"hub did not respond to {method}")
                self._cv.wait(remaining)
            frame = self._responses.pop(rid)
        if frame.get("error"):
            raise RuntimeError(f"{method} failed: {frame['error']}")
        return frame.get("result")

    def notify(self, method, params=None):
        self.ws.send_json({"jsonrpc": "2.0", "method": method, "params": params})

    def notifications(self, method=None):
        with self._notif_lock:
            if method is None:
                return list(self._notifications)
            dq = self._by_method.get(method)
            return list(dq) if dq else []

    def notification_count(self, method):
        """O(1) count of a notification stream, for the eval's runaway guard."""
        with self._notif_lock:
            dq = self._by_method.get(method)
            return len(dq) if dq else 0

    def session_events(self, session_id):
        """the fanned-out session.event params for one session, in arrival order."""
        return [p for (_m, p) in self.notifications("session.event")
                if p and p.get("session_id") == session_id]

    def close(self):
        self._closed = True
        self.ws.close()
