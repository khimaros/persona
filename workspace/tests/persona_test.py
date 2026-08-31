#!/usr/bin/env python3
"""end-to-end tests for the persona hook dispatcher (JSONL IPC over a memory endpoint).

WHAT THIS FILE STOPPED TESTING (hmux phase 74). it was 94KB, and most of it covered a storage
engine persona no longer has: trait/document/record tools, a mongo filter evaluator, a dot-path
updater. all of that moved into `hmux-memory`, where it is covered by 61 rust tests -- cheaper, and
exhaustive in a way a subprocess-per-case suite could never be.

WHAT IS LEFT IS WHAT PERSONA ACTUALLY DECIDES, and it is worth testing precisely because it is
small: how a system prompt is composed from files, what a task is, and the ORDER of the two writes
a comment makes. the store is a fake here ON PURPOSE -- this suite asserts persona's policy, and
the endpoint's own contract is hmux's to keep (`e2e/memory_face_test.py`, `crates/hmux-memory`).

the hook is driven the way the host drives it: argv names the stage, one json object on stdin,
JSONL on stdout.
"""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "persona.py"
PASS = FAIL = 0


def check(desc, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS: {desc}")
    else:
        FAIL += 1
        print(f"FAIL: {desc}")
        if detail:
            print(f"  {detail}")


class FakeMemory:
    """the subset of the memory endpoint persona uses, plus a record of what it was asked.

    DELIBERATELY NOT A REIMPLEMENTATION of the store: it holds what it is told and hands it back.
    what it is really for is the REQUEST LOG -- the order and the shape of what persona sends,
    which is the half no rust test can see.
    """

    def __init__(self, entries=None, docs=None):
        self.entries = entries or []          # [{path, visibility, content}]
        self.docs = docs or {}                # path -> {key: value}
        self.log = []                         # [(method, path, body-or-query)]
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):        # keep the test output readable
                pass

            def _body(self):
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    return json.loads(raw)
                except Exception:  # noqa: BLE001
                    return raw.decode()

            def _send(self, obj, status=200):
                payload = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                rel = u.path[len("/t"):].lstrip("/")
                outer.log.append(("GET", rel, q))
                if not rel:
                    want = q.get("content")
                    out = []
                    for e in outer.entries:
                        row = {k: v for k, v in e.items() if k != "content"}
                        if want and e.get("visibility") == want:
                            row["content"] = e.get("content", "")
                        out.append(row)
                    return self._send({"seq": len(outer.log), "entries": out})
                if rel in outer.docs:
                    doc = outer.docs[rel]
                    key = q.get("key")
                    return self._send(doc.get(key, {}) if key else doc)
                for e in outer.entries:
                    if e.get("path") == rel:
                        return self._send(e.get("content", ""))
                return self._send({"error": f"`{rel}` does not exist"}, 404)

            def do_PUT(self):
                rel = urlparse(self.path).path[len("/t"):].lstrip("/")
                body = self._body()
                outer.log.append(("PUT", rel, body))
                found = next((e for e in outer.entries if e.get("path") == rel), None)
                if found:
                    found["content"] = body
                else:
                    outer.entries.append(
                        {"path": rel, "visibility": "listed", "content": body, "rev": 1})
                self._send({"path": rel, "rev": 1})

            def do_PATCH(self):
                rel = urlparse(self.path).path[len("/t"):].lstrip("/")
                body = self._body()
                outer.log.append(("PATCH", rel, body))
                doc = outer.docs.setdefault(rel, {})
                for path, value in (body.get("$set") or {}).items():
                    head, _, tail = path.partition(".")
                    if tail:
                        doc.setdefault(head, {})[tail] = value
                    else:
                        doc[head] = value
                self._send({"path": rel, "rev": 1})

            def do_POST(self):
                rel = urlparse(self.path).path[len("/t"):].lstrip("/")
                body = self._body()
                outer.log.append(("POST", rel, body))
                self._send({"path": rel, "rev": 1, "duplicate": False})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self, method=None):
        return [c for c in self.log if method is None or c[0] == method]

    def close(self):
        self.server.shutdown()


