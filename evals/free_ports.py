#!/usr/bin/env python3
"""print N distinct free tcp host ports (default 4), space-separated.

the eval publishes the container's hub port to a random free host port so a run never collides
with -- or has to shut down -- the developer's own `persona` container (which holds 4280). the
faces are not published at all; they are reached through the hub. binding N sockets to port 0 at
once yields N distinct ephemeral ports; we print them and close, letting compose rebind them right
after (the tiny TOCTOU window is acceptable for a dev-only eval).
"""
import socket, sys

n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
socks = []
for _ in range(n):
    s = socket.socket()
    s.bind(("", 0))
    socks.append(s)
print(" ".join(str(s.getsockname()[1]) for s in socks))
for s in socks:
    s.close()
