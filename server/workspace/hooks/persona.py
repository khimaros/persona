#!/usr/bin/env python3
"""persona hook dispatcher."""

import json, re, sys
from pathlib import Path
from typing import Annotated, TypedDict, get_type_hints

# workspace layout: traits/ for persona files, prompts/ for builtin templates
WORKSPACE = Path(__file__).resolve().parent.parent
TRAITS = WORKSPACE / "traits"
PROMPTS = WORKSPACE / "prompts"
AVATAR = "🌀"
AGENT_MARKER = "<~ PERSONA AGENT MARKER ~>"

class HookResult(TypedDict, total=False):
    system: list[str]
    tools: list[dict]
    user: str
    prompt: str
    message: str
    actions: list[dict]
    result: str
    modified: list[str]
    notify: list[dict]
    error: str

HOOKS, TOOLS = {}, {}

def hook(fn):
    HOOKS[fn.__name__] = fn
    return fn

def tool(fn):
    TOOLS[fn.__name__] = fn
    return fn

# emit a JSONL log line to stdout (picked up by the plugin)
def debug(msg):
    print(json.dumps({"log": f"[{AVATAR}] {msg}"}), flush=True)

# trait visibility: ALLCAPS = inlined in system prompt (letters, digits, _, .),
# lowercase = listed (read on demand), .hidden = unlisted
def is_hidden(name):
    return name.startswith(".")

def is_core(name):
    stem = Path(name).stem
    stripped = re.sub(r"[_.0-9]", "", stem)
    return stem == stem.upper() and len(stripped) > 0 and stripped.isalpha()

def trait_names(include_hidden=False):
    return sorted(
        f.name for f in TRAITS.iterdir()
        if f.is_file() and (include_hidden or not is_hidden(f.name))
    )

def core_trait_names():
    return [n for n in trait_names() if is_core(n)]

def listed_trait_names():
    return [n for n in trait_names() if not is_core(n)]

def trait_path(name):
    """resolve trait name to path, rejecting traversal outside TRAITS/."""
    resolved = (TRAITS / name).resolve()
    if not resolved.parts or not str(resolved).startswith(str(TRAITS.resolve())):
        raise ValueError(f"invalid trait path: {name}")
    return resolved

def prompt_path(name):
    return PROMPTS / f"{name}.md"

def prompt_names():
    return sorted(f.stem for f in PROMPTS.iterdir() if f.is_file() and f.suffix == ".md")

def format_trait(name):
    try:
        content = trait_path(name).read_text()
    except FileNotFoundError:
        content = "(empty)"
    return f"\n{{file:{TRAITS}/{name}}}\n{content}\n"

# compose system prompt from preamble, mode-specific prompt, traits, and env
def system_prompt(mode=None):
    parts = [prompt_path("preamble").read_text()]
    if mode:
        parts.append(prompt_path(mode).read_text())
    parts += [format_trait(t) for t in core_trait_names()]
    listed = listed_trait_names()
    if listed:
        parts.append(f"\nadditional traits (use trait_read to view): {', '.join(listed)}\n")
    return ["".join(parts)]

@tool
def trait_list(
    include_hidden: Annotated[str, "include hidden traits (true/false)"] = "false",
) -> HookResult:
    """list all traits of the persona"""
    show_hidden = str(include_hidden).lower() == "true"
    names = trait_names(include_hidden=show_hidden)
    return {"result": f"{AVATAR} available traits: {', '.join(names)}"}

@tool
def trait_read(
    trait: Annotated[str, "trait filename (e.g. SOUL.md)"],
) -> HookResult:
    """read a trait from the persona"""
    try:
        trait_path(trait)
    except ValueError as e:
        return {"result": f"{AVATAR} invalid trait: {e}"}
    return {"result": f"{AVATAR} {format_trait(trait)}"}

