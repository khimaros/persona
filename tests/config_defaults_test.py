#!/usr/bin/env python3
"""the SHIPPED defaults in hmux/config.toml, checked against what a deployment may not inherit.

a deployment copies this config and edits it. every edit it has to make is a place the two can
drift, so the shipped file should already be what a deployment wants -- except for the settings
that would be UNSAFE to ship on, which have to stay off here and be turned on per-deployment
(via the file or HMUX_CFG_*). these assertions name both halves.
"""
import sys
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "hmux" / "config.toml"
# named exactly, not matched as substrings: the bridge's own `cleanup_tokens` is a context budget.
CREDENTIAL_KEYS = {"access_token", "password", "homeserver", "user_id"}
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


def main():
    cfg = tomllib.loads(CONFIG.read_text())
    profile = cfg["profile"]["persona"]
    clients = profile["clients"]
    kinds = {c["kind"] for c in clients}

    # the bridge idles without HMUX_BRIDGE_* rather than failing, so shipping it declared costs a
    # deployment nothing and saves it an edit. it was commented out, and every bridge deployment
    # had to rediscover that.
    check("the bridge face is declared by default", "bridge" in kinds,
          "a deployment that wants matrix should only have to set HMUX_BRIDGE_* in persona.env")

    bridge = next((c for c in clients if c["kind"] == "bridge"), {}).get("bridge", {})
    secrets = sorted(CREDENTIAL_KEYS & set(bridge))
    check("the bridge carries no credentials in the config file", not secrets,
          f"{secrets} belong in persona.env -- this file is committed")

    # persona takes every configured ingress at its word, so that a persona can tell the people it
    # talks to apart. the setting is stated EXPLICITLY rather than left to hmux's default (which is
    # the opposite), because it is only sound behind a front door that authenticates and strips --
    # a choice that has to be visible in the file to be reconsidered.
    identity = profile.get("identity") or {}
    check("the identity ingress policy is stated, not inherited", "trust_headers" in identity,
          "hmux defaults it OFF; persona's answer should be readable here either way")
    check("configured identity ingress is trusted", identity.get("trust_headers") is True,
          "a persona that cannot tell its people apart addresses all of them as one")

    # `declare_allowed` lets a peer name the speaker per prompt. that is right for a face that
    # authenticated the human itself (the bridge), and wrong for one that did not -- a browser ui
    # carries one identity per connection and has no business naming someone else.
    declare = identity.get("declare_allowed")
    check("only self-authenticating faces may declare a speaker",
          declare is None or set(declare) <= {"bridge"},
          f"{declare} -- hmux defaults to ['bridge']; widening it needs its own reason")

    # every exposed mount must name a face that exists, or the hub proxies a path to nothing.
    exposed = profile.get("expose", {})
    check("something is exposed on the hub port", bool(exposed),
          "without this, every face needs its own published host port")
    for kind, mount in exposed.items():
        check(f"exposed `{kind}` names a declared client", kind in kinds,
              f"{mount} would proxy to a face that is never started")
        check(f"exposed `{kind}` mount is not the hub-reserved /ws", mount.rstrip("/") != "/ws")

    print(f"\n=== config defaults: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
