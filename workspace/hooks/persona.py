#!/usr/bin/env python3
"""persona hook dispatcher.

WHAT THIS FILE STOPPED BEING (hmux phase 74). it used to be a storage engine: seven trait tools,
three document tools, three record tools, a mongo-style filter evaluator and a dot-path updater --
about seventy per cent of it. all of that moved into hmux's `memory` face, which serves the same
files to the model as `memory_*` tools and to everything else over http. what is left is POLICY:
what `SOUL.md` MEANS, how a system prompt is composed from it, and what a task is.

AND IT TOUCHES NO FILE DIRECTLY, not even to read one. that was argued the other way first --
whole-file reads have no logic to duplicate and cannot fail -- and what settled it is that the
memory root is going to be REORGANISED (`/work/memory/traits/`, `/work/memory/touch/`), and a hook
that globs `WORKSPACE/traits` breaks the day it moves. reading through the endpoint makes the layout
memory's business and nobody else's. it also deletes the second copy of the visibility rule:
ALLCAPS-inlines / lowercase-is-listed / .dotted-is-hidden is memory's now, and a listing reports it.

the endpoint arrives in each stage's payload as `host.memory`, beside the `cwd` that was always
there.
"""

import json, os, re, sys, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, TypedDict, get_type_hints

# where the store is, learned from each hook's payload (`host.memory`). the env var is the manual
# fallback: running a stage by hand, and the contract test, which has no host to be told by.
MEMORY = os.environ.get("HMUX_MEMORY", "")
# how long to wait on it. WELL INSIDE the host's 30s hook cap, so a wedged memory face costs a
# stage a moment and reports a sentence, rather than the whole budget and a timeout that reads
# like a hang.
MEMORY_TIMEOUT = 5

# the two directories persona knows by name. everything else about the layout is memory's.
TRAITS = "traits"
PROMPTS = "prompts"
AVATAR = "🌀"
ISO_DT_DESC = "ISO 8601 datetime with timezone offset (e.g. 2026-04-01T09:00:00.000+00:00)"
ISO_DUR_DESC = "ISO 8601 duration (e.g. P1D, P1W, P1M, P1Y, PT1H, PT30M)"
TASKS_TRAIT = f"{TRAITS}/.tasks.json"
TASK_COMMENTS_TRAIT = f"{TRAITS}/.tasks_comments.jsonl"
TASK_STATUSES = ["open", "in_progress", "blocked", "closed", "wontfix"]

class HookResult(TypedDict, total=False):
    system: list[str]
    system_mode: str  # "replace" | "append" (host default); persona replaces
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

# parameter spec: dict metadata = typed param, bare string = string type (backwards compat)
def param(description, type="string", optional=False, enum=None):
    spec = {"type": type, "description": description, "optional": optional}
    if enum:
        spec["enum"] = list(enum)
    return spec

def hook(fn):
    HOOKS[fn.__name__] = fn
    return fn

def tool(fn=None, *, permission=None, name=None):
    def decorator(f):
        if permission:
            f._permission = permission
        # `name` overrides the tool's short name when the function name is unavailable
        # (e.g. `datetime` would shadow the imported class), yielding persona_<name>.
        TOOLS[name or f.__name__] = f
        return f
    if fn is not None:
        return decorator(fn)
    return decorator

# emit a JSONL log line to stdout (picked up by the host)
def debug(msg):
    print(json.dumps({"log": f"[{AVATAR}] {msg}"}), flush=True)

def result_ok(extra=None):
    """structured success response."""
    r = {"success": True}
    if extra:
        r.update(extra)
    return json.dumps(r)

def result_err(msg):
    """structured error response."""
    return json.dumps({"error": msg})

# --- the memory endpoint ---

class MemoryError(Exception):
    """the store could not answer. carries a sentence a model can act on."""

