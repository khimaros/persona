#!/usr/bin/env python3
"""eval harness: send natural language queries via opencode and verify tool calls.

requires a running opencode server with the persona agent configured.

usage:
    make eval
    OPENCODE_URL=http://host:4096 pytest evals/persona_eval.py -v

environment:
    OPENCODE_URL      server base URL (required, set by Makefile from container ADDRESS)
    OPENCODE_DIR      project directory for x-opencode-directory header (default: cwd)
    OPENCODE_AGENT    agent name (default: per)
    OPENCODE_MODEL    model ID override, e.g. anthropic/claude-sonnet-4-20250514
"""

import json, os, re, time, warnings
import urllib.request, urllib.error
import pytest

BASE_URL = os.environ.get("OPENCODE_URL", "")
DIRECTORY = os.environ.get("OPENCODE_DIR", os.getcwd())
AGENT = os.environ.get("OPENCODE_AGENT", "per")
MODEL = os.environ.get("OPENCODE_MODEL", "")

POLL_INTERVAL = 2
POLL_TIMEOUT = 86400

# test data constants
TEST_TRAIT = "eval_test_trait.md"
TEST_TRAIT_CONTENT = "this is an eval test trait for verification"
TEST_TASK_SUMMARY = "review eval results"
TEST_TASK_DUE = "2099-12-31T00:00:00.000+00:00"
TEST_CLOSED_TASK_SUMMARY = "update documentation"
TEST_CLOSED_TASK_DUE = "2099-12-31T00:00:00.000+00:00"
TEST_RECURRING_SUMMARY = "write a short poem to the poems.md trait"
TEST_RECURRING_DUE = "2025-01-01T00:00:00.000+00:00"
TEST_RECURRING_INTERVAL = "PT1H"
TEST_RECURRING_DUE_BUMPED = "2025-01-01T01:00:00.000+00:00"
TEST_JOURNAL_CONTENT = "eval test observation: the sky is particularly blue today"
TEST_TRAIT_RENAME = "eval_test_trait_renamed.md"
TEST_DATA_TRAIT = ".eval_data.json"

# --- HTTP helpers ---

def api(method, path, body=None, expect_empty=False):
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json", "x-opencode-directory": DIRECTORY}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        if expect_empty or not raw.strip():
            return {}
        return json.loads(raw)

def wait_idle(session_id):
    """poll session status until busy then idle, or timeout."""
    deadline = time.time() + POLL_TIMEOUT
    saw_busy = False
    while time.time() < deadline:
        statuses = api("GET", "/session/status")
        status = statuses.get(session_id, {})
        if status and status.get("type") != "idle":
            saw_busy = True
        if saw_busy and (not status or status.get("type") == "idle"):
            return True
        time.sleep(POLL_INTERVAL)
    return False

# --- response parsing ---

class Response:
    """parsed LLM response with tool calls, text, reasoning, and full part history."""
    def __init__(self, raw):
        parts = raw.get("parts", [])
        self.parts = parts
        self.calls = []
        for p in parts:
            if p.get("type") == "tool":
                s = p.get("state", {})
                self.calls.append({
                    "tool": p["tool"],
                    "input": s.get("input", {}),
                    "output": s.get("output", ""),
                    "status": s.get("status", "unknown"),
                })
        self.text = "\n".join(
            p.get("text", "") for p in parts if p.get("type") == "text")
        self.reasoning = "\n".join(
            p.get("text", "") for p in parts if p.get("type") == "reasoning")

    @property
    def diag(self):
        return format_diagnostics(self.calls, self.text, self.reasoning, self.parts)

    def tool_output(self, index):
        """parse JSON output of tool call at index."""
        raw = self.calls[index]["output"] if index < len(self.calls) else ""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

# --- assertion helpers ---

