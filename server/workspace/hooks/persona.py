#!/usr/bin/env python3
"""persona hook dispatcher."""

import json, re, sys, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, TypedDict, get_type_hints

# workspace layout: traits/ for persona files, prompts/ for builtin templates
WORKSPACE = Path(__file__).resolve().parent.parent
TRAITS = WORKSPACE / "traits"
PROMPTS = WORKSPACE / "prompts"
AVATAR = "🌀"
ISO_DT_DESC = "ISO 8601 datetime with timezone (e.g. 2026-04-01T09:00:00.000Z)"
ISO_DUR_DESC = "ISO 8601 duration (e.g. P1D, P1W, P1M, P1Y, PT1H, PT30M)"
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

# parameter spec: dict metadata = typed param, bare string = string type (backwards compat)
def param(description, type="string", optional=False):
    return {"type": type, "description": description, "optional": optional}

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
    include_hidden: Annotated[str, param("include hidden (dot-prefixed) traits", type="boolean", optional=True)] = "false",
) -> HookResult:
    """list all traits of the persona"""
    show_hidden = str(include_hidden).lower() == "true"
    names = trait_names(include_hidden=show_hidden)
    return {"result": f"{AVATAR} available traits: {', '.join(names)}"}

@tool
def trait_read(
    trait: Annotated[str, "trait filename in traits/ (e.g. SOUL.md)"],
) -> HookResult:
    """read a trait from the persona"""
    try:
        trait_path(trait)
    except ValueError as e:
        return {"result": f"{AVATAR} invalid trait: {e}"}
    return {"result": f"{AVATAR} {format_trait(trait)}"}