def _mem(method, path="", body=None, **query):
    """one request to the memory face.

    RAISES RATHER THAN RETURNING A SENTINEL, so a caller cannot mistake "the store is down" for
    "the file is empty" -- which is the difference between an error worth retrying and a system
    prompt composed from nothing.
    """
    if not MEMORY:
        raise MemoryError("no memory face is configured for this deployment")
    url = f"{MEMORY.rstrip('/')}/t"
    if path:
        url += "/" + path.lstrip("/")
    if query:
        clean = {k: v for k, v in query.items() if v not in (None, "")}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    data = None
    if body is not None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        ctype = "application/octet-stream" if isinstance(body, str) else "application/json"
        req.add_header("content-type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=MEMORY_TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", "")
        except Exception:  # noqa: BLE001
            pass
        raise MemoryError(detail or f"memory: http {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise MemoryError(f"memory: {e}") from e
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.decode("utf-8", "replace")

def _index(content=None):
    """every path memory holds, with its visibility and revision.

    ONE ROUND TRIP FOR A WHOLE SYSTEM PROMPT when `content=core`: the bytes of the shouting files
    come back inline and everything else is named. that endpoint parameter exists for this caller.
    """
    got = _mem("GET", "", content=content) or {}
    return got.get("entries", [])

# --- prompt + trait composition (persona's actual job) ---

def _named(entries, prefix, suffix=""):
    """the entries under `prefix/`, as {short name: entry}."""
    out = {}
    for e in entries:
        p = e.get("path", "")
        if p.startswith(f"{prefix}/") and p.endswith(suffix):
            out[p[len(prefix) + 1 : len(p) - len(suffix) if suffix else len(p)]] = e
    return out

def system_prompt(mode=None):
    """compose the system prompt: preamble, the mode's own prompt, then every core trait.

    THE ORDER AND THE BYTES MUST NOT WANDER between runs -- the host freezes this per session for
    the provider's prompt cache, and a set that reordered itself would invalidate it every time.
    memory lists in sorted order, so this does too by construction rather than by sorting again.
    """
    core_index = _index(content="core")
    # the prompt files are `listed`, not `core`, so their bytes are not in the first answer. one
    # more call brings all of them at once rather than one call per file.
    prompts = {n: (e.get("content") or "") for n, e in
               _named(_index(content="listed"), PROMPTS, ".md").items()}
    parts = [prompts.get("preamble", "")]
    if mode:
        parts.append(prompts.get(mode, ""))
    core, listed = [], []
    for name, e in _named(core_index, TRAITS).items():
        if e.get("visibility") == "core":
            core.append(f"\n{{trait:{name}}}\n{e.get('content') or '(empty)'}\n")
        elif e.get("visibility") == "listed":
            listed.append(name)
    parts += core
    if listed:
        parts.append(
            f"\nadditional traits (use memory_read on {TRAITS}/<name> to view): "
            f"{', '.join(sorted(listed))}\n"
        )
    return ["".join(p for p in parts if p)]

def prompt_text(name):
    """one prompt file's body, or "" when it is absent -- a stage with no prompt is skipped."""
    try:
        got = _mem("GET", f"{PROMPTS}/{name}.md")
    except MemoryError:
        return ""
    return got if isinstance(got, str) else ""

# persona OWNS the system prompt (the traits ARE it), so every system-bearing hook result
# REPLACES the backend default instead of appending to it. the composed value is still frozen
# per session host-side, so replacing does not invalidate the provider prompt cache.
REPLACE_SYSTEM = {"system_mode": "replace"}

# --- shared helpers the task tools still need ---

def _coerce_json(value, expected_type):
    """parse string-encoded JSON when the model sends a string instead of an object.

    KEPT THOUGH THE STORAGE TOOLS WENT: models stringify their objects, and answering "fields must
    be an object" to a well-formed object-in-a-string spends a turn on punctuation.
    """
    if isinstance(value, str):
        cleaned = re.sub(r'<\|"\|>', '"', value)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, expected_type):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return value

def _normalize_field_value(v):
    """a scalar the model may have wrapped, unwrapped once."""
    if isinstance(v, dict) and len(v) == 1 and "value" in v:
        return v["value"]
    return v

def _normalize_fields(fields):
    return {k: _normalize_field_value(v) for k, v in fields.items()}

def format_iso(dt):
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}+00:00"

def now_iso():
    return format_iso(datetime.now(timezone.utc))