def match_args(expected, actual):
    """check that expected args are a subset of actual args."""
    for key, val in expected.items():
        if key not in actual:
            return False, f"missing key: {key}"
        if isinstance(val, dict):
            if not isinstance(actual[key], dict):
                return False, f"{key}: expected dict, got {type(actual[key]).__name__}"
            ok, msg = match_args(val, actual[key])
            if not ok:
                return False, f"{key}.{msg}"
        elif actual[key] != val:
            return False, f"{key}: expected {val!r}, got {actual[key]!r}"
    return True, ""

def format_call(c):
    """format a single tool call for diagnostics."""
    s = f"{c['tool']} [{c['status']}]({json.dumps(c['input'], ensure_ascii=False)[:200]})"
    if c["status"] == "error":
        s += f"\n      error: {c['output'][:200]}"
    return s

def format_diagnostics(calls, text, reasoning, parts=None):
    lines = [f"actual calls ({len(calls)}):"]
    for i, c in enumerate(calls):
        lines.append(f"  [{i}] {format_call(c)}")
    if text:
        lines.append(f"text: {text}")
    if reasoning:
        lines.append(f"reasoning: {reasoning}")
    if parts:
        lines.append(f"parts ({len(parts)}):")
        for i, p in enumerate(parts):
            lines.append(f"  [{i}] {json.dumps(p, ensure_ascii=False)}")
    return "\n".join(lines)

def parse_tool_output(output):
    """parse JSON from a raw tool output string."""
    if not output:
        return {}
    try:
        return json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return {}

class Call:
    """expected tool call spec with | support for alternatives."""
    __slots__ = ("tool", "args", "status", "output")

    def __init__(self, tool, args=None, status="completed", output=None):
        self.tool = tool
        self.args = args
        self.status = status
        self.output = output

    def __or__(self, other):
        if isinstance(other, AnyOf):
            return AnyOf([self] + other.alts)
        return AnyOf([self, other])

    def __repr__(self):
        return self.tool

C = Call

class AnyOf:
    """one of several alternative call specs, created via Call | Call."""
    __slots__ = ("alts",)

    def __init__(self, alts):
        self.alts = alts

    def __or__(self, other):
        if isinstance(other, AnyOf):
            return AnyOf(self.alts + other.alts)
        return AnyOf(self.alts + [other])

    def __repr__(self):
        return " | ".join(repr(a) for a in self.alts)

def _as_call(spec):
    """normalize a dict or Call into a Call."""
    if isinstance(spec, (Call, AnyOf)):
        return spec
    return Call(spec["tool"], spec.get("args"), spec.get("status", "completed"), spec.get("output"))

def _try_match(actual, call):
    """check a single actual call against a Call spec, returning (ok, error_msg)."""
    if actual["tool"] != call.tool:
        return False, f"expected {call.tool}, got {actual['tool']}"
    if actual["status"] != call.status:
        return False, f"expected status={call.status}, got status={actual['status']}"
    if call.args is not None:
        ok, msg = match_args(call.args, actual["input"])
        if not ok:
            return False, f"args: {msg}"
    if call.output is not None:
        parsed = parse_tool_output(actual["output"])
        ok, msg = match_args(call.output, parsed)
        if not ok:
            return False, f"output: {msg}"
    return True, ""

def _tool_names(spec):
    """all possible tool names from a Call or AnyOf."""
    spec = _as_call(spec)
    if isinstance(spec, AnyOf):
        return {a.tool for a in spec.alts}
    return {spec.tool}

def _check_call(actual, spec, prefix, diag):
    """validate an actual call against a spec (Call, AnyOf, or dict)."""
    spec = _as_call(spec)
    if isinstance(spec, AnyOf):
        errors = []
        for alt in spec.alts:
            ok, msg = _try_match(actual, alt)
            if ok:
                return
            errors.append(f"  {alt.tool}: {msg}")
        assert False, (
            f"{prefix}: expected one of {spec}, got {actual['tool']}\n"
            + "\n".join(errors) + f"\n{diag}"
        )
    ok, msg = _try_match(actual, spec)
    assert ok, f"{prefix} {spec.tool}: {msg}\n{diag}"

