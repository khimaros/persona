#!/usr/bin/env python3
"""eval harness: send natural language queries via opencode and verify tool calls.

requires a running opencode server with the persona agent configured.

usage:
    make eval
    OPENCODE_URL=http://10.0.0.1:4096 pytest server/tests/persona_eval.py -v

environment:
    OPENCODE_URL      server base URL (required, set by Makefile from container ADDRESS)
    OPENCODE_DIR      project directory for x-opencode-directory header (default: cwd)
    OPENCODE_AGENT    agent name (default: per)
    OPENCODE_MODEL    model ID override, e.g. anthropic/claude-sonnet-4-20250514
"""

import json, os, re, time
import urllib.request, urllib.error
import pytest

BASE_URL = os.environ.get("OPENCODE_URL", "")
DIRECTORY = os.environ.get("OPENCODE_DIR", os.getcwd())
AGENT = os.environ.get("OPENCODE_AGENT", "per")
MODEL = os.environ.get("OPENCODE_MODEL", "")

POLL_INTERVAL = 2
POLL_TIMEOUT = 300

# test data constants
TEST_TRAIT = "eval_test_trait.md"
TEST_TRAIT_CONTENT = "this is an eval test trait for verification"
TEST_TASK_SUMMARY = "eval test task: verify persona tools"
TEST_TASK_DUE = "2099-12-31T00:00:00.000+00:00"
TEST_JOURNAL_CONTENT = "eval test observation: the sky is particularly blue today"

# --- HTTP helpers ---

def api(method, path, body=None, expect_empty=False):
    """make an HTTP request to the opencode API."""
    url = f"{BASE_URL}{path}"
    headers = {
        "Content-Type": "application/json",
        "x-opencode-directory": DIRECTORY,
    }
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

def extract_tool_calls(response):
    calls = []
    for part in response.get("parts", []):
        if part.get("type") == "tool":
            state = part.get("state", {})
            calls.append({
                "tool": part["tool"],
                "input": state.get("input", {}),
                "output": state.get("output", ""),
                "status": state.get("status", "unknown"),
            })
    return calls

def extract_text(response):
    texts = []
    for part in response.get("parts", []):
        if part.get("type") == "text":
            texts.append(part.get("text", ""))
    return "\n".join(texts)

def parse_tool_output(output):
    """parse JSON from tool output string, returning {} on failure."""
    if not output:
        return {}
    try:
        return json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return {}

def extract_reasoning(response):
    texts = []
    for part in response.get("parts", []):
        if part.get("type") == "reasoning":
            texts.append(part.get("text", ""))
    return "\n".join(texts)

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

# --- session fixture ---

@pytest.fixture(scope="module")
def session_id():
    """create a session for the entire eval module."""
    if not BASE_URL:
        pytest.skip("OPENCODE_URL not set")
    try:
        api("GET", "/global/health")
    except Exception as e:
        pytest.skip(f"opencode server unreachable: {e}")
    result = api("POST", "/session", {"title": f"persona-eval-{int(time.time())}"})
    return result["id"]

class SessionState:
    """track message count between prompts within a session."""
    msg_count = 0

@pytest.fixture(scope="module")
def state():
    return SessionState()

def send_prompt(session_id, state, text):
    """send a prompt, wait for completion, return parsed response."""
    body = {
        "agent": AGENT,
        "parts": [{"type": "text", "text": text}],
    }
    if MODEL:
        provider, model = MODEL.split("/", 1)
        body["model"] = {"providerID": provider, "modelID": model}
    # count messages before sending
    msgs = api("GET", f"/session/{session_id}/message")
    state.msg_count = len(msgs)
    api("POST", f"/session/{session_id}/prompt_async", body, expect_empty=True)
    assert wait_idle(session_id), "timed out waiting for LLM response"
    # fetch new messages
    msgs = api("GET", f"/session/{session_id}/message")
    new_msgs = msgs[state.msg_count:]
    parts = []
    for msg in new_msgs:
        if msg.get("info", {}).get("role") == "assistant":
            parts.extend(msg.get("parts", []))
    return {"parts": parts}

