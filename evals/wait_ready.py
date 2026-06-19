#!/usr/bin/env python3
"""exit 0 once the hub has an online backend that can create a session.

used by the Makefile eval target to wait out the container's cold-start (the pi
backend installs its extension deps on the first session create). run from evals/.
"""
import sys

from hub import HubClient

url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:4280/ws"
try:
    c = HubClient(url)
    online = any(h.get("online") for h in c.request("harness.list"))
    sid = c.request("session.create", {"harness_id": None, "opts": {}})["id"] if online else None
    c.close()
    sys.exit(0 if sid else 1)
except Exception:
    sys.exit(1)