def assert_calls(r, expect, also=None):
    """assert tool calls match expected sequence exactly.

    r: Response object
    expect: list of Call, AnyOf, or dicts. each specifies a required call
        in order. use Call("tool") | Call("tool") for alternatives.
    also: list of permitted extra call specs (same format as expect).
        extra calls matching any spec in also may appear anywhere
        without causing a count/order mismatch. required calls in
        expect are matched first, so a tool can appear in both expect
        and also (required once, extras permitted).
    """
    also = also or []
    exp_names = [_tool_names(e) for e in expect]
    also_names = set()
    for a in also:
        also_names |= _tool_names(a)
    # greedily match actual calls against expect in order
    required, extras = [], []
    ei = 0
    for c in r.calls:
        if ei < len(expect) and c["tool"] in exp_names[ei]:
            required.append(c)
            ei += 1
        elif c["tool"] in also_names:
            extras.append(c)
        else:
            required.append(c)
    assert len(required) == len(expect), (
        f"expected {len(expect)} required call(s), got {len(required)}\n"
        f"expected: {[repr(e) for e in expect]}\n"
        f"also permitted: {[repr(a) for a in also]}\n"
        f"extras: {[c['tool'] for c in extras]}\n{r.diag}"
    )
    for i, (actual, exp) in enumerate(zip(required, expect)):
        _check_call(actual, exp, f"call [{i}]", r.diag)
    # validate each extra call against its matching also spec
    for c in extras:
        spec = next(a for a in also if c["tool"] in _tool_names(a))
        _check_call(c, spec, f"also[{c['tool']}]", r.diag)
    if extras:
        names = [c["tool"] for c in extras]
        warnings.warn(f"optional calls used: {names}", stacklevel=2)

def assert_bash_sequence(r, patterns):
    """assert bash tool calls match expected command patterns in order.

    patterns: list of regex patterns to match against command strings.
    only bash calls are considered; non-bash calls are ignored.
    """
    bash_calls = [c for c in r.calls if c["tool"] == "bash"]
    commands = [c["input"].get("command", "") for c in bash_calls]
    assert len(bash_calls) >= len(patterns), (
        f"expected at least {len(patterns)} bash call(s), got {len(bash_calls)}\n"
        f"commands: {commands}\n{r.diag}"
    )
    for i, pattern in enumerate(patterns):
        assert re.search(pattern, commands[i]), (
            f"bash call [{i}]: expected command matching /{pattern}/\n"
            f"actual: {commands[i]!r}\n{r.diag}"
        )

def assert_text(r, pattern):
    """assert response text matches a regex pattern."""
    assert re.search(pattern, r.text, re.IGNORECASE), (
        f"expected text matching /{pattern}/i\n{r.diag}"
    )

# --- session fixture ---

@pytest.fixture(scope="module")
def session_id():
    if not BASE_URL:
        pytest.skip("OPENCODE_URL not set")
    try:
        api("GET", "/global/health")
    except Exception as e:
        pytest.skip(f"opencode server unreachable: {e}")
    result = api("POST", "/session", {"title": f"persona-eval-{int(time.time())}"})
    return result["id"]

class SessionState:
    msg_count = 0

@pytest.fixture(scope="module")
def state():
    return SessionState()

def send_prompt(session_id, state, text):
    """send a prompt, wait for completion, return Response."""
    body = {"agent": AGENT, "parts": [{"type": "text", "text": text}]}
    if MODEL:
        provider, model = MODEL.split("/", 1)
        body["model"] = {"providerID": provider, "modelID": model}
    msgs = api("GET", f"/session/{session_id}/message")
    state.msg_count = len(msgs)
    api("POST", f"/session/{session_id}/prompt_async", body, expect_empty=True)
    assert wait_idle(session_id), "timed out waiting for LLM response"
    msgs = api("GET", f"/session/{session_id}/message")
    new_msgs = msgs[state.msg_count:]
    parts = []
    for msg in new_msgs:
        if msg.get("info", {}).get("role") == "assistant":
            parts.extend(msg.get("parts", []))
    return Response({"parts": parts})