def format_diagnostics(calls, text, reasoning):
    """format response diagnostics for assertion messages."""
    lines = []
    tool_names = [c["tool"] for c in calls]
    lines.append(f"tools called: {tool_names}")
    for c in calls:
        lines.append(f"  {c['tool']} [{c['status']}]: {json.dumps(c['input'], ensure_ascii=False)[:200]}")
        if c["status"] == "error":
            lines.append(f"    error: {c['output'][:200]}")
    if text:
        lines.append(f"text: {text[:300]}")
    if reasoning:
        lines.append(f"reasoning: {reasoning[:500]}")
    return "\n".join(lines)

# --- core expansion (no tools, answer from system prompt) ---

class TestCoreExpansion:
    def test_agents_from_system_prompt(self, session_id, state):
        """LLM answers about plugins from inlined AGENTS trait without calling tools."""
        r = send_prompt(session_id, state, "what plugins are you built on? answer briefly, just name them.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert len(tool_names) == 0, f"expected no tool calls\n{diag}"
        assert "persona_trait_read" not in tool_names, f"should not read traits\n{diag}"
        assert re.search(r"(?i)evolve|bridge", text), f"expected mention of evolve or bridge\n{diag}"

# --- trait tools: create, list, read, edit, delete ---

class TestTraitLifecycle:
    def test_01_create(self, session_id, state):
        r = send_prompt(session_id, state, f"create a new trait called {TEST_TRAIT} with this exact content: {TEST_TRAIT_CONTENT}")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_trait_write" in tool_names, f"expected trait_write call\n{diag}"
        tw = [c for c in calls if c["tool"] == "persona_trait_write"][0]
        assert tw["input"].get("trait") == TEST_TRAIT, f"expected trait={TEST_TRAIT}\n{diag}"
        assert tw["status"] == "completed", f"expected completed status\n{diag}"

    def test_02_list_includes_created(self, session_id, state):
        r = send_prompt(session_id, state, "what traits do i have? list them all.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_trait_list" in tool_names, f"expected trait_list call\n{diag}"
        assert TEST_TRAIT in text, f"expected {TEST_TRAIT} in response\n{diag}"

    def test_03_read_returns_content(self, session_id, state):
        r = send_prompt(session_id, state, f"read the {TEST_TRAIT} trait and quote its full content back to me verbatim.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_trait_read" in tool_names, f"expected trait_read call\n{diag}"
        tr = [c for c in calls if c["tool"] == "persona_trait_read"][0]
        assert tr["input"].get("trait") == TEST_TRAIT, f"expected trait={TEST_TRAIT}\n{diag}"
        assert "eval test trait for verification" in text, f"expected content in response\n{diag}"

    def test_04_edit(self, session_id, state):
        r = send_prompt(session_id, state, f"edit {TEST_TRAIT} and append a new line: 'updated by eval harness'")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_trait_edit" in tool_names, f"expected trait_edit call\n{diag}"
        te = [c for c in calls if c["tool"] == "persona_trait_edit"][0]
        assert te["input"].get("trait") == TEST_TRAIT, f"expected trait={TEST_TRAIT}\n{diag}"
        assert te["status"] == "completed", f"expected completed status\n{diag}"

    def test_05_read_after_edit(self, session_id, state):
        r = send_prompt(session_id, state, f"read {TEST_TRAIT} again and quote its full content verbatim.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_trait_read" in tool_names, f"expected trait_read call\n{diag}"
        assert "updated by eval harness" in text, f"expected edited content in response\n{diag}"

    def test_06_delete(self, session_id, state):
        r = send_prompt(session_id, state, f"delete the {TEST_TRAIT} trait")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_trait_delete" in tool_names, f"expected trait_delete call\n{diag}"
        td = [c for c in calls if c["tool"] == "persona_trait_delete"][0]
        assert td["input"].get("trait") == TEST_TRAIT, f"expected trait={TEST_TRAIT}\n{diag}"
        assert td["status"] == "completed", f"expected completed status\n{diag}"

# --- task tools: create, query, count, delete ---

class TestTaskLifecycle:
    def test_01_create(self, session_id, state):
        r = send_prompt(session_id, state,
            f"create a task: {TEST_TASK_SUMMARY}. due {TEST_TASK_DUE}. tell me the task id from the response.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_task_create" in tool_names, f"expected task_create call\n{diag}"
        tc = [c for c in calls if c["tool"] == "persona_task_create"][0]
        assert tc["status"] == "completed", f"expected completed status\n{diag}"
        # parse the tool output and verify the LLM relays the actual id
        output = parse_tool_output(tc["output"])
        assert output.get("success") is True, f"expected success in tool output\n{diag}"
        task_id = output.get("id", "")
        assert task_id, f"expected id in tool output\n{diag}"
        assert task_id in text, f"expected LLM to relay task id {task_id}\n{diag}"

    def test_02_query_finds_created(self, session_id, state):
        r = send_prompt(session_id, state, "list my tasks. quote the summary of each one.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_data_query" in tool_names, f"expected data_query call\n{diag}"
        dq = [c for c in calls if c["tool"] == "persona_data_query"][0]
        assert dq["input"].get("trait") == ".tasks.json", f"expected trait=.tasks.json\n{diag}"
        assert "persona_task_list" not in tool_names, f"should not use removed task_list\n{diag}"
        assert "verify persona tools" in text, f"expected task summary in response\n{diag}"

    def test_03_filter_open(self, session_id, state):
        r = send_prompt(session_id, state, "show me only open tasks. quote their summaries.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_data_query" in tool_names, f"expected data_query call\n{diag}"
        assert "verify persona tools" in text, f"expected task summary in response\n{diag}"

    def test_04_filter_by_due_date(self, session_id, state):
        r = send_prompt(session_id, state, "what tasks are due before 2100-01-01T00:00:00.000+00:00? quote each summary.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_data_query" in tool_names, f"expected data_query call\n{diag}"
        assert "verify persona tools" in text, f"expected task summary in response\n{diag}"

    def test_05_count_by_status(self, session_id, state):
        r = send_prompt(session_id, state, "how many tasks do i have in each status? give me the counts.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_data_count" in tool_names, f"expected data_count call\n{diag}"
        dc = [c for c in calls if c["tool"] == "persona_data_count"][0]
        assert dc["input"].get("trait") == ".tasks.json", f"expected trait=.tasks.json\n{diag}"
        assert dc["input"].get("field") == "status", f"expected field=status\n{diag}"

    def test_06_delete(self, session_id, state):
        r = send_prompt(session_id, state,
            "find the task with summary containing 'verify persona tools' and delete it from .tasks.json using data_delete")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_data_delete" in tool_names, f"expected data_delete call\n{diag}"
        dd = [c for c in calls if c["tool"] == "persona_data_delete"][0]
        assert dd["input"].get("trait") == ".tasks.json", f"expected trait=.tasks.json\n{diag}"
        assert dd["status"] == "completed", f"expected completed status\n{diag}"
        assert "persona_task_delete" not in tool_names, f"should not use removed task_delete\n{diag}"

# --- journal (record) tools: append, query, count ---

class TestJournalLifecycle:
    def test_01_append(self, session_id, state):
        r = send_prompt(session_id, state, f"add a journal observation: {TEST_JOURNAL_CONTENT}")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_record_append" in tool_names, f"expected record_append call\n{diag}"
        ra = [c for c in calls if c["tool"] == "persona_record_append"][0]
        assert ra["input"].get("trait") == ".journal.jsonl", f"expected trait=.journal.jsonl\n{diag}"
        assert ra["status"] == "completed", f"expected completed status\n{diag}"
        assert "persona_journal_append" not in tool_names, f"should not use removed journal_append\n{diag}"

    def test_02_query_finds_entry(self, session_id, state):
        r = send_prompt(session_id, state, "search my journal for entries about sky. quote the matching content.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_record_query" in tool_names, f"expected record_query call\n{diag}"
        rq = [c for c in calls if c["tool"] == "persona_record_query"][0]
        assert rq["input"].get("trait") == ".journal.jsonl", f"expected trait=.journal.jsonl\n{diag}"
        assert "persona_journal_list" not in tool_names, f"should not use removed journal_list\n{diag}"
        assert "blue" in text.lower(), f"expected 'blue' in response\n{diag}"

    def test_03_count(self, session_id, state):
        r = send_prompt(session_id, state, "how many journal entries do i have? give me the exact number.")
        calls = extract_tool_calls(r)
        text = extract_text(r)
        reasoning = extract_reasoning(r)
        diag = format_diagnostics(calls, text, reasoning)
        tool_names = [c["tool"] for c in calls]
        assert "persona_record_count" in tool_names, f"expected record_count call\n{diag}"
        rc = [c for c in calls if c["tool"] == "persona_record_count"][0]
        assert rc["input"].get("trait") == ".journal.jsonl", f"expected trait=.journal.jsonl\n{diag}"
        assert "persona_journal_count" not in tool_names, f"should not use removed journal_count\n{diag}"
