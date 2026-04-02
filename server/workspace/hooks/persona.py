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
ISO_DT_DESC = "ISO 8601 datetime with timezone offset (e.g. 2026-04-01T09:00:00.000+00:00)"
ISO_DUR_DESC = "ISO 8601 duration (e.g. P1D, P1W, P1M, P1Y, PT1H, PT30M)"
AGENT_MARKER = "<~ PERSONA AGENT MARKER ~>"
DEFAULT_READ_LIMIT = 2000
TASKS_TRAIT = ".tasks.json"
TASK_COMMENTS_TRAIT = ".tasks_comments.jsonl"

# shared parameter descriptions
FILTER_JSON_DESC = 'MongoDB filter (supported subset). exact: {"status": "open"}, supported operators: $in, $lt, $gt, $lte, $gte, $regex, $not, $or, $exists. top-level keys are AND. "id" matches dict keys'
FILTER_JSONL_DESC = 'MongoDB filter (supported subset). exact: {"type": "note"}, supported operators: $in, $lt, $gt, $lte, $gte, $regex, $not, $or, $exists. dot-paths for nested fields: {"meta.source": "web"}. top-level keys are AND'
FIELDS_JSON_DESC = 'projection: plain string names of fields to return (e.g. ["title", "status"]). NOT for filtering. omit to return all fields'
FIELDS_JSONL_DESC = 'projection: plain string names of fields to return (e.g. ["type", "content"]). NOT for filtering. omit to return all fields'
TRAIT_JSON_DESC = "trait filename in traits/, must end in .json (e.g. .tasks.json)"
TRAIT_JSONL_DESC = "trait filename in traits/, must end in .jsonl (e.g. .journal.jsonl)"
TRAIT_DESC = "trait path in traits/ (e.g. SOUL.md, sub/topic.md, .data.json)"
VALUE_DESC = 'the literal value to store, e.g. "blue", 42, [1,2], {"a":1}. passed directly as JSON, not a boolean flag'

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

def tool(fn=None, *, permission=None):
    def decorator(f):
        if permission:
            f._permission = permission
        TOOLS[f.__name__] = f
        return f
    if fn is not None:
        return decorator(fn)
    return decorator

# emit a JSONL log line to stdout (picked up by the plugin)
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

# --- trait helpers ---

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
        str(f.relative_to(TRAITS)) for f in TRAITS.rglob("*")
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

def cleanup_empty_parents(path):
    """remove empty ancestor directories up to (but not including) TRAITS/."""
    parent = path.parent
    while parent != TRAITS and parent.is_dir():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent

def prompt_path(name):
    return PROMPTS / f"{name}.md"

def format_trait(name):
    try:
        content = trait_path(name).read_text()
    except FileNotFoundError:
        content = "(empty)"
    return f"\n{{trait:{name}}}\n{content}\n"

# compose system prompt from preamble, mode-specific prompt, traits, and env
def system_prompt(mode=None):
    parts = [prompt_path("preamble").read_text()]
    if mode:
        parts.append(prompt_path(mode).read_text())
    parts += [format_trait(t) for t in core_trait_names()]
    listed = listed_trait_names()
    if listed:
        formatted = ", ".join(f"{{trait:{n}}}" for n in listed)
        parts.append(f"\nadditional traits (use trait_read to view): {formatted}\n")
    return ["".join(parts)]

# --- trait tools ---

@tool
def trait_list(
    include_hidden: Annotated[str, param("include hidden (dot-prefixed) traits", type="boolean", optional=True)] = "false",
) -> HookResult:
    """list all traits of the persona, including those in subdirectories (shown as relative paths)"""
    show_hidden = str(include_hidden).lower() == "true"
    names = trait_names(include_hidden=show_hidden)
    formatted = ", ".join(f"{{trait:{n}}}" for n in names)
    return {"result": f"available traits: {formatted}"}