# === eval tests ===

# --- core expansion (answer from system prompt, no tools) ---

class TestCoreExpansion:
    def test_agents_from_system_prompt(self, session_id, state):
        """LLM answers about plugins from inlined AGENTS trait without tool calls."""
        r = send_prompt(session_id, state, "what plugins are you built on? answer briefly, just name them.")
        assert_calls(r, [], also=[
            {"tool": "persona_trait_read", "args": {"trait": "AGENTS.md"}},
        ])
        assert_text(r, r"(?i)evolve|bridge")

# --- trait tools: create, list, read, edit, read, delete ---

class TestTraitLifecycle:
    def test_01_create(self, session_id, state):
        r = send_prompt(session_id, state,
            f"create a new trait called {TEST_TRAIT} with this exact content: {TEST_TRAIT_CONTENT}")
        assert_calls(r, [
            {"tool": "persona_trait_write", "args": {"trait": TEST_TRAIT}, "output": {"success": True}},
        ])

    def test_02_list_includes_created(self, session_id, state):
        r = send_prompt(session_id, state, "what traits do i have? list every filename.")
        assert_calls(r, [
            {"tool": "persona_trait_list"},
        ])
        assert_text(r, re.escape(TEST_TRAIT))

    def test_03_read_returns_content(self, session_id, state):
        r = send_prompt(session_id, state,
            f"read the {TEST_TRAIT} trait and quote its full content back to me verbatim.")
        assert_calls(r, [
            {"tool": "persona_trait_read", "args": {"trait": TEST_TRAIT}},
        ])
        assert_text(r, "eval test trait for verification")

    def test_04_append(self, session_id, state):
        r = send_prompt(session_id, state,
            f"append a new line to {TEST_TRAIT}: 'updated by eval harness'")
        assert_calls(r, [
            {"tool": "persona_trait_append", "args": {"trait": TEST_TRAIT}, "output": {"success": True}},
        ], also=[
            {"tool": "persona_trait_read", "args": {"trait": TEST_TRAIT}},
        ])

    def test_05_read_after_append(self, session_id, state):
        r = send_prompt(session_id, state,
            f"read {TEST_TRAIT} again and quote its full content verbatim.")
        assert_calls(r, [
            {"tool": "persona_trait_read", "args": {"trait": TEST_TRAIT}},
        ])
        assert_text(r, "updated by eval harness")

    def test_06_delete(self, session_id, state):
        r = send_prompt(session_id, state, f"delete the {TEST_TRAIT} trait")
        assert_calls(r, [
            {"tool": "persona_trait_delete", "args": {"trait": TEST_TRAIT}, "output": {"success": True}},
        ])

# --- task tools: create, query, filter, count, delete ---