@tool
def trait_write(
    trait: Annotated[str, "trait filename (e.g. SOUL.md)"],
    content: Annotated[str, "full content for the trait"],
) -> HookResult:
    """write a trait to the persona"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": f"{AVATAR} invalid trait: {e}"}
    TRAITS.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return {"result": f"{AVATAR} successfully wrote {trait}", "modified": [trait],
            "notify": [{"type": "trait_changed", "files": [trait]}]}

@tool
def trait_patch(
    trait: Annotated[str, "trait filename (e.g. SOUL.md)"],
    old_string: Annotated[str, "the text to replace"],
    new_string: Annotated[str, "the new text to replace with"],
) -> HookResult:
    """patch a trait in the persona"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": f"{AVATAR} invalid trait: {e}"}
    TRAITS.mkdir(parents=True, exist_ok=True)
    content = path.read_text()
    n = content.count(old_string)
    if n != 1:
        return {"result": f"{AVATAR} failed: {'not found' if n == 0 else f'{n} matches'}"}
    path.write_text(content.replace(old_string, new_string, 1))
    return {"result": f"{AVATAR} successfully patched {trait}", "modified": [trait],
            "notify": [{"type": "trait_changed", "files": [trait]}]}

@tool
def trait_delete(
    trait: Annotated[str, "trait filename (e.g. SOUL.md)"],
) -> HookResult:
    """delete a trait from the persona"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": f"{AVATAR} invalid trait: {e}"}
    if not path.exists():
        return {"result": f"{AVATAR} not found: {trait}"}
    path.unlink()
    return {"result": f"{AVATAR} successfully deleted {trait}", "modified": [trait],
            "notify": [{"type": "trait_changed", "files": [trait]}]}

@tool
def trait_move(
    old_trait: Annotated[str, "current trait filename"],
    new_trait: Annotated[str, "new trait filename"],
) -> HookResult:
    """rename or move a trait in the persona"""
    try:
        src = trait_path(old_trait)
        dst = trait_path(new_trait)
    except ValueError as e:
        return {"result": f"{AVATAR} invalid trait: {e}"}
    if not src.exists():
        return {"result": f"{AVATAR} not found: {old_trait}"}
    if dst.exists():
        return {"result": f"{AVATAR} already exists: {new_trait}"}
    src.rename(dst)
    return {"result": f"{AVATAR} moved {old_trait} -> {new_trait}", "modified": [old_trait, new_trait],
            "notify": [{"type": "trait_changed", "files": [old_trait, new_trait]}]}

@tool
def tool_discover() -> HookResult:
    """discover all available persona tools (including dynamically added ones)"""
    defs = tool_defs()
    lines = []
    for d in defs:
        params = ", ".join(f"{k}: {v}" for k, v in d["parameters"].items())
        lines.append(f"  {d['name']}({params}): {d['description']}")
    return {"result": f"{AVATAR} available tools:\n" + "\n".join(lines)}

@tool
def tool_invoke(
    name: Annotated[str, "tool name to invoke"],
    args: Annotated[str, "JSON-encoded arguments object"] = "{}",
) -> HookResult:
    """invoke a persona tool dynamically by name"""
    handler = TOOLS.get(name)
    if not handler:
        return {"result": f"{AVATAR} unknown tool: {name}"}
    try:
        parsed = json.loads(args)
    except json.JSONDecodeError as e:
        return {"result": f"{AVATAR} invalid args JSON: {e}"}
    return handler(**parsed)

# generate tool definitions from @tool-decorated functions via Annotated metadata
def tool_defs():
    defs = []
    for name, fn in TOOLS.items():
        hints = get_type_hints(fn, include_extras=True)
        params = {
            p: h.__metadata__[0]
            for p, h in hints.items()
            if p != "return" and hasattr(h, "__metadata__")
        }
        defs.append(
            {"name": name, "description": fn.__doc__ or "", "parameters": params}
        )
    return defs

@hook
def discover(ctx: dict) -> HookResult:
    names = [t["name"] for t in tool_defs()]
    debug(f"tools: {', '.join(names)}")
    return {"tools": tool_defs()}

@hook
def mutate_request(ctx: dict) -> HookResult:
    system = ctx.get("system")
    if system is not None and not any(AGENT_MARKER in s for s in system):
        debug("no agent marker, skipping")
        return {}
    debug(f"core: {', '.join(core_trait_names())}, listed: {', '.join(listed_trait_names())}")
    return {"system": system_prompt("chat")}

@hook
def format_notification(ctx: dict) -> HookResult:
    notifications = ctx.get("notifications", [])
    changed = set()
    for n in notifications:
        if n.get("type") == "trait_changed":
            changed.update(n.get("files", []))
    if not changed:
        return {}
    return {"message": f"[trait-update] updated: {', '.join(sorted(changed))}. re-read if needed."}

@hook
def observe_message(ctx: dict) -> HookResult:
    session = ctx.get("session", {})
    debug(f"session={session.get('id', '?')} agent={session.get('agent', '?')}")
    return {}

@hook
def idle(ctx: dict) -> HookResult:
    session = ctx.get("session", {})
    answer = ctx.get("answer", "")
    debug(f"session={session.get('id', '?')} answer_len={len(answer)}")
    return {}

@hook
def heartbeat(ctx: dict) -> HookResult:
    debug(f"core: {', '.join(core_trait_names())}, listed: {', '.join(listed_trait_names())}")
    try:
        user = prompt_path("heartbeat").read_text()
        debug(f"heartbeat prompt len={len(user)}")
        if not user.strip():
            debug("heartbeat prompt is empty, skipping")
            return {}
        return {"system": system_prompt("heartbeat"), "user": user}
    except FileNotFoundError:
        debug("heartbeat.md not found, skipping")
        return {}

@hook
def recover(ctx: dict) -> HookResult:
    debug(f"recovering from {ctx.get('failed_hook', '?')}: {ctx.get('error', '?')}")
    try:
        return {"system": system_prompt("recover"), "user": prompt_path("recover").read_text()}
    except Exception as e:
        debug(f"recover prompts unavailable: {e}")
        return {"system": ["system recovery — prompts unavailable"]}

@hook
def tool_before(ctx: dict) -> HookResult:
    return {}

@hook
def tool_after(ctx: dict) -> HookResult:
    return {}

@hook
def compacting(ctx: dict) -> HookResult:
    debug(f"core: {', '.join(core_trait_names())}, listed: {', '.join(listed_trait_names())}")
    try:
        return {"prompt": prompt_path("compaction").read_text()}
    except FileNotFoundError:
        debug("compaction.md not found, skipping")
        return {}

# dispatch to @tool-registered handler by name
@hook
def execute_tool(ctx: dict) -> HookResult:
    name = ctx.get("tool", "")
    handler = TOOLS.get(name)
    if not handler:
        debug(f"unknown tool: {name}")
        return {"result": f"{AVATAR} unknown tool: {name}"}
    args = ctx.get("args", {})
    debug(f"tool={name} args={list(args.keys())}")
    try:
        result = handler(**args)
        debug(f"tool={name} result keys={list(result.keys())}")
        return result
    except Exception as e:
        debug(f"tool={name} error: {e}")
        return {"result": f"{AVATAR} tool error: {e}"}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: persona <hook_name>"}))
        sys.exit(1)
    h = HOOKS.get(sys.argv[1])
    if not h:
        print(json.dumps({"error": f"unknown hook: {sys.argv[1]}"}))
        sys.exit(1)
    try:
        ctx = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        ctx = {}
    try:
        result = h(ctx)
    except Exception as e:
        debug(f"{sys.argv[1]}: {e}")
        result = {"error": str(e)}
    for key, value in result.items():
        print(json.dumps({key: value}), flush=True)