def validate_iso_datetime(value):
    """validate and parse an ISO 8601 datetime with timezone. raises ValueError if invalid."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"missing timezone: {value}")
    return dt

# parse ISO 8601 duration (P[nY][nM][nW][nD][T[nH][nM][nS]])
ISO_DURATION_RE = re.compile(
    r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
    r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$"
)

def parse_iso_duration(value):
    """parse an ISO 8601 duration string into a timedelta. raises ValueError if invalid."""
    m = ISO_DURATION_RE.match(value)
    if not m or value == "P":
        raise ValueError(f"invalid ISO 8601 duration: {value}")
    years, months, weeks, days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    # approximate years/months as days since timedelta doesn't support them
    total_days = years * 365 + months * 30 + weeks * 7 + days
    return timedelta(days=total_days, hours=hours, minutes=minutes, seconds=seconds)

@tool(name="datetime")
def _datetime() -> HookResult:
    """the current date and time in canonical ISO 8601 UTC (e.g. 2026-04-01T09:00:00.000+00:00); use it to set task due dates, timestamp records, or reason about "now\""""
    return {"result": now_iso()}

# --- tasks: persona's schema over memory's shapes ---
#
# THE LAYERING IS THE POINT. memory provides addressability (`$set` on one key of a document, an
# append to a log); persona provides MEANING -- what `due` is, which statuses exist, and that a
# comment on a recurring task moves its due date. memory must not know any of that, which is why
# the ISO duration arithmetic stayed here when everything around it left.

def _task(task_id):
    """one task, or None. a dot-path read, so finding one does not fetch all the others."""
    got = _mem("GET", TASKS_TRAIT, key=task_id)
    return got if isinstance(got, dict) and got else None

@tool
def task_create(
    title: Annotated[str, "task title"],
    description: Annotated[str, param("detailed task description", optional=True)] = "",
    status: Annotated[str, param(f"task status (default: open). one of: {', '.join(TASK_STATUSES)}", optional=True, enum=TASK_STATUSES)] = "open",
    due: Annotated[str, param(f"due date as {ISO_DT_DESC}", optional=True)] = "",
    interval: Annotated[str, param(f"recurrence as {ISO_DUR_DESC}. recurring tasks are updated via persona_task_comment, which auto-bumps due by this interval", optional=True)] = "",
    fields: Annotated[object, param("arbitrary extra fields to set, e.g. {\"owner\": \"tom\"}", type="object", optional=True)] = None,
) -> HookResult:
    """create a new task. set interval for recurring tasks. use persona_task_comment to log updates on recurring tasks"""
    try:
        if due:
            validate_iso_datetime(due)
        if interval:
            parse_iso_duration(interval)
            if not due:
                return {"result": result_err("interval requires a due date")}
        task_id = str(uuid.uuid4())
        now = now_iso()
        task = {"title": title, "status": status, "created": now, "updated": now}
        fields = _coerce_json(fields, dict)
        if isinstance(fields, dict):
            task.update(_normalize_fields(fields))
        if description:
            task["description"] = description
        if due:
            task["due"] = due
        if interval:
            task["interval"] = interval
        # ONE `$set` ON THE TASK'S OWN KEY, so creating a task neither reads nor rewrites the
        # others -- and two creations racing cannot lose one another.
        _mem("PATCH", TASKS_TRAIT, {"$set": {task_id: task}})
        return {"result": result_ok({"id": task_id})}
    except (ValueError, MemoryError) as e:
        return {"result": result_err(str(e))}

@tool(permission={"arg": "id"})
def task_update(
    id: Annotated[str, "task UUID"],
    title: Annotated[str, param("new title", optional=True)] = "",
    description: Annotated[str, param("new description", optional=True)] = "",
    status: Annotated[str, param(f"new status. one of: {', '.join(TASK_STATUSES)}", optional=True, enum=TASK_STATUSES)] = "",
    due: Annotated[str, param(f"new due date as {ISO_DT_DESC}. for recurring tasks, prefer persona_task_comment to auto-bump due by interval", optional=True)] = "",
    interval: Annotated[str, param(f"recurrence as {ISO_DUR_DESC}. requires due", optional=True)] = "",
    fields: Annotated[object, param("arbitrary extra fields to set, e.g. {\"owner\": \"tom\", \"cc\": \"alice\"}", type="object", optional=True)] = None,
) -> HookResult:
    """update task metadata (title, description, fields). for recurring tasks, use persona_task_comment to log progress - it auto-bumps due by interval"""
    try:
        task = _task(id)
        if not task:
            return {"result": result_err(f"not found: {id}")}
        # SET ONLY WHAT CHANGED, by dot-path. writing the whole task back would put every field it
        # read back with it, so two edits in flight would each undo the other's.
        ops = {}
        fields = _coerce_json(fields, dict)
        if isinstance(fields, dict):
            for k, v in _normalize_fields(fields).items():
                ops[f"{id}.{k}"] = v
        if due:
            validate_iso_datetime(due)
            ops[f"{id}.due"] = due
        if interval:
            parse_iso_duration(interval)
            if not task.get("due") and not due:
                return {"result": result_err("interval requires a due date")}
            ops[f"{id}.interval"] = interval
        if title:
            ops[f"{id}.title"] = title
        if description:
            ops[f"{id}.description"] = description
        if status:
            ops[f"{id}.status"] = status
        ops[f"{id}.updated"] = now_iso()
        _mem("PATCH", TASKS_TRAIT, {"$set": ops})
        return {"result": result_ok()}
    except (ValueError, MemoryError) as e:
        return {"result": result_err(str(e))}

