#!/usr/bin/env python3
"""persona's permission policy, checked against the rule that bit us.

an absolute path arg is judged TWICE -- by `tools.<name>` and by the `external` allow-list, with
the more restrictive winning. so a path named in one list and not the other is refused, which is
how the agent came to be denied the very file it had just been told to read back.

this walks the real config.toml rather than a copy of it, so the two lists cannot drift apart
again without something going red.
"""
import importlib.util
import re
import sys
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "hmux" / "config.toml"
HOOK = Path(__file__).resolve().parents[1] / "workspace" / "hooks" / "persona.py"
PASS = FAIL = 0


def load_hook_tools():
    """what persona.py actually registers, read from the hook rather than restated here."""
    spec = importlib.util.spec_from_file_location("persona_hook", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tool_defs()


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


def action_for(rules, tool):
    """what a policy does with `tool` by default.

    A RULE IS EITHER AN ACTION OR A TABLE KEYED BY THE TOOL'S ARGUMENT, and the table's `"*"` is
    what a call with an unremarkable argument gets. Reading only the first shape is how a check can
    report "not allowed" about a tool that is perfectly allowed.
    """
    got = allowed_by(rules, tool)
    return got.get("*") if isinstance(got, dict) else got


def main():
    cfg = tomllib.loads(CONFIG.read_text())
    spokes = cfg["profile"]["persona"]["spokes"]
    perm = next(c for c in spokes if c.get("kind") == "permission")["permission"]
    external = perm["external"]
    tools = perm["tools"]
    read = tools["read"]

    # --- phase 82: which door the model has to its own memory --------------------------------
    #
    # A RULE THAT DOES NOT MATCH FAILS OPEN, which is why this is checked against the real file
    # rather than described in a comment: the gate is simply absent and looks exactly like a policy
    # nobody wrote. Every one of these names was renamed by phase 82.
    kinds = {s.get("kind") for s in spokes}
    check("the traits face is running, since it is the model's only door to its memory",
          "traits" in kinds, str(sorted(k for k in kinds if k)))
    check("and the workspace face under it, which owns the bytes and the git history",
          "workspace" in kinds, str(sorted(k for k in kinds if k)))
    check("the wide workspace tools are DISABLED, not merely denied",
          action_for(tools, "workspace_read") == "disable",
          f"workspace_read -> {action_for(tools, 'workspace_read')!r}")
    for name in ("trait_read", "trait_list", "trait_append", "trait_write"):
        check(f"and the trait tools are reachable: {name}",
              action_for(tools, name) == "allow", f"{name} -> {action_for(tools, name)!r}")
    # AND THE QUERY ENGINE TOO, which is not decoration: `persona_task_*` creates and amends tasks
    # but does not LIST or DELETE them, and the journal has no wrapper at all. Disabling these would
    # leave the agent unable to read its own task list -- silently, because a disabled tool is not
    # offered and so is never refused out loud.
    for needed in ("trait_data_query", "trait_data_update", "trait_record_query"):
        check(f"and the query engine, which tasks and the journal need: {needed}",
              action_for(tools, needed) == "allow",
              f"{needed} -> {action_for(tools, needed)!r}")
    # THE PER-TRAIT RULES ARE KEYED ON THE TOOL NAME, and a table for a tool that does not exist is
    # the failure this whole check exists for: silent, open, and indistinguishable from no policy.
    for gated in ("trait_write", "trait_edit", "trait_delete"):
        check(f"{gated} asks before touching SOUL.md",
              tools.get(gated, {}).get("SOUL.md") == "ask", str(tools.get(gated)))
    check("and append stays open on it, because it cannot destroy what is there",
          "SOUL.md" not in tools.get("trait_append", {}), str(tools.get("trait_append")))

    # AND NO RULE MAY NAME A `persona_*` TOOL THE HOOK DOES NOT REGISTER. the block above checks
    # the rules we knew to look for; this checks the CLASS, against the hook itself rather than
    # against a list kept by hand. a stale name here is not merely dead config -- an `ask` that
    # matches nothing is an `ask` nobody is ever shown, which is the same silence as no policy.
    #
    # ONLY THE `persona_*` FAMILY, because it is the only one this repo owns: `trait_*`, `hcp_*`
    # and the rest are hmux's and cannot be enumerated without booting a stack. a glob is skipped
    # since it is a family rule and names no single tool.
    registered = {f"persona_{d['name']}" for d in load_hook_tools()}
    named = [k for k in tools if k.startswith("persona_") and "*" not in k]
    for rule in named:
        check(f"the policy rule for {rule} names a tool that exists",
              rule in registered, f"{rule} is not registered; persona.py serves {sorted(registered)}")

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
        cfg["profile"]["persona"].get("harness", {}).get("pi", {}).get("scratch_dir")
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