class TestTaskLifecycle:
    def test_01_create(self, session_id, state):
        r = send_prompt(session_id, state,
            f"create a task: {TEST_TASK_SUMMARY}. due {TEST_TASK_DUE}. tell me the task id from the response.")
        assert_calls(r, [
            {"tool": "persona_task_create", "args": {"title": TEST_TASK_SUMMARY, "due": TEST_TASK_DUE}, "output": {"success": True}},
        ])
        task_id = r.tool_output(0).get("id", "")
        assert task_id, f"expected id in tool output\n{r.diag}"
        assert task_id in r.text, f"expected LLM to relay task id {task_id}\n{r.diag}"

    def test_02_create_closed(self, session_id, state):
        """create a second task and immediately close it for filter contrast."""
        r = send_prompt(session_id, state,
            f"create a task: {TEST_CLOSED_TASK_SUMMARY}. due {TEST_CLOSED_TASK_DUE}. then mark it as closed.")
        assert_calls(r, [
            {"tool": "persona_task_create", "args": {"title": TEST_CLOSED_TASK_SUMMARY, "due": TEST_CLOSED_TASK_DUE}, "output": {"success": True}},
            {"tool": "persona_task_update", "args": {"status": "closed"}, "output": {"success": True}},
        ])

    def test_03_filter_by_due_date(self, session_id, state):
        r = send_prompt(session_id, state,
            "what tasks are due before 2100-01-01? quote each title.")
        assert_calls(r, [
            {"tool": "persona_data_query", "args": {"trait": ".tasks.json"}},
        ])
        assert_text(r, re.escape(TEST_TASK_SUMMARY))

    def test_04_count_by_status(self, session_id, state):
        r = send_prompt(session_id, state,
            "how many tasks do i have in each status? give me the counts.")
        assert_calls(r, [
            {"tool": "persona_data_count", "args": {"trait": ".tasks.json", "field": "status"}},
        ])

    def test_05_filter_open(self, session_id, state):
        r = send_prompt(session_id, state,
            "show me only open tasks. quote their titles.")
        assert_calls(r, [
            {"tool": "persona_data_query", "args": {"trait": ".tasks.json"}},
        ])
        assert_text(r, re.escape(TEST_TASK_SUMMARY))

    def test_06_comment(self, session_id, state):
        r = send_prompt(session_id, state,
            f"add a comment on the '{TEST_TASK_SUMMARY}' task: 'initial verification passed'")
        assert_calls(r, [
            {"tool": "persona_task_comment", "args": {"text": "initial verification passed"}, "output": {"success": True}},
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": ".tasks.json"}},
        ])

    def test_07_delete_specific(self, session_id, state):
        r = send_prompt(session_id, state,
            f"delete the '{TEST_TASK_SUMMARY}' task from .tasks.json by its id")
        assert_calls(r, [
            C("persona_data_delete", args={"trait": ".tasks.json"}),
        ], also=[
            C("persona_data_query", args={"trait": ".tasks.json"}),
        ])

    def test_08_delete_all(self, session_id, state):
        r = send_prompt(session_id, state,
            "delete all remaining tasks from .tasks.json")
        assert_calls(r, [
            C("persona_data_delete", args={"trait": ".tasks.json"})
            | C("persona_trait_delete", args={"trait": ".tasks.json"}, output={"success": True})
            | C("persona_data_update", args={"trait": ".tasks.json", "value": {}}),
        ], also=[
            C("persona_data_query", args={"trait": ".tasks.json"}),
            C("persona_data_delete", args={"trait": ".tasks.json"}),
            C("persona_data_count", args={"trait": ".tasks.json"}),
        ])

# --- recurring task: create, do work (auto-bump), delete ---

class TestRecurringTask:
    def test_01_create(self, session_id, state):
        r = send_prompt(session_id, state,
            f"create a recurring task: {TEST_RECURRING_SUMMARY}. due {TEST_RECURRING_DUE}, repeats every {TEST_RECURRING_INTERVAL}. tell me the task id.")
        assert_calls(r, [
            {"tool": "persona_task_create", "args": {"title": TEST_RECURRING_SUMMARY, "due": TEST_RECURRING_DUE, "interval": TEST_RECURRING_INTERVAL}, "output": {"success": True}},
        ])
        task_id = r.tool_output(0).get("id", "")
        assert task_id, f"expected id in tool output\n{r.diag}"
        assert task_id in r.text, f"expected LLM to relay task id {task_id}\n{r.diag}"

    def test_02_work_on_task(self, session_id, state):
        """LLM should query tasks, write the trait, and comment."""
        r = send_prompt(session_id, state,
            "do the work described by my next due recurring task right now")
        assert_calls(r, [
            C("persona_trait_write", args={"trait": "poems.md"}, output={"success": True})
            | C("persona_trait_append", args={"trait": "poems.md"}, output={"success": True}),
            C("persona_task_comment", output={"success": True}),
        ], also=[
            C("persona_data_query", args={"trait": ".tasks.json"}),
            C("persona_data_count", args={"trait": ".tasks.json"}),
            C("evolve_datetime"),
            C("glob"),
            C("persona_trait_read", args={"trait": "poems.md"}),
        ])
        comment_call = next(c for c in r.calls if c["tool"] == "persona_task_comment")
        comment_out = parse_tool_output(comment_call["output"])
        assert comment_out.get("due") == TEST_RECURRING_DUE_BUMPED, (
            f"expected due auto-bump to {TEST_RECURRING_DUE_BUMPED}\n{r.diag}"
        )

    def test_03_delete(self, session_id, state):
        r = send_prompt(session_id, state,
            "find the task containing 'poem' and delete it from .tasks.json")
        assert_calls(r, [
            {"tool": "persona_data_delete", "args": {"trait": ".tasks.json"}},
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": ".tasks.json"}},
        ])