@tool(permission={"arg": "trait"})
def trait_read(
    trait: Annotated[str, TRAIT_DESC],
    offset: Annotated[str, param("the line number to start reading from (1-indexed)", type="number", optional=True)] = "",
    limit: Annotated[str, param(f"the maximum number of lines to read (defaults to {DEFAULT_READ_LIMIT})", type="number", optional=True)] = "",
) -> HookResult:
    """read a trait from the persona"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": result_err(str(e))}
    try:
        content = path.read_text()
    except FileNotFoundError:
        content = "(empty)"
    lines = content.split("\n")
    start = int(offset) - 1 if offset else 0
    end = start + (int(limit) if limit else DEFAULT_READ_LIMIT)
    sliced = lines[start:end]
    header = f"\n{{trait:{trait}}}\n"
    return {"result": header + "\n".join(sliced)}

@tool(permission={"arg": "trait"})
def trait_write(
    trait: Annotated[str, TRAIT_DESC],
    content: Annotated[str, "full content for the trait"],
) -> HookResult:
    """write a trait to the persona. parent directories are created automatically"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": result_err(str(e))}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return {"result": result_ok(), "modified": [trait],
            "notify": [{"type": "trait_changed", "files": [trait]}]}

@tool(permission={"arg": "trait"})
def trait_edit(
    trait: Annotated[str, TRAIT_DESC],
    oldString: Annotated[str, "the text to replace"],
    newString: Annotated[str, "the text to replace it with (must be different from oldString)"],
    replaceAll: Annotated[str, param("replace all occurrences (default false)", type="boolean", optional=True)] = "false",
) -> HookResult:
    """edit a trait in the persona (find-and-replace)"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": result_err(str(e))}
    content = path.read_text()
    n = content.count(oldString)
    if n == 0:
        return {"result": result_err("oldString not found")}
    if n > 1 and str(replaceAll).lower() != "true":
        return {"result": result_err(f"{n} matches for oldString, expected 1 (use replaceAll to replace all)")}
    if str(replaceAll).lower() == "true":
        path.write_text(content.replace(oldString, newString))
    else:
        path.write_text(content.replace(oldString, newString, 1))
    return {"result": result_ok(), "modified": [trait],
            "notify": [{"type": "trait_changed", "files": [trait]}]}

@tool(permission={"arg": "trait"})
def trait_append(
    trait: Annotated[str, TRAIT_DESC],
    content: Annotated[str, "text to append to the trait"],
) -> HookResult:
    """append text to the end of a trait. creates the trait if it doesn't exist"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": result_err(str(e))}
    append_to_trait(trait, "\n" + content)
    return {"result": result_ok(), "modified": [trait],
            "notify": [{"type": "trait_changed", "files": [trait]}]}

@tool(permission={"arg": "trait"})
def trait_delete(
    trait: Annotated[str, TRAIT_DESC],
) -> HookResult:
    """delete a trait from the persona. empty parent directories are removed automatically"""
    try:
        path = trait_path(trait)
    except ValueError as e:
        return {"result": result_err(str(e))}
    if not path.exists():
        return {"result": result_err(f"not found: {trait}")}
    path.unlink()
    cleanup_empty_parents(path)
    return {"result": result_ok(), "modified": [trait],
            "notify": [{"type": "trait_changed", "files": [trait]}]}

@tool(permission={"arg": ["old_trait", "new_trait"]})
def trait_move(
    old_trait: Annotated[str, "current " + TRAIT_DESC],
    new_trait: Annotated[str, "new " + TRAIT_DESC],
) -> HookResult:
    """rename or move a trait in the persona. destination directories are created and empty source directories are removed automatically"""
    try:
        src = trait_path(old_trait)
        dst = trait_path(new_trait)
    except ValueError as e:
        return {"result": result_err(str(e))}
    if not src.exists():
        return {"result": result_err(f"not found: {old_trait}")}
    if dst.exists():
        return {"result": result_err(f"already exists: {new_trait}")}
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    cleanup_empty_parents(src)
    return {"result": result_ok(), "modified": [old_trait, new_trait],
            "notify": [{"type": "trait_changed", "files": [old_trait, new_trait]}]}

# --- generic structured data tools (.json traits) ---

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

# --- MongoDB-style filter evaluator ---

def _match_condition(value, condition):
    """evaluate a single field condition against a value."""
    if not isinstance(condition, dict):
        # bare value = exact match
        return value == condition
    for op, operand in condition.items():
        if op == "$eq":
            if value != operand:
                return False
        elif op == "$in":
            if value not in operand:
                return False
        elif op in ("$ne", "$not"):
            if value == operand:
                return False
        elif op == "$nin":
            if value in operand:
                return False
        elif op == "$lt":
            if value is None or value >= operand:
                return False
        elif op == "$lte":
            if value is None or value > operand:
                return False
        elif op == "$gt":
            if value is None or value <= operand:
                return False
        elif op == "$gte":
            if value is None or value < operand:
                return False
        elif op == "$regex":
            flags = 0
            opts = condition.get("$options", "")
            if "i" in opts:
                flags |= re.IGNORECASE
            if value is None or not re.search(operand, str(value), flags):
                return False
        elif op == "$exists":
            if operand and value is None:
                return False
            if not operand and value is not None:
                return False
        elif op == "$options":
            pass  # handled by $regex
        else:
            return False
    return True

