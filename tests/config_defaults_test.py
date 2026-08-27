#!/usr/bin/env python3
"""the SHIPPED defaults in hmux/config.toml, checked against what a deployment may not inherit.

a deployment copies this config and edits it. every edit it has to make is a place the two can
drift, so the shipped file should already be what a deployment wants -- except for the settings
that would be UNSAFE to ship on, which have to stay off here and be turned on per-deployment
(via the file or HMUX_CFG_*). these assertions name both halves.
"""
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "hmux" / "config.toml"
# named exactly, not matched as substrings: the bridge's own `cleanup_tokens` is a context budget.
CREDENTIAL_KEYS = {"access_token", "password", "homeserver", "user_id"}

# what each `tools` setting on the federation face exposes to the model. WRITTEN OUT RATHER THAN
# READ FROM WHAT THE CONFIG HAPPENS TO LIST, because the failure this catches is a tool that is
# exposed and NOT mentioned in the policy -- a check that iterated the policy could never see one.
#
# the two settings are different privileges: talking to a peer somebody already chose, versus
# CHOOSING one. that is why `manage` is not just "more tools".
FEDERATION_TOOLS = {
    "off": set(),
    "ask": {"federation_peers", "federation_ask", "federation_task"},
    "manage": {"federation_peers", "federation_ask", "federation_task",
               "federation_invite", "federation_pair", "federation_verify",
               "federation_unpair"},
}
# admitting a peer is not something to hand over on a wildcard: these three change WHO can reach
# this agent, so each needs its own line in the policy and none may be `allow`.
FEDERATION_ADMISSION = {"federation_invite", "federation_pair", "federation_unpair"}
# NOT admission: `federation_verify` changes nothing about who may reach us -- under
# `verify = "sas"` it is how a peer already paired stops being refused, and under `tofu`
# it only records that somebody looked. it still asks, because the thing it records is a
# human having compared a string, and the model cannot do that.
# where hmux declares the same mapping. checked against ours when the checkout is beside us, so
# this cannot rot into a stale private copy the way a hardcoded list otherwise would.
HMUX_TOOLS_RS = (Path(__file__).resolve().parents[2] / "hmux" / "crates" / "hmux-federation"
                 / "src" / "lib.rs")
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
    faces = profile["faces"]
    kinds = {f["kind"] for f in faces}

    # the bridge idles without HMUX_BRIDGE_* rather than failing, so shipping it declared costs a
    # deployment nothing and saves it an edit. it was commented out, and every bridge deployment
    # had to rediscover that.
    check("the bridge face is declared by default", "bridge" in kinds,
          "a deployment that wants matrix should only have to set HMUX_BRIDGE_* in persona.env")

    bridge = next((f for f in faces if f["kind"] == "bridge"), {}).get("bridge", {})
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

    # `single_user` is deliberately OFF here, so a person at a ui is `local:anonymous` rather than
    # the login name the hub runs as. it is left commented in the config rather than set, and this
    # asserts nothing about it: the choice is the operator's and hmux's default already matches.

    # `declare_allowed` lets a peer name the speaker per prompt. that is right for a face that
    # authenticated the human itself (the bridge), and wrong for one that did not -- a browser ui
    # carries one identity per connection and has no business naming someone else.
    declare = identity.get("declare_allowed")
    check("only self-authenticating faces may declare a speaker",
          declare is None or set(declare) <= {"bridge"},
          f"{declare} -- hmux defaults to ['bridge']; widening it needs its own reason")

    # EVERY FEDERATION TOOL THE MODEL IS GIVEN MUST BE NAMED IN THE POLICY. the permission face
    # defaults to deny, so a tool that is exposed and unlisted is not "open" -- it is a tool the
    # model can see, will try, and can never use, which reads to the agent as a broken capability
    # rather than a refused one. the two settings live in different files and neither validates the
    # other, so this is the only place the pair is checked.
    federation = next((f for f in faces if f["kind"] == "federation"), None)
    if federation is not None:
        setting = (federation.get("federation") or {}).get("tools", "ask")
        policy = profile["faces"]
        perms = next((f for f in policy if f["kind"] == "permission"), {})
        rules = ((perms.get("permission") or {}).get("tools")) or {}
        check(f"the federation `tools` setting is one hmux knows: {setting!r}",
              setting in FEDERATION_TOOLS,
              f"expected one of {sorted(FEDERATION_TOOLS)}")
        for tool in sorted(FEDERATION_TOOLS.get(setting, set())):
            check(f"`{tool}` is exposed and the policy names it", tool in rules,
                  f"tools = {setting!r} gives the model {tool}, and the policy defaults to "
                  f"{(perms.get('permission') or {}).get('default')!r} -- it would always fail")
        # admitting a peer stays a human's decision. `ask` or `deny`, never `allow`.
        for tool in sorted(FEDERATION_ADMISSION & set(rules)):
            check(f"`{tool}` does not admit peers without a human", rules[tool] != "allow",
                  "this changes who can reach this agent; an invite string can arrive from a peer "
                  "or a web page, so the model must not act on one unsupervised")
        # and our copy of hmux's mapping is really hmux's -- BOTH WAYS.
        #
        # THIS USED TO CHECK ONE DIRECTION and that is the direction that cannot fail usefully.
        # Asking "does hmux still declare everything on our list" catches a tool being REMOVED,
        # which breaks nothing here. The failure that matters is a tool being ADDED: hmux exposes
        # it, this policy has never heard of it, and `default = "deny"` means the model is offered
        # a capability it can see, will try, and can never use. `federation_verify` was added and
        # every check here stayed green.
        if HMUX_TOOLS_RS.exists():
            src = HMUX_TOOLS_RS.read_text()
            for tool in sorted(FEDERATION_TOOLS["manage"]):
                check(f"hmux still declares `{tool}`", f'"{tool}"' in src,
                      f"{HMUX_TOOLS_RS} no longer mentions it; this list has gone stale")
            # the `manage` arm of `Tools::names()`, which is the widest set and so a superset of
            # every other arm -- read from the source rather than from our copy of it.
            arm = re.search(r"Tools::Manage\s*=>\s*&\[(.*?)\]", src, re.S)
            check("hmux's `manage` tool list is still readable from source", arm is not None,
                  f"{HMUX_TOOLS_RS} changed shape; this check now verifies nothing")
            if arm:
                declared = set(re.findall(r'"(federation_\w+)"', arm.group(1)))
                check("this list knows every federation tool hmux exposes",
                      declared <= FEDERATION_TOOLS["manage"],
                      f"hmux exposes {sorted(declared - FEDERATION_TOOLS['manage'])} and this file "
                      f"has never heard of them, so nothing checks the policy names them")
        else:
            print(f"SKIP: no hmux checkout at {HMUX_TOOLS_RS}; tool list unverified against source")

    # every exposed mount must name a face that exists, or the hub proxies a path to nothing.
    exposed = profile.get("expose", {})
    check("something is exposed on the hub port", bool(exposed),
          "without this, every face needs its own published host port")
    for kind, mount in exposed.items():
        check(f"exposed `{kind}` names a declared client", kind in kinds,
              f"{mount} would proxy to a face that is never started")
        check(f"exposed `{kind}` mount is not the hub-reserved /ws", mount.rstrip("/") != "/ws")

    # EVERY DECLARED KIND MUST BE ONE THE BINARY KNOWS, and this is not a style check: `kind` is a
    # plain string that `hmux up` resolves at launch, so a face this image does not carry does not
    # degrade -- it fails `up`, and the whole stack (pi, every other face) never starts. that is the
    # failure mode that kept the face-exposure config staged COMMENTED for a release: a config
    # written for a newer hmux takes down an older one at boot with no partial service.
    #
    # asked of the BINARY rather than a list kept here, so this cannot rot into its own stale copy
    # of hmux's face kinds.
    hmux_bin = shutil.which("hmux") or (
        Path(__file__).resolve().parents[2] / "hmux" / "target" / "debug" / "hmux"
    )
    if Path(hmux_bin).exists():
        for kind in sorted(kinds):
            known = subprocess.run(
                [str(hmux_bin), "face", kind, "--help"],
                capture_output=True,
            )
            check(f"`{kind}` is a face kind this hmux carries", known.returncode == 0,
                  f"`hmux up` would refuse it at boot and start nothing at all")
    else:
        print(f"SKIP: no hmux binary at {hmux_bin}; cannot check declared kinds")

    print(f"\n=== config defaults: {PASS} passed, {FAIL} failed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