# --- trait_move ---

class TestTraitMove:
    def test_01_create(self, session_id, state):
        r = send_prompt(session_id, state,
            f"create a trait called {TEST_TRAIT} with content: 'temporary trait for rename test'")
        assert_calls(r, [
            {"tool": "persona_trait_write", "args": {"trait": TEST_TRAIT}, "output": {"success": True}},
        ], also=[
            {"tool": "persona_trait_read", "args": {"trait": TEST_TRAIT}},
        ])

    def test_02_move(self, session_id, state):
        r = send_prompt(session_id, state,
            f"rename the trait {TEST_TRAIT} to {TEST_TRAIT_RENAME}")
        assert_calls(r, [
            {"tool": "persona_trait_move", "args": {"old_trait": TEST_TRAIT, "new_trait": TEST_TRAIT_RENAME}, "output": {"success": True}},
        ], also=[
            {"tool": "persona_trait_read", "args": {"trait": TEST_TRAIT_RENAME}},
        ])

    def test_03_verify_and_cleanup(self, session_id, state):
        r = send_prompt(session_id, state,
            f"read {TEST_TRAIT_RENAME} and quote its content, then delete it")
        assert_calls(r, [
            {"tool": "persona_trait_read", "args": {"trait": TEST_TRAIT_RENAME}},
            {"tool": "persona_trait_delete", "args": {"trait": TEST_TRAIT_RENAME}, "output": {"success": True}},
        ])
        assert_text(r, "rename test")

# --- structured data tools: update, append, query, delete ---

class TestDataLifecycle:
    def test_01_update_create(self, session_id, state):
        """create a .json trait by setting a value with data_update."""
        r = send_prompt(session_id, state,
            f"set the value of 'color' to 'blue' in the {TEST_DATA_TRAIT} trait")
        assert_calls(r, [
            {"tool": "persona_data_update", "args": {"trait": TEST_DATA_TRAIT, "key": "color", "value": "blue"}, "output": {"success": True}},
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": TEST_DATA_TRAIT}},
        ])

    def test_02_update_second_field(self, session_id, state):
        r = send_prompt(session_id, state,
            f"also set 'size' to the string 'large' in {TEST_DATA_TRAIT}")
        assert_calls(r, [
            {"tool": "persona_data_update", "args": {"trait": TEST_DATA_TRAIT, "key": "size", "value": "large"}, "output": {"success": True}},
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": TEST_DATA_TRAIT}},
        ])

    def test_03_query(self, session_id, state):
        r = send_prompt(session_id, state,
            f"query all fields from {TEST_DATA_TRAIT} and show me the contents.")
        assert_calls(r, [
            {"tool": "persona_data_query", "args": {"trait": TEST_DATA_TRAIT}},
        ])
        assert_text(r, "blue")
        assert_text(r, "large")

    def test_04_append_to_array(self, session_id, state):
        """create an array field and append to it with data_append."""
        r = send_prompt(session_id, state,
            f"set 'tags' to an empty array in {TEST_DATA_TRAIT}, then append 'eval' to it")
        assert_calls(r, [
            {"tool": "persona_data_update", "args": {"trait": TEST_DATA_TRAIT, "key": "tags", "value": []}, "output": {"success": True}},
            C("persona_data_append", args={"trait": TEST_DATA_TRAIT, "key": "tags", "value": "eval"}, output={"success": True})
            | C("persona_data_append", args={"trait": TEST_DATA_TRAIT, "key": "tags", "value": ["eval"]}, output={"success": True}),
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": TEST_DATA_TRAIT}},
            C("persona_data_append", args={"trait": TEST_DATA_TRAIT, "key": "tags"}),
        ])

    def test_05_verify_append(self, session_id, state):
        r = send_prompt(session_id, state,
            f"read {TEST_DATA_TRAIT} fresh and tell me what's in the tags array")
        assert_calls(r, [
            {"tool": "persona_data_query", "args": {"trait": TEST_DATA_TRAIT}},
        ])
        assert_text(r, "eval")

    def test_06_cleanup(self, session_id, state):
        r = send_prompt(session_id, state,
            f"delete the trait file {TEST_DATA_TRAIT}")
        assert_calls(r, [
            {"tool": "persona_trait_delete", "args": {"trait": TEST_DATA_TRAIT}, "output": {"success": True}},
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": TEST_DATA_TRAIT}},
            {"tool": "persona_record_query"},
        ])

