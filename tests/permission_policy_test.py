#!/usr/bin/env python3
"""persona's permission policy, checked against the rule that bit us.

an absolute path arg is judged TWICE -- by `tools.<name>` and by the `external` allow-list, with
the more restrictive winning. so a path named in one list and not the other is refused, which is
how the agent came to be denied the very file it had just been told to read back.

this walks the real config.toml rather than a copy of it, so the two lists cannot drift apart
again without something going red.
"""
import re
import sys
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "hmux" / "config.toml"
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


def matches(pattern, path):
    """the rust matcher's rule: exact, or a trailing-* prefix."""
    if pattern == path:
        return True
    return pattern.endswith("*") and path.startswith(pattern.rstrip("*"))


def allowed_by(rules, path):
    """the most specific matching rule wins: exact > longest prefix > '*'."""
    best, best_spec = None, -1
    for pat, action in rules.items():
        if not matches(pat, path):
            continue
        spec = len(pat) if pat != "*" else 0
        spec = 10**6 if pat == path else spec
        if spec > best_spec:
            best, best_spec = action, spec
    return best


def main():
    cfg = tomllib.loads(CONFIG.read_text())
    perm = cfg["profile"]["persona"]["clients"]
    perm = next(c for c in perm if c.get("kind") == "permission")["permission"]
    external = perm["external"]
    read = perm["tools"]["read"]

    # every ABSOLUTE path the read rule allows must also be allowed by external, or the overlay
    # denies it and the tool rule is a lie.
    for pat, action in read.items():
        if action != "allow" or not pat.startswith("/"):
            continue
        probe = pat.rstrip("*") + "probe"
        check(f"external also allows what tools.read allows: {pat}",
              allowed_by(external, probe) == "allow",
              f"{probe} -> external says {allowed_by(external, probe)!r}")

    # the one that started it: pi truncates long output to a file under its scratch dir and tells
    # the agent to read it back. the path is DERIVED from `scratch_dir` rather than written out
    # here, so moving pi's scratch dir without moving the policy fails this instead of failing a
    # person mid-conversation.
    scratch = (
        cfg["profile"]["persona"].get("backend", {}).get("pi", {}).get("scratch_dir")
    )
    check("pi is given a scratch directory of its own", bool(scratch),
          "without one its files land loose in /tmp and the policy has to admit a name prefix")
    truncated = f"{(scratch or '/tmp').rstrip('/')}/pi-bash-deadbeef.log"
    check("the agent can read back its own truncated bash output",
          allowed_by(read, truncated) == "allow" and allowed_by(external, truncated) == "allow",
          f"tools.read={allowed_by(read, truncated)!r} external={allowed_by(external, truncated)!r}")

    # and allowing it does not open /tmp -- the point of a directory over a name prefix.
    check("allowing pi's scratch files does not open the rest of /tmp",
          allowed_by(external, "/tmp/secrets.txt") != "allow")
    check("nor anything that merely starts with the same characters",
          allowed_by(external, f"{(scratch or '/tmp')}-elsewhere/x") != "allow")

    print(f"\n=== permission policy: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