def _match_filter(entry_id, entry, filter_obj):
    """evaluate a MongoDB-style filter against a dict-of-dicts entry."""
    if not isinstance(filter_obj, dict):
        return True
    for key, condition in filter_obj.items():
        if key == "$or":
            if not any(_match_filter(entry_id, entry, clause) for clause in condition):
                return False
        elif key == "id":
            if not _match_condition(entry_id, condition):
                return False
        else:
            if not _match_condition(entry.get(key), condition):
                return False
    return True

def _resolve_dot_path(obj, path):
    """resolve a dot-separated path into a nested dict (e.g. 'meta.source')."""
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj

def _validate_fields(fields):
    """return fields if valid (None or list of strings), else raise."""
    if fields is None:
        return None
    if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
        raise ValueError(f'fields must be ["name1", "name2"], got: {json.dumps(fields)[:100]}')
    return fields

def _match_record_filter(record, filter_obj):
    """evaluate a MongoDB-style filter against a record (JSONL). supports dot-path keys for nested fields."""
    if not isinstance(filter_obj, dict):
        return True
    for key, condition in filter_obj.items():
        if key == "$or":
            if not any(_match_record_filter(record, clause) for clause in condition):
                return False
        else:
            value = _resolve_dot_path(record, key) if "." in key else record.get(key)
            if not _match_condition(value, condition):
                return False
    return True

# --- data tools (.json) ---

@tool(permission={"arg": "trait"})
def data_query(
    trait: Annotated[str, TRAIT_JSON_DESC],
    key: Annotated[str, param("dot-path to a nested value (e.g. mykey, nested.key). omit to query the whole file. not needed for dict-of-dicts like .tasks.json", optional=True)] = "",
    filter: Annotated[object, param(FILTER_JSON_DESC, type="object", optional=True)] = None,
    fields: Annotated[object, param(FIELDS_JSON_DESC, type="array[string]", optional=True)] = None,
    limit: Annotated[str, param("max entries to return (default 50, applied after filter)", optional=True)] = "50",
    offset: Annotated[str, param("skip first N entries (default 0)", optional=True)] = "0",
) -> HookResult:
    """query structured data from a .json trait. without key, operates on the whole file. on dict-of-dicts, supports MongoDB-style filter on values with id matching on keys"""
    try:
        fields = _validate_fields(fields)
        data = load_json_trait(trait)
        selected = get_at_key(data, key) if key else data
        # dict-of-dicts: apply filter, pagination, fields projection
        if isinstance(selected, dict) and filter is not None or (
            isinstance(selected, dict) and (fields is not None or int(limit) < len(selected) or int(offset) != 0)
            and all(isinstance(v, dict) for v in selected.values())
        ):
            filtered = {k: v for k, v in selected.items() if _match_filter(k, v, filter)}
            items = list(filtered.items())
            start = int(offset)
            end = start + int(limit)
            if start < 0 and end >= 0:
                end = None
            page = dict(items[start:end])
            if fields is not None and isinstance(fields, list) and fields:
                page = {k: {f: v[f] for f in fields if f in v} for k, v in page.items()}
            return {"result": json.dumps(page, indent=2, ensure_ascii=False)}
        # non-dict or no filter/pagination: return as-is
        if isinstance(selected, dict) and filter is not None:
            filtered = {k: v for k, v in selected.items() if _match_filter(k, v, filter)}
            return {"result": json.dumps(filtered, indent=2, ensure_ascii=False)}
        return {"result": json.dumps(selected, indent=2, ensure_ascii=False)}
    except (ValueError, FileNotFoundError) as e:
        return {"result": result_err(str(e))}
    except re.error as e:
        return {"result": result_err(f"invalid regex: {e}")}