@tool(permission={"arg": "id"})
def task_comment(
    id: Annotated[str, "task UUID"],
    text: Annotated[str, "summary of work done on this task"],
) -> HookResult:
    """log work done on a task. for recurring tasks, auto-bumps due by interval"""
    try:
        if not text:
            return {"result": result_err("text is required")}
        task = _task(id)
        if not task:
            return {"result": result_err(f"not found: {id}")}
        # THE ORDER IS LOAD-BEARING AND THERE IS NO TRANSACTION ACROSS TWO FILES. append the
        # comment FIRST: a crash between the two then leaves a comment against a stale due date,
        # which is visible and self-correcting. the other order leaves a bumped task with no record
        # of why, which is invisible.
        _mem("POST", TASK_COMMENTS_TRAIT, {"fields": {"task_id": id, "text": text}})
        ops = {f"{id}.updated": now_iso()}
        extra = {}
        if task.get("interval") and task.get("due"):
            old_due = validate_iso_datetime(task["due"])
            delta = parse_iso_duration(task["interval"])
            extra["due"] = format_iso(old_due + delta)
            ops[f"{id}.due"] = extra["due"]
        _mem("PATCH", TASKS_TRAIT, {"$set": ops})
        return {"result": result_ok(extra)}
    except (ValueError, MemoryError) as e:
        return {"result": result_err(str(e))}

# --- prompt tools (the hook owns its prompts, v3) ---

@tool
def prompt_list() -> HookResult:
    """list the persona's prompt files (the prompt contract driving each lifecycle stage)"""
    try:
        names = sorted(_named(_index(), PROMPTS))
    except MemoryError as e:
        return {"result": result_err(str(e))}
    return {"result": f"prompts: {', '.join(names)}" if names else "no prompts"}

@tool(permission={"arg": "prompt"})
def prompt_read(
    prompt: Annotated[str, param("prompt filename in prompts/ (e.g. chat.md)")],
) -> HookResult:
    """read a prompt file. these drive the system prompt (preamble/chat), heartbeat, and recover"""
    try:
        got = _mem("GET", f"{PROMPTS}/{prompt}")
        return {"result": got if isinstance(got, str) else json.dumps(got)}
    except MemoryError as e:
        return {"result": str(e)}

@tool(permission={"arg": "prompt"})
def prompt_write(
    prompt: Annotated[str, param("prompt filename in prompts/ (e.g. chat.md)")],
    content: Annotated[str, param("full content for the prompt file")],
) -> HookResult:
    """create or overwrite a prompt file. affects the next mutate_request/heartbeat/recover"""
    try:
        _mem("PUT", f"{PROMPTS}/{prompt}", content)
    except MemoryError as e:
        return {"result": result_err(str(e))}
    return {"result": f"wrote {prompt}"}

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
        entry = {"name": name, "description": fn.__doc__ or "", "parameters": params}
        if hasattr(fn, "_permission"):
            entry["permission"] = fn._permission
        defs.append(entry)
    return defs

@hook
def discover(ctx: dict) -> HookResult:
    names = [t["name"] for t in tool_defs()]
    debug(f"tools: {', '.join(names)}")
    return {
        "name": "persona",
        "test": "persona_test.py",
        "tools": tool_defs(),
    }