def call_hook(stage, ctx=None, memory=None):
    """drive one stage the way the host does. returns (merged result, logs)."""
    full = dict(ctx or {})
    host = dict(full.get("host") or {"name": "hmux", "version": 3})
    if memory:
        host["memory"] = memory
    full["host"] = host
    proc = subprocess.run(
        [sys.executable, str(HOOK), stage], input=json.dumps(full),
        capture_output=True, text=True)
    result, logs = {}, []
    for line in proc.stdout.strip().splitlines():
        if not line:
            continue
        obj = json.loads(line)
        if "log" in obj:
            logs.append(obj["log"])
        else:
            result.update(obj)
    return result, logs


def call_tool(name, args, memory):
    result, _ = call_hook("execute_tool", {"tool": name, "args": args}, memory=memory)
    raw = result.get("result", "")
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw


def a_store():
    return FakeMemory(entries=[
        {"path": "prompts/preamble.md", "visibility": "listed", "content": "you are per.\n"},
        {"path": "prompts/chat.md", "visibility": "listed", "content": "be brief.\n"},
        {"path": "prompts/heartbeat.md", "visibility": "listed", "content": "look around.\n"},
        {"path": "traits/SOUL.md", "visibility": "core", "content": "curious"},
        {"path": "traits/USER.md", "visibility": "core", "content": "prefers ascii"},
        {"path": "traits/federation.md", "visibility": "listed", "content": "long prose"},
    ])