@tool(permission={"arg": "trait"})
def data_update(
    trait: Annotated[str, TRAIT_JSON_DESC],
    key: Annotated[str, param("dot-path selector (e.g. mykey, nested.key, arr.0)", optional=True)] = "",
    value: Annotated[object, param(VALUE_DESC, type="any")] = None,
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
                return {"result": result_err(f"key not reachable: {key}")}
            save_json_trait(trait, data)
        return {"result": result_ok(), "modified": [trait],
                "notify": [{"type": "trait_changed", "files": [trait]}]}
    except ValueError as e:
        return {"result": result_err(str(e))}

@tool(permission={"arg": "trait"})
def data_delete(
    trait: Annotated[str, TRAIT_JSON_DESC],
    key: Annotated[str, "dot-path selector to delete (e.g. mykey, nested.key, arr.0)"],
) -> HookResult:
    """delete a key or array index from a .json trait"""
    try:
        data = load_json_trait(trait)
        data, ok = delete_at_key(data, key)
        if not ok:
            return {"result": result_err(f"key not found: {key}")}
        save_json_trait(trait, data)
        return {"result": result_ok(), "modified": [trait],
                "notify": [{"type": "trait_changed", "files": [trait]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": result_err(str(e))}

@tool(permission={"arg": "trait"})
def data_append(
    trait: Annotated[str, TRAIT_JSON_DESC],
    key: Annotated[str, param("dot-path to array (empty = root array)", optional=True)] = "",
    value: Annotated[object, param(VALUE_DESC, type="any")] = None,
) -> HookResult:
    """append a value to an array in a .json trait"""
    try:
        try:
            data = load_json_trait(trait)
        except FileNotFoundError:
            data = []
        data, ok = append_at_key(data, key, value)
        if not ok:
            return {"result": result_err("target is not an array")}
        save_json_trait(trait, data)
        return {"result": result_ok(), "modified": [trait],
                "notify": [{"type": "trait_changed", "files": [trait]}]}
    except ValueError as e:
        return {"result": result_err(str(e))}

@tool(permission={"arg": "trait"})
def data_count(
    trait: Annotated[str, TRAIT_JSON_DESC],
    field: Annotated[str, param("group by this field and count occurrences of each unique value (e.g. field='status' → {\"open\": 5, \"done\": 3})", optional=True)] = "",
    filter: Annotated[object, param("MongoDB-style filter (same syntax as data_query)", type="object", optional=True)] = None,
) -> HookResult:
    """count entries in a dict-of-dicts .json trait. without field: returns total count and field names. with field: groups by that field and returns count per unique value"""
    try:
        data = load_json_trait(trait)
        if not isinstance(data, dict):
            return {"result": result_err(f"{trait} is not a dict-of-dicts")}
        entries = {k: v for k, v in data.items() if _match_filter(k, v, filter)}
        if field:
            values: dict[str, int] = {}
            for v in entries.values():
                if isinstance(v, dict):
                    fv = v.get(field)
                    if fv is not None:
                        key = str(fv)
                        values[key] = values.get(key, 0) + 1
            return {"result": json.dumps({"count": len(entries), "field": field, "values": values})}
        field_counts: dict[str, int] = {}
        for v in entries.values():
            if isinstance(v, dict):
                for k in v:
                    field_counts[k] = field_counts.get(k, 0) + 1
        return {"result": json.dumps({"count": len(entries), "fields": field_counts})}
    except (ValueError, FileNotFoundError) as e:
        return {"result": result_err(str(e))}

# --- generic record tools (.jsonl traits) ---

def load_records(name):
    """load all records from a .jsonl trait, enforcing extension."""
    if not name.endswith(".jsonl"):
        raise ValueError("trait must have .jsonl extension")
    path = trait_path(name)
    lines = path.read_text().strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]

def append_to_trait(name, text):
    """append raw text to a trait file, creating parent dirs as needed."""
    path = trait_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(text)

def append_record(name, record):
    """append a single JSON record to a .jsonl trait."""
    append_to_trait(name, json.dumps(record, ensure_ascii=False) + "\n")

def _stable_sort_record(record):
    """sort record keys alphabetically, id first."""
    keys = sorted(record.keys())
    if "id" in record:
        keys = ["id"] + [k for k in keys if k != "id"]
    return {k: record[k] for k in keys}

@tool(permission={"arg": "trait"})
def record_append(
    trait: Annotated[str, TRAIT_JSONL_DESC],
    fields: Annotated[object, param('object of field names to values, e.g. {"type": "observation", "content": "saw a bird", "meta": {"source": "web"}}. values can be strings, numbers, booleans, arrays, or nested objects', type="object")] = None,
) -> HookResult:
    """append a timestamped record to a .jsonl trait. timestamp is added automatically"""
    try:
        if not trait.endswith(".jsonl"):
            return {"result": result_err("trait must have .jsonl extension")}
        trait_path(trait)  # validate path
        if not isinstance(fields, dict) or not any(v for v in fields.values()):
            return {"result": result_err("fields must be a JSON object with at least one non-empty value")}
        record = {"timestamp": format_iso(datetime.now(timezone.utc))}
        record.update(fields)
        append_record(trait, record)
        return {"result": result_ok(), "modified": [trait],
                "notify": [{"type": "trait_changed", "files": [trait]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": result_err(str(e))}

@tool(permission={"arg": "trait"})
def record_query(
    trait: Annotated[str, TRAIT_JSONL_DESC],
    filter: Annotated[object, param(FILTER_JSONL_DESC, type="object", optional=True)] = None,
    fields: Annotated[object, param(FIELDS_JSONL_DESC, type="array[string]", optional=True)] = None,
    limit: Annotated[str, param("max records to return (default 50)", optional=True)] = "50",
    offset: Annotated[str, param("skip first N records, negative counts from end (default 0, oldest first)", optional=True)] = "0",
) -> HookResult:
    """query records from a .jsonl trait with MongoDB-style filtering and pagination"""
    try:
        fields = _validate_fields(fields)
        records = load_records(trait)
        filtered = [r for r in records if _match_record_filter(r, filter)]
        start = int(offset)
        end = start + int(limit)
        if start < 0 and end >= 0:
            end = None
        page = filtered[start:end]
        if fields is not None and isinstance(fields, list) and fields:
            page = [{f: r[f] for f in fields if f in r} for r in page]
        else:
            page = [_stable_sort_record(r) for r in page]
        return {"result": f"{len(page)}/{len(filtered)} records:\n" +
                "\n".join(json.dumps(r, ensure_ascii=False) for r in page)}
    except (ValueError, FileNotFoundError) as e:
        return {"result": result_err(str(e))}
    except re.error as e:
        return {"result": result_err(f"invalid regex: {e}")}

@tool(permission={"arg": "trait"})
def record_count(
    trait: Annotated[str, TRAIT_JSONL_DESC],
    field: Annotated[str, param("group by this field (supports dot-paths like meta.source) and count occurrences of each unique value (e.g. field='status' → {\"open\": 5, \"done\": 3})", optional=True)] = "",
    filter: Annotated[object, param("MongoDB-style filter (same syntax as record_query)", type="object", optional=True)] = None,
) -> HookResult:
    """count records in a .jsonl trait. without field: returns total count and field names. with field: groups by that field and returns count per unique value"""
    try:
        records = load_records(trait)
        filtered = [r for r in records if _match_record_filter(r, filter)]
        if field:
            values: dict[str, int] = {}
            for r in filtered:
                v = _resolve_dot_path(r, field) if "." in field else r.get(field)
                if v is not None:
                    key = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)
                    values[key] = values.get(key, 0) + 1
            return {"result": json.dumps({"count": len(filtered), "field": field, "values": values})}
        field_counts: dict[str, int] = {}
        for r in filtered:
            for k in r:
                field_counts[k] = field_counts.get(k, 0) + 1
        return {"result": json.dumps({"count": len(filtered), "fields": field_counts})}
    except (ValueError, FileNotFoundError) as e:
        return {"result": result_err(str(e))}

# --- task tools (fixed trait: .tasks.json) ---

# canonical format: UTC with +00:00 offset, millisecond precision
# matches evolve_datetime tool output (e.g. 2026-04-01T09:00:00.000+00:00)
def format_iso(dt):
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}+00:00"

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
    try:
        return load_json_trait(TASKS_TRAIT)
    except FileNotFoundError:
        return {}

def save_tasks(data):
    save_json_trait(TASKS_TRAIT, data)

@tool
def task_create(
    title: Annotated[str, "task title"],
    description: Annotated[str, param("detailed task description", optional=True)] = "",
    status: Annotated[str, param("task status (default: open)", optional=True)] = "open",
    due: Annotated[str, param(f"due date as {ISO_DT_DESC}", optional=True)] = "",
    interval: Annotated[str, param(f"recurrence as {ISO_DUR_DESC}. recurring tasks are updated via persona_task_comment, which auto-bumps due by this interval", optional=True)] = "",
    fields: Annotated[object, param("arbitrary extra fields to set, e.g. {\"owner\": \"tom\"}", type="object", optional=True)] = None,
) -> HookResult:
    """create a new task. set interval for recurring tasks. use persona_task_comment to log updates on recurring tasks"""
    try:
        tasks = load_tasks()
        if not isinstance(tasks, dict):
            return {"result": result_err(f"{TASKS_TRAIT} is not an object")}
        if due:
            validate_iso_datetime(due)
        if interval:
            parse_iso_duration(interval)
            if not due:
                return {"result": result_err("interval requires a due date")}
        task_id = str(uuid.uuid4())
        now = format_iso(datetime.now(timezone.utc))
        task = {"title": title, "status": status, "created": now, "updated": now}
        if isinstance(fields, dict):
            task.update(fields)
        if description:
            task["description"] = description
        if due:
            task["due"] = due
        if interval:
            task["interval"] = interval
        tasks[task_id] = task
        save_tasks(tasks)
        return {"result": result_ok({"id": task_id}), "modified": [TASKS_TRAIT],
                "notify": [{"type": "trait_changed", "files": [TASKS_TRAIT]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": result_err(str(e))}

@tool(permission={"arg": "id"})
def task_update(
    id: Annotated[str, "task UUID"],
    title: Annotated[str, param("new title", optional=True)] = "",
    description: Annotated[str, param("new description", optional=True)] = "",
    status: Annotated[str, param("new status", optional=True)] = "",
    due: Annotated[str, param(f"new due date as {ISO_DT_DESC}. for recurring tasks, prefer persona_task_comment to auto-bump due by interval", optional=True)] = "",
    interval: Annotated[str, param(f"recurrence as {ISO_DUR_DESC}. requires due", optional=True)] = "",
    fields: Annotated[object, param("arbitrary extra fields to set, e.g. {\"owner\": \"tom\", \"cc\": \"alice\"}", type="object", optional=True)] = None,
) -> HookResult:
    """update task metadata (title, description, fields). for recurring tasks, use persona_task_comment to log progress — it auto-bumps due by interval"""
    try:
        tasks = load_tasks()
        if id not in tasks:
            return {"result": result_err(f"not found: {id}")}
        if isinstance(fields, dict):
            tasks[id].update(fields)
        if due:
            validate_iso_datetime(due)
            tasks[id]["due"] = due
        if interval:
            parse_iso_duration(interval)
            if not tasks[id].get("due") and not due:
                return {"result": result_err("interval requires a due date")}
            tasks[id]["interval"] = interval
        if title:
            tasks[id]["title"] = title
        if description:
            tasks[id]["description"] = description
        if status:
            tasks[id]["status"] = status
        tasks[id]["updated"] = format_iso(datetime.now(timezone.utc))
        save_tasks(tasks)
        return {"result": result_ok(), "modified": [TASKS_TRAIT],
                "notify": [{"type": "trait_changed", "files": [TASKS_TRAIT]}]}
    except (ValueError, FileNotFoundError) as e:
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
        tasks = load_tasks()
        if id not in tasks:
            return {"result": result_err(f"not found: {id}")}
        now = format_iso(datetime.now(timezone.utc))
        record = {"timestamp": now, "task_id": id, "text": text}
        append_record(TASK_COMMENTS_TRAIT, record)
        tasks[id]["updated"] = now
        # recurring: bump due on comment
        extra = {}
        if tasks[id].get("interval") and tasks[id].get("due"):
            old_due = validate_iso_datetime(tasks[id]["due"])
            delta = parse_iso_duration(tasks[id]["interval"])
            tasks[id]["due"] = format_iso(old_due + delta)
            extra["due"] = tasks[id]["due"]
        save_tasks(tasks)
        return {"result": result_ok(extra), "modified": [TASKS_TRAIT, TASK_COMMENTS_TRAIT],
                "notify": [{"type": "trait_changed", "files": [TASKS_TRAIT, TASK_COMMENTS_TRAIT]}]}
    except (ValueError, FileNotFoundError) as e:
        return {"result": result_err(str(e))}

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
    return {"name": "persona", "test": "persona_test.py", "tools": tool_defs()}

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
    return {"message": f"traits were updated: {', '.join(sorted(changed))}. re-read if needed."}

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
    try:
        result = h(ctx)
    except Exception as e:
        debug(f"{sys.argv[1]}: {e}")
        result = {"error": str(e)}
    for key, value in result.items():
        print(json.dumps({key: value}), flush=True)