@tool
def trait_write(
    trait: Annotated[str, "trait filename in traits/ (e.g. SOUL.md)"],
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
def trait_edit(
    trait: Annotated[str, "trait filename in traits/ (e.g. SOUL.md)"],
    oldString: Annotated[str, "the text to replace"],
    newString: Annotated[str, "the text to replace it with (must be different from oldString)"],
    replaceAll: Annotated[str, param("replace all occurrences (default false)", type="boolean", optional=True)] = "false",
) -> HookResult:
    """edit a trait in the persona (find-and-replace)"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": f"{AVATAR} invalid trait: {e}"}
    TRAITS.mkdir(parents=True, exist_ok=True)
    content = path.read_text()
    n = content.count(oldString)
    if n == 0:
        return {"result": f"{AVATAR} failed: oldString not found"}
    if n > 1 and str(replaceAll).lower() != "true":
        return {"result": f"{AVATAR} failed: {n} matches for oldString, expected 1 (use replaceAll to replace all)"}
    if str(replaceAll).lower() == "true":
        path.write_text(content.replace(oldString, newString))
    else:
        path.write_text(content.replace(oldString, newString, 1))
    return {"result": f"{AVATAR} successfully edited {trait}", "modified": [trait],
            "notify": [{"type": "trait_changed", "files": [trait]}]}

@tool
def trait_delete(
    trait: Annotated[str, "trait filename in traits/ (e.g. SOUL.md)"],
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
    old_trait: Annotated[str, "current trait filename in traits/"],
    new_trait: Annotated[str, "new trait filename in traits/"],
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

# --- generic structured data tools (.json traits) ---

def parse_value(raw):
    """parse a value: try JSON first, fall back to raw string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw

def resolve_key(data, key):
    """walk a dot-path key, returning (parent, final_key, exists)."""
    if not key:
        return None, None, True
    parts = key.split(".")
    current = data
    for part in parts[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None, None, False
        elif isinstance(current, dict):
            if part not in current:
                return None, None, False
            current = current[part]
        else:
            return None, None, False
    return current, parts[-1], True

def get_at_key(data, key):
    """get value at dot-path key."""
    if not key:
        return data
    parent, final, exists = resolve_key(data, key)
    if not exists:
        return None
    if isinstance(parent, list):
        try:
            return parent[int(final)]
        except (ValueError, IndexError):
            return None
    if isinstance(parent, dict):
        return parent.get(final)
    return None

def set_at_key(data, key, value):
    """set value at dot-path key, returning success."""
    if not key:
        return value, True
    parent, final, exists = resolve_key(data, key)
    if not exists or parent is None:
        return data, False
    if isinstance(parent, list):
        try:
            parent[int(final)] = value
            return data, True
        except (ValueError, IndexError):
            return data, False
    if isinstance(parent, dict):
        parent[final] = value
        return data, True
    return data, False

def delete_at_key(data, key):
    """delete value at dot-path key, returning success."""
    parent, final, exists = resolve_key(data, key)
    if not exists or parent is None:
        return data, False
    if isinstance(parent, list):
        try:
            del parent[int(final)]
            return data, True
        except (ValueError, IndexError):
            return data, False
    if isinstance(parent, dict):
        if final not in parent:
            return data, False
        del parent[final]
        return data, True
    return data, False

def append_at_key(data, key, value):
    """append value to array at dot-path key, returning success."""
    target = get_at_key(data, key) if key else data
    if not isinstance(target, list):
        return data, False
    target.append(value)
    return data, True

def load_json_trait(name):
    """load a .json trait, enforcing extension."""
    if not name.endswith(".json"):
        raise ValueError("trait must have .json extension")
    path = trait_path(name)
    return json.loads(path.read_text())

def save_json_trait(name, data):
    """save a .json trait."""
    path = trait_path(name)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

@tool
def data_read(
    trait: Annotated[str, "trait filename in traits/, must end in .json (e.g. .tasks.json)"],
    key: Annotated[str, param("dot-path selector (e.g. mykey, nested.key, arr.0)", optional=True)] = "",
) -> HookResult:
    """read structured data from a .json trait, optionally at a dot-path key"""
    try:
        data = load_json_trait(trait)
        result = get_at_key(data, key) if key else data
        return {"result": f"{AVATAR} {json.dumps(result, indent=2, ensure_ascii=False)}"}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def data_update(
    trait: Annotated[str, "trait filename in traits/, must end in .json (e.g. .tasks.json)"],
    key: Annotated[str, param("dot-path selector (e.g. mykey, nested.key, arr.0)", optional=True)] = "",
    value: Annotated[object, param("the value to set (any JSON type)", type="any")] = None,
) -> HookResult:
    """set a value in a .json trait at a dot-path key, or overwrite the whole file"""
    try:
        try:
            data = load_json_trait(trait)
        except FileNotFoundError:
            data = {}
        if not key:
            save_json_trait(trait, value)
        else:
            data, ok = set_at_key(data, key, value)
            if not ok:
                return {"result": f"{AVATAR} failed: key not reachable: {key}"}
            save_json_trait(trait, data)
        return {"result": f"{AVATAR} successfully updated {trait}", "modified": [trait],
                "notify": [{"type": "trait_changed", "files": [trait]}]}
    except ValueError as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def data_delete(
    trait: Annotated[str, "trait filename in traits/, must end in .json (e.g. .tasks.json)"],
    key: Annotated[str, "dot-path selector to delete (e.g. mykey, nested.key, arr.0)"],
) -> HookResult:
    """delete a key or array index from a .json trait"""
    try:
        data = load_json_trait(trait)
        data, ok = delete_at_key(data, key)
        if not ok:
            return {"result": f"{AVATAR} failed: key not found: {key}"}
        save_json_trait(trait, data)
        return {"result": f"{AVATAR} successfully deleted {key} from {trait}", "modified": [trait],
                "notify": [{"type": "trait_changed", "files": [trait]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def data_append(
    trait: Annotated[str, "trait filename in traits/, must end in .json (e.g. .tasks.json)"],
    key: Annotated[str, param("dot-path to array (empty = root array)", optional=True)] = "",
    value: Annotated[object, param("value to append to the array", type="any")] = None,
) -> HookResult:
    """append a value to an array in a .json trait"""
    try:
        try:
            data = load_json_trait(trait)
        except FileNotFoundError:
            data = []
        data, ok = append_at_key(data, key, value)
        if not ok:
            return {"result": f"{AVATAR} failed: target is not an array"}
        save_json_trait(trait, data)
        return {"result": f"{AVATAR} successfully appended to {trait}", "modified": [trait],
                "notify": [{"type": "trait_changed", "files": [trait]}]}
    except ValueError as e:
        return {"result": f"{AVATAR} error: {e}"}

# --- generic record tools (.jsonl traits) ---

def load_records(name):
    """load all records from a .jsonl trait, enforcing extension."""
    if not name.endswith(".jsonl"):
        raise ValueError("trait must have .jsonl extension")
    path = trait_path(name)
    lines = path.read_text().strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]

def append_record(name, record):
    """append a single record to a .jsonl trait."""
    path = trait_path(name)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

@tool
def record_append(
    trait: Annotated[str, "trait filename in traits/, must end in .jsonl (e.g. .journal.jsonl)"],
    fields: Annotated[object, param("record fields as a JSON object", type="object")] = None,
) -> HookResult:
    """append a timestamped record to a .jsonl trait"""
    try:
        if not trait.endswith(".jsonl"):
            return {"result": f"{AVATAR} error: trait must have .jsonl extension"}
        trait_path(trait)  # validate path
        record = {"timestamp": format_iso(datetime.now(timezone.utc))}
        if isinstance(fields, dict):
            record.update(fields)
        append_record(trait, record)
        return {"result": f"{AVATAR} successfully appended to {trait}", "modified": [trait],
                "notify": [{"type": "trait_changed", "files": [trait]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def record_list(
    trait: Annotated[str, "trait filename in traits/, must end in .jsonl (e.g. .journal.jsonl)"],
    type: Annotated[str, param("filter by type field", optional=True)] = "",
    limit: Annotated[str, param("max records to return (default 50)", optional=True)] = "50",
    offset: Annotated[str, param("skip first N records (default 0)", optional=True)] = "0",
    after: Annotated[str, param(f"filter: only records after this {ISO_DT_DESC}", optional=True)] = "",
    before: Annotated[str, param(f"filter: only records before this {ISO_DT_DESC}", optional=True)] = "",
) -> HookResult:
    """list records from a .jsonl trait with optional filtering"""
    try:
        records = load_records(trait)
        if type:
            records = [r for r in records if r.get("type") == type]
        if after:
            records = [r for r in records if r.get("timestamp", "") > after]
        if before:
            records = [r for r in records if r.get("timestamp", "") < before]
        start = int(offset)
        end = start + int(limit)
        page = records[start:end]
        return {"result": f"{AVATAR} {len(page)}/{len(records)} records:\n" +
                "\n".join(json.dumps(r, ensure_ascii=False) for r in page)}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def record_search(
    trait: Annotated[str, "trait filename in traits/, must end in .jsonl (e.g. .journal.jsonl)"],
    pattern: Annotated[str, "regex pattern to match against each record line"],
) -> HookResult:
    """search records in a .jsonl trait by regex"""
    try:
        records = load_records(trait)
        regex = re.compile(pattern)
        matches = [r for r in records if regex.search(json.dumps(r, ensure_ascii=False))]
        return {"result": f"{AVATAR} {len(matches)} matches:\n" +
                "\n".join(json.dumps(r, ensure_ascii=False) for r in matches)}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}
    except re.error as e:
        return {"result": f"{AVATAR} invalid regex: {e}"}

@tool
def record_count(
    trait: Annotated[str, "trait filename in traits/, must end in .jsonl (e.g. .journal.jsonl)"],
    type: Annotated[str, param("count only records with this type", optional=True)] = "",
) -> HookResult:
    """count records in a .jsonl trait"""
    try:
        records = load_records(trait)
        if type:
            records = [r for r in records if r.get("type") == type]
        return {"result": f"{AVATAR} {len(records)} records"}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

# --- task tools (fixed trait: .tasks.json) ---

TASKS_TRAIT = ".tasks.json"

# canonical format: always UTC with Z suffix, millisecond precision
# matches JavaScript's Date.toISOString() (e.g. 2026-04-01T09:00:00.000Z)
def format_iso(dt):
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"

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

def load_tasks():
    return load_json_trait(TASKS_TRAIT)

def save_tasks(data):
    save_json_trait(TASKS_TRAIT, data)

@tool
def task_list(
    status: Annotated[str, param("filter by status (e.g. open, done)", optional=True)] = "",
    due_before: Annotated[str, param(f"filter: tasks due before this {ISO_DT_DESC}", optional=True)] = "",
) -> HookResult:
    """list tasks, optionally filtered by status or due date"""
    try:
        tasks = load_tasks()
        entries = list(tasks.items()) if isinstance(tasks, dict) else []
        if status:
            entries = [(k, t) for k, t in entries if t.get("status") == status]
        if due_before:
            entries = [(k, t) for k, t in entries if t.get("due", "") and t.get("due", "") < due_before]
        lines = [f"  {k}: [{t.get('status', '?')}] {t.get('title', '?')}" +
                 (f" (due: {t['due']})" if t.get("due") else "") +
                 (f" (every {t['interval']})" if t.get("interval") else "")
                 for k, t in entries]
        items = entries
        return {"result": f"{AVATAR} {len(items)} tasks:\n" + "\n".join(lines)}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def task_read(
    id: Annotated[str, "task UUID"],
) -> HookResult:
    """read full details of a task by UUID, including description"""
    try:
        tasks = load_tasks()
        if id not in tasks:
            return {"result": f"{AVATAR} not found: {id}"}
        t = tasks[id]
        lines = [f"  id: {id}"]
        lines.append(f"  title: {t.get('title', '?')}")
        lines.append(f"  status: {t.get('status', '?')}")
        if t.get("description"):
            lines.append(f"  description: {t['description']}")
        if t.get("due"):
            lines.append(f"  due: {t['due']}")
        if t.get("interval"):
            lines.append(f"  interval: {t['interval']}")
        lines.append(f"  created: {t.get('created', '?')}")
        lines.append(f"  updated: {t.get('updated', '?')}")
        return {"result": f"{AVATAR} task:\n" + "\n".join(lines)}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def task_create(
    title: Annotated[str, "task title"],
    description: Annotated[str, param("detailed task description", optional=True)] = "",
    status: Annotated[str, param("task status (default: open)", optional=True)] = "open",
    due: Annotated[str, param(f"due date as {ISO_DT_DESC}", optional=True)] = "",
    interval: Annotated[str, param(f"recurrence as {ISO_DUR_DESC}. when a recurring task is marked done, a new task is auto-created with due bumped by this interval", optional=True)] = "",
) -> HookResult:
    """create a new task. set interval for recurring tasks (auto-creates next instance when done)"""
    try:
        tasks = load_tasks()
        if not isinstance(tasks, dict):
            return {"result": f"{AVATAR} error: {TASKS_TRAIT} is not an object"}
        if due:
            validate_iso_datetime(due)
        if interval:
            parse_iso_duration(interval)
            if not due:
                return {"result": f"{AVATAR} error: interval requires a due date"}
        task_id = str(uuid.uuid4())
        now = format_iso(datetime.now(timezone.utc))
        task = {"title": title, "status": status, "created": now, "updated": now}
        if description:
            task["description"] = description
        if due:
            task["due"] = due
        if interval:
            task["interval"] = interval
        tasks[task_id] = task
        save_tasks(tasks)
        return {"result": f"{AVATAR} created task {task_id}: {title}", "modified": [TASKS_TRAIT],
                "notify": [{"type": "trait_changed", "files": [TASKS_TRAIT]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def task_update(
    id: Annotated[str, "task UUID"],
    title: Annotated[str, param("new title", optional=True)] = "",
    description: Annotated[str, param("new description", optional=True)] = "",
    status: Annotated[str, param("new status. when a recurring task is set to done, a new task is auto-created with due bumped by its interval", optional=True)] = "",
    due: Annotated[str, param(f"new due date as {ISO_DT_DESC}", optional=True)] = "",
    interval: Annotated[str, param(f"recurrence as {ISO_DUR_DESC}. requires due", optional=True)] = "",
) -> HookResult:
    """update an existing task by UUID. recurring tasks auto-create next instance when marked done"""
    try:
        tasks = load_tasks()
        if id not in tasks:
            return {"result": f"{AVATAR} not found: {id}"}
        if due:
            validate_iso_datetime(due)
            tasks[id]["due"] = due
        if interval:
            parse_iso_duration(interval)
            if not tasks[id].get("due") and not due:
                return {"result": f"{AVATAR} error: interval requires a due date"}
            tasks[id]["interval"] = interval
        if title:
            tasks[id]["title"] = title
        if description:
            tasks[id]["description"] = description
        if status:
            tasks[id]["status"] = status
        tasks[id]["updated"] = format_iso(datetime.now(timezone.utc))
        # recurring: when marked done, auto-create next instance
        if status == "done" and tasks[id].get("interval") and tasks[id].get("due"):
            old_due = validate_iso_datetime(tasks[id]["due"])
            delta = parse_iso_duration(tasks[id]["interval"])
            new_due = old_due + delta
            new_id = str(uuid.uuid4())
            now = format_iso(datetime.now(timezone.utc))
            new_task = {
                "title": tasks[id]["title"],
                "status": "open",
                "due": format_iso(new_due),
                "interval": tasks[id]["interval"],
                "created": now,
                "updated": now,
            }
            if tasks[id].get("description"):
                new_task["description"] = tasks[id]["description"]
            tasks[new_id] = new_task
            save_tasks(tasks)
            return {"result": f"{AVATAR} completed task {id}, next recurrence: {new_id} (due: {format_iso(new_due)})",
                    "modified": [TASKS_TRAIT],
                    "notify": [{"type": "trait_changed", "files": [TASKS_TRAIT]}]}
        save_tasks(tasks)
        return {"result": f"{AVATAR} updated task {id}", "modified": [TASKS_TRAIT],
                "notify": [{"type": "trait_changed", "files": [TASKS_TRAIT]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def task_delete(
    id: Annotated[str, "task UUID"],
) -> HookResult:
    """delete a task by UUID"""
    try:
        tasks = load_tasks()
        if id not in tasks:
            return {"result": f"{AVATAR} not found: {id}"}
        del tasks[id]
        save_tasks(tasks)
        return {"result": f"{AVATAR} deleted task {id}", "modified": [TASKS_TRAIT],
                "notify": [{"type": "trait_changed", "files": [TASKS_TRAIT]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

# --- journal tools (fixed trait: .journal.jsonl) ---

JOURNAL_TRAIT = ".journal.jsonl"

@tool
def journal_append(
    fields: Annotated[object, param("record fields as a JSON object", type="object")] = None,
) -> HookResult:
    """append a timestamped entry to the journal"""
    try:
        trait_path(JOURNAL_TRAIT)
        record = {"timestamp": format_iso(datetime.now(timezone.utc))}
        if isinstance(fields, dict):
            record.update(fields)
        append_record(JOURNAL_TRAIT, record)
        return {"result": f"{AVATAR} journal entry recorded", "modified": [JOURNAL_TRAIT],
                "notify": [{"type": "trait_changed", "files": [JOURNAL_TRAIT]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": f"{AVATAR} error: {e}"}

@tool
def journal_list(
    type: Annotated[str, param("filter by type field", optional=True)] = "",
    limit: Annotated[str, param("max entries to return (default 50)", optional=True)] = "50",
    offset: Annotated[str, param("skip first N entries (default 0)", optional=True)] = "0",
    after: Annotated[str, param(f"only entries after this {ISO_DT_DESC}", optional=True)] = "",
    before: Annotated[str, param(f"only entries before this {ISO_DT_DESC}", optional=True)] = "",
) -> HookResult:
    """list journal entries with optional filtering"""
    return record_list(trait=JOURNAL_TRAIT, type=type, limit=limit,
                       offset=offset, after=after, before=before)

@tool
def journal_search(
    pattern: Annotated[str, "regex pattern to match against each entry"],
) -> HookResult:
    """search journal entries by regex"""
    return record_search(trait=JOURNAL_TRAIT, pattern=pattern)

@tool
def journal_count(
    type: Annotated[str, param("count only entries with this type", optional=True)] = "",
) -> HookResult:
    """count journal entries"""
    return record_count(trait=JOURNAL_TRAIT, type=type)

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