@hook
def mutate_request(ctx: dict) -> HookResult:
    # v2 host-capability surfacing: log host/model/user when present.
    host = ctx.get("host") or {}
    if host:
        debug(f"host={host.get('name', '?')} v={host.get('version', '?')} memory={MEMORY or '(none)'}")
    if "model" in ctx:
        debug(f"model={ctx.get('model') or '(none)'}")
    if "user" in ctx:
        debug(f"user_len={len(ctx.get('user') or '')}")
    # path-gating (e.g. opencode's title-gen / subagent suppression) is
    # the host's responsibility - see the opencode agent marker.
    try:
        return {"system": system_prompt("chat"), **REPLACE_SYSTEM}
    except MemoryError as e:
        # A SESSION WITH NO SOUL MUST SAY SO. returning nothing would leave the backend's own
        # default in place, which reads as persona simply not being loaded.
        debug(f"mutate_request: {e}")
        return {"system": [f"system degraded - memory is unreachable ({e})"], **REPLACE_SYSTEM}

@hook
def format_notification(ctx: dict) -> HookResult:
    notifications = ctx.get("notifications", [])
    changed = set()
    for n in notifications:
        if n.get("type") == "trait_changed":
            changed.update(n.get("files", []))
    if not changed:
        return {}
    return {"message": f"traits were updated: {', '.join(sorted(changed))}. re-read if needed."}

@hook
def observe_message(ctx: dict) -> HookResult:
    session = ctx.get("session", {})
    debug(f"session={session.get('id', '?')} agent={session.get('agent', '?')}")
    return {}

@hook
def before_stop(ctx: dict) -> HookResult:
    session = ctx.get("session", {})
    answer = ctx.get("answer", "")
    debug(f"session={session.get('id', '?')} answer_len={len(answer)}")
    if "exit_reason" in ctx:
        debug(f"exit_reason={ctx.get('exit_reason')} final={ctx.get('final', False)}")
        if ctx.get("error"):
            debug(f"error={ctx.get('error')}")
    return {}

@hook
def heartbeat(ctx: dict) -> HookResult:
    user = prompt_text("heartbeat").strip()
    if not user:
        debug("no heartbeat prompt, skipping")
        return {}
    try:
        return {"system": system_prompt("heartbeat"), "user": user, **REPLACE_SYSTEM}
    except MemoryError as e:
        debug(f"heartbeat: {e}")
        return {}

@hook
def recover(ctx: dict) -> HookResult:
    debug(f"recovering from {ctx.get('failed_hook', '?')}: {ctx.get('error', '?')}")
    user = prompt_text("recover")
    if not user:
        return {"system": ["system recovery - prompts unavailable"], **REPLACE_SYSTEM}
    try:
        return {"system": system_prompt("recover"), "user": user, **REPLACE_SYSTEM}
    except MemoryError:
        return {"system": ["system recovery - prompts unavailable"], **REPLACE_SYSTEM}

@hook
def before_tool(ctx: dict) -> HookResult:
    return {}

@hook
def after_tool(ctx: dict) -> HookResult:
    return {}

@hook
def compacting(ctx: dict) -> HookResult:
    # persona owns compaction: hand back its compaction.md as the summarization prompt. an
    # empty prompt (no compaction.md) falls back to the backend's own compaction.
    instructions = prompt_text("compaction").strip()
    return {"prompt": instructions} if instructions else {}

# dispatch to @tool-registered handler by name
@hook
def execute_tool(ctx: dict) -> HookResult:
    name = ctx.get("tool", "")
    handler = TOOLS.get(name)
    if not handler:
        debug(f"unknown tool: {name}")
        return {"result": result_err(f"unknown tool: {name}")}
    args = ctx.get("args", {})
    debug(f"tool={name} args={list(args.keys())}")
    try:
        result = handler(**args)
        debug(f"tool={name} result keys={list(result.keys())}")
        return result
    except Exception as e:
        debug(f"tool={name} error: {e}")
        return {"result": result_err(f"tool error: {e}")}

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
    # THE ENDPOINT IS LEARNED HERE, ONCE, before any handler runs -- `execute_tool` calls handlers
    # with the model's arguments only, so a tool has no ctx of its own to read it from.
    MEMORY = (ctx.get("host") or {}).get("memory") or MEMORY
    try:
        result = h(ctx)
    except Exception as e:
        debug(f"{sys.argv[1]}: {e}")
        result = {"error": str(e)}
    for key, value in result.items():
        print(json.dumps({key: value}), flush=True)