# --- journal (record) tools: append, query, count ---

class TestJournalLifecycle:
    def test_01_append(self, session_id, state):
        r = send_prompt(session_id, state,
            f"add a journal entry. the type is 'observation' and the content is '{TEST_JOURNAL_CONTENT}'")
        assert_calls(r, [
            {"tool": "persona_record_append", "args": {"trait": ".journal.jsonl"}, "output": {"success": True}},
        ], also=[
            C("persona_record_query", args={"trait": ".journal.jsonl"}),
        ])

    def test_02_query_finds_entry(self, session_id, state):
        r = send_prompt(session_id, state,
            "search my journal for entries about sky. quote the matching content.")
        assert_calls(r, [
            {"tool": "persona_record_query", "args": {"trait": ".journal.jsonl"}},
        ])
        assert_text(r, "blue")

    def test_03_count(self, session_id, state):
        r = send_prompt(session_id, state,
            "how many journal entries do i have? give me the exact number.")
        assert_calls(r, [
            {"tool": "persona_record_count", "args": {"trait": ".journal.jsonl"}},
        ])

# --- browser-use tools: start, navigate, extract, summarize ---

class TestBrowserUse:
    def test_01_start_session(self, session_id, state):
        r = send_prompt(session_id, state, "open a browser session")
        assert_bash_sequence(r, [
            r"browser-head start",
        ])

    def test_02_navigate_hackernews(self, session_id, state):
        r = send_prompt(session_id, state, "go to https://news.ycombinator.com")
        assert_bash_sequence(r, [
            r"browser-use.*open.*https://news\.ycombinator\.com",
        ])

    def test_03_summarize_top_comments(self, session_id, state):
        """extract links, visit 3 comment threads, summarize to a trait."""
        r = send_prompt(session_id, state,
            "visit the comment threads for the top 3 stories on the page. "
            "for each one, write a one-paragraph summary of the discussion to the research_notes.md trait.")
        # LLM may navigate via click or open, so just check it used browser-use enough
        bash_calls = [c for c in r.calls if c["tool"] == "bash"
                      and "browser-use" in c["input"].get("command", "")]
        assert len(bash_calls) >= 7, (
            f"expected at least 7 browser-use calls, got {len(bash_calls)}\n{r.diag}")
        # should have written to the trait
        trait_calls = [c for c in r.calls if c["tool"] in ("persona_trait_write", "persona_trait_append")]
        assert len(trait_calls) >= 1, f"expected trait write/append\n{r.diag}"
        assert trait_calls[0]["input"].get("trait") == "research_notes.md", (
            f"expected trait=research_notes.md\n{r.diag}"
        )

    def test_04_cleanup(self, session_id, state):
        r = send_prompt(session_id, state, "delete the research_notes.md trait")
        assert_calls(r, [
            {"tool": "persona_trait_delete", "args": {"trait": "research_notes.md"}, "output": {"success": True}},
        ])