def main():
    # --- composition: what a system prompt is made of -----------------------------------------
    mem = a_store()
    try:
        result, _ = call_hook("mutate_request", memory=mem.url)
        system = "".join(result.get("system") or [])
        check("the preamble and the mode's prompt both reach the system prompt",
              "you are per." in system and "be brief." in system, system[:200])
        check("a core trait is inlined, by name and content",
              "{trait:SOUL.md}" in system and "curious" in system, system[:200])
        check("a listed trait is NAMED but not inlined",
              "federation.md" in system and "long prose" not in system, system[:300])
        check("and the prompt says how to read one, in the tool that now serves it",
              "memory_read on traits/<name>" in system, system[-200:])
        # PERSONA OWNS THE SYSTEM PROMPT, so it replaces rather than appends -- an append would
        # leave the backend's own default in front of the soul.
        check("the result REPLACES the backend default", result.get("system_mode") == "replace")
        check("composition costs two round trips, not one per file",
              len(mem.calls("GET")) == 2, str(mem.calls("GET")))
    finally:
        mem.close()

    # --- composition when the store is unreachable ---------------------------------------------
    result, _ = call_hook("mutate_request", memory="http://127.0.0.1:1")
    system = "".join(result.get("system") or [])
    # A SESSION WITH NO SOUL MUST SAY SO. returning nothing would leave the backend default in
    # place, which reads as persona simply not being loaded.
    check("an unreachable store degrades VISIBLY rather than silently",
          "degraded" in system and result.get("system_mode") == "replace", system[:200])

    # --- the heartbeat stage ------------------------------------------------------------------
    mem = a_store()
    try:
        result, _ = call_hook("heartbeat", memory=mem.url)
        check("the heartbeat carries its own prompt as the user turn",
              result.get("user", "").strip() == "look around.", str(result.get("user")))
        check("and composes the soul beside it",
              "curious" in "".join(result.get("system") or []))
    finally:
        mem.close()

    mem = FakeMemory(entries=[])
    try:
        result, _ = call_hook("heartbeat", memory=mem.url)
        check("no heartbeat prompt means no beat, not an empty one", result == {}, str(result))
    finally:
        mem.close()

    # --- tasks: persona's schema over memory's shapes ------------------------------------------
    mem = a_store()
    try:
        got = call_tool("task_create", {"title": "water the plants"}, mem.url)
        patches = mem.calls("PATCH")
        check("creating a task returns its id", isinstance(got, dict) and "id" in got, str(got))
        # ONE `$set` ON THE TASK'S OWN KEY. rewriting the whole document would make two creations
        # racing lose one another, which is what the per-entry shape exists to prevent.
        ops = patches[0][2]["$set"] if patches else {}
        check("and writes ONE key of the tasks document",
              len(patches) == 1 and list(ops) == [got.get("id")], str(patches))
        check("it never reads the other tasks in order to write one",
              not any(c[1].endswith(".tasks.json") for c in mem.calls("GET")),
              str(mem.calls("GET")))

        task_id = got["id"]
        mem.docs["traits/.tasks.json"] = {task_id: {"title": "water the plants", "status": "open"}}
        mem.log.clear()
        call_tool("task_update", {"id": task_id, "status": "blocked"}, mem.url)
        ops = mem.calls("PATCH")[0][2]["$set"]
        # SET ONLY WHAT CHANGED, by dot-path: writing the whole task back would put every field it
        # read back with it, so two edits in flight would each undo the other's.
        check("updating a task sets only the fields that changed",
              set(ops) == {f"{task_id}.status", f"{task_id}.updated"}, str(ops))

        got = call_tool("task_update", {"id": "no-such-task", "status": "open"}, mem.url)
        check("updating a task that does not exist says so",
              isinstance(got, dict) and "not found" in got.get("error", ""), str(got))
    finally:
        mem.close()

    mem = a_store()
    try:
        got = call_tool("task_create", {"title": "weekly", "interval": "P1W"}, mem.url)
        check("a recurrence with no due date is refused",
              isinstance(got, dict) and "interval requires a due date" in got.get("error", ""),
              str(got))
    finally:
        mem.close()

    # --- the ordering that has no transaction to protect it ------------------------------------
    mem = a_store()
    try:
        task_id = "t-1"
        mem.docs["traits/.tasks.json"] = {
            task_id: {"title": "weekly", "status": "open", "interval": "P1W",
                      "due": "2026-01-01T09:00:00.000+00:00"}}
        got = call_tool("task_comment", {"id": task_id, "text": "did it"}, mem.url)
        check("commenting on a recurring task bumps its due date by the interval",
              got.get("due") == "2026-01-08T09:00:00.000+00:00", str(got))
        writes = [c for c in mem.log if c[0] in ("POST", "PATCH")]
        # THE ORDER IS LOAD-BEARING AND THERE IS NO TRANSACTION ACROSS TWO FILES. a crash between
        # them must leave a comment against a stale due date -- visible and self-correcting -- and
        # never a bumped task with no record of why, which is invisible.
        check("the comment is appended BEFORE the due date moves",
              [w[0] for w in writes] == ["POST", "PATCH"], str(writes))
        check("and the comment carries the task it is about",
              writes[0][2]["fields"]["task_id"] == task_id, str(writes[0]))
    finally:
        mem.close()

    # --- prompts round-trip through the endpoint -----------------------------------------------
    mem = a_store()
    try:
        call_tool("prompt_write", {"prompt": "chat.md", "content": "be briefer."}, mem.url)
        check("writing a prompt PUTs it under prompts/",
              mem.calls("PUT") and mem.calls("PUT")[0][1] == "prompts/chat.md",
              str(mem.calls("PUT")))
        got = call_tool("prompt_read", {"prompt": "chat.md"}, mem.url)
        check("and reading it back gives what was written", got == "be briefer.", str(got))
        listed = call_tool("prompt_list", {}, mem.url)
        check("listing names the prompt files", "chat.md" in str(listed), str(listed))
    finally:
        mem.close()

    # --- the tool surface ----------------------------------------------------------------------
    mem = a_store()
    try:
        result, _ = call_hook("discover", memory=mem.url)
        names = {t["name"] for t in result.get("tools", [])}
        check("discover declares persona's remaining tools",
              {"task_create", "task_update", "task_comment", "prompt_read", "datetime"} <= names,
              str(sorted(names)))
        # THE MIGRATION'S REGRESSION GUARD. these moved to the memory face; a copy left behind here
        # would be a second implementation of the thing the move existed to have one of.
        gone = {n for n in names if n.startswith(("trait_", "data_", "record_"))}
        check("and NO storage tool survived the move to the memory face", not gone, str(gone))
        gated = {t["name"] for t in result.get("tools", []) if t.get("permission")}
        check("the mutating task tools still carry their permission arg",
              {"task_update", "task_comment"} <= gated, str(sorted(gated)))
    finally:
        mem.close()

    # --- the clock -----------------------------------------------------------------------------
    got = call_tool("datetime", {}, "http://127.0.0.1:1")
    check("the datetime tool answers in canonical ISO 8601 UTC, with no store involved",
          isinstance(got, str) and got.endswith("+00:00") and got[4] == "-", str(got))

    print(f"\n{PASS + FAIL} tests, {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
