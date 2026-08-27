#!/usr/bin/env python3
"""eval harness: drive the persona agent over the hmux NATIVE hub api and verify
its tool calls + text.

the eval is a native hmux CLIENT (see hub.py): it connects to the hub over a
websocket, creates and drives a session, and reads the normalized session.event
stream -- no opencode-wire face in between. it works against any hmux backend
(pi, opencode, ...) persona runs behind the hub.

usage:
    make eval
    HUB_URL=ws://host:4280/ws BACKEND_MODEL=kairos/qwen3.5-9b:Q8_0 pytest evals/persona_eval.py -v

environment:
    HUB_URL          hub websocket url (default ws://127.0.0.1:4280/ws)
    BACKEND_AGENT    agent name (default: per; a no-op for the pi backend, which picks
                     its agent out-of-band, but honored by backends that read it)
    BACKEND_MODEL    model id "provider/id", e.g. kairos/qwen3.5-9b:Q8_0
    REASONING        thinking level applied per session (default: off; e.g. low/medium/high)
    PROMPT_TIMEOUT   seconds to wait for one turn to finish (default 600)
    BROWSER_PROMPT_TIMEOUT
                     the same, for the browsing turns, which are a dozen live page loads
                     long (default 1800)
"""

import json, os, re, sys, time, warnings
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hub import HubClient

HUB_URL = os.environ.get("HUB_URL", "ws://127.0.0.1:4280/ws")
AGENT = os.environ.get("BACKEND_AGENT", "per")
MODEL = os.environ.get("BACKEND_MODEL", "")
# thinking level, pinned per session. off is the calibrated default: reasoning makes
# models substitute their own reasoned-out reply for the injected persona instructions,
# which tanks instruction-following (qwen3.6-35b-a3b: 29/31 at off vs 14/31 at high).
REASONING = os.environ.get("REASONING", "off")
# hard per-turn wall-clock cap. covers a cold model load (~2min) + a normal turn; a runaway
# generation is aborted at the cap (send_prompt then session.aborts it, stopping the event
# flood) rather than allowed to accumulate unboundedly and peg CPU/memory.
PROMPT_TIMEOUT = int(os.environ.get("PROMPT_TIMEOUT", "600"))
# BROWSING is a different order of turn: each step is a real page load plus a model decision, so
# "summarize three comment threads" is a dozen round trips against a live site. one such turn hit
# the 600s cap with the model mid-task, having already visited all three threads -- a failure that
# says nothing about the agent. raising the GLOBAL cap instead would blunt the runaway guard for
# every other test, where 600s already means something is wrong.
BROWSER_PROMPT_TIMEOUT = int(os.environ.get("BROWSER_PROMPT_TIMEOUT", "1800"))
# a turn producing this many stream events is a runaway (a normal turn, even a long reasoning
# one, is a few thousand at most); abort it immediately so its flood cannot exhaust memory.
RUNAWAY_MAX_EVENTS = int(os.environ.get("RUNAWAY_MAX_EVENTS", "20000"))

POLL_INTERVAL = 0.1

# the module-wide client (set by the `hub` fixture) that send_prompt drives, plus the
# set of interceptor request ids already resolved (idempotent across turns).
_CLIENT = None
_RESOLVED = set()

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

# --- native hub helpers ---

def _wait(pred, timeout_s):
    """poll a predicate until true or the deadline passes."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False

def _model_ref(spec):
    """'kairos/qwen3.5-9b:Q8_0' -> {'provider':'kairos','id':'qwen3.5-9b:Q8_0'} (split on first '/')."""
    provider, _, bare = spec.partition("/")
    return {"provider": provider, "id": bare}

def _online_backend(client):
    """the first online harness registered with the hub, or None."""
    for h in client.request("harness.list"):
        if h.get("online"):
            return h
    return None

# --- interceptor auto-approval ---

# persona gates SOUL/core-trait writes with an `ask`, which the harness raises as an hmux
# interception (an elicitation for persona's own asks; a permission gate if a client armed
# tool gating). the hub routes it to subscribed clients; the eval is that client and
# approves each one so a turn never wedges. a turn that raises nothing makes this a no-op.

def _approve_option(opts):
    """the id of the approving choice for a select: prefer a session-wide yes, then any
    yes/allow/approve/trust option, then the first."""
    if not opts:
        return None
    label = lambda o: o.get("label") or o.get("id") or ""
    for pat in (r"for this session", r"^(yes|allow|approve|trust)"):
        for o in opts:
            if re.search(pat, label(o), re.I):
                return o.get("id")
    return opts[0].get("id")

def _elicit_answer(el):
    """an ElicitAnswer approving/answering an elicitation of any kind."""
    kind = el.get("kind")
    if kind in ("select", "multiselect"):
        chosen = _approve_option(el.get("options") or [])
        return {"selected": [chosen] if chosen else []}
    if kind == "confirm":
        return {"confirmed": True}
    if kind == "input":
        return {"text": el.get("default") or ""}
    return {}

def resolve_pending(client, sid):
    """approve every pending permission gate and answer every elicitation for the session.
    idempotent by request id (continue == allow; a replace answers a question)."""
    for _m, p in client.notifications("interceptor.request"):
        if not p or p.get("session_id") != sid:
            continue
        rid = p.get("request_id")
        if rid in _RESOLVED:
            continue
        hook = p.get("hook")
        if hook == "permission":
            decision = {"kind": "continue"}
        elif hook == "elicitation":
            el = (p.get("options") or {}).get("elicitation") or p.get("payload") or {}
            decision = {"kind": "replace", "payload": _elicit_answer(el)}
        else:
            continue  # loop-internal mutation hooks are not armed by the eval
        try:
            client.request("interceptor.resolve", {"request_id": rid, "decision": decision})
        except Exception:
            pass
        _RESOLVED.add(rid)

def _status_events(client, sid):
    """this session's status.changed params, in arrival order."""
    return [p for (_m, p) in client.notifications("status.changed")
            if p and p.get("session_id") == sid]

def wait_turn(client, sid, since_status, timeout_s=None):
    """drive one turn to completion: resolve interceptions as they arrive and return once
    the session's status has gone processing -> idle (R16, authoritative). the status
    slice starts at `since_status` so a later prompt on the same session is not tripped by
    a previous turn's idle. returns True on completion, False on timeout.

    `timeout_s` overrides PROMPT_TIMEOUT for a turn that is legitimately long (browsing)."""
    deadline = time.time() + (timeout_s or PROMPT_TIMEOUT)
    events_before = client.notification_count("session.event")
    while time.time() < deadline:
        resolve_pending(client, sid)
        # runaway guard: a turn flooding tens of thousands of stream events is looping; abort
        # it at once so its flood cannot exhaust memory, rather than waiting out PROMPT_TIMEOUT.
        if client.notification_count("session.event") - events_before > RUNAWAY_MAX_EVENTS:
            try:
                client.request("session.abort", {"session_id": sid}, timeout=5)
            except Exception:
                pass
            return False
        st = _status_events(client, sid)[since_status:]
        if any((s.get("status") or {}).get("state") == "processing" for s in st) \
                and st and (st[-1].get("status") or {}).get("state") == "idle":
            resolve_pending(client, sid)  # drain a gate raced with the idle transition
            return True
        time.sleep(POLL_INTERVAL)
    return False

# --- response parsing ---

class Response:
    """a turn's normalized result: tool calls, text, reasoning, and the raw events."""
    def __init__(self, events):
        # the same tool call streams pending->running->completed, so keep the last state
        # per call id while preserving first-seen order.
        calls, order = {}, []
        text = reasoning = ""
        for p in events:
            ev = p.get("event", {})
            kind = ev.get("type")
            if kind == "tool_update":
                tc = ev.get("tool_call", {})
                cid = tc.get("id")
                if cid not in calls:
                    order.append(cid)
                out = tc.get("output")
                calls[cid] = {
                    "tool": tc.get("name", ""),
                    "input": tc.get("input") or {},
                    "output": out if isinstance(out, str) else ("" if out is None else json.dumps(out)),
                    "status": tc.get("status", "unknown"),
                }
            elif kind == "text_delta":
                text += ev.get("delta", "")
            elif kind == "reasoning_delta":
                reasoning += ev.get("delta", "")
        self.calls = [calls[c] for c in order]
        self.text = text
        self.reasoning = reasoning
        self.parts = [p.get("event", {}) for p in events]  # raw events, for diagnostics

    @property
    def diag(self):
        return format_diagnostics(self.calls, self.text, self.reasoning, self.parts)

    def tool_output(self, index):
        """parse the JSON output of the tool call at index."""
        raw = self.calls[index]["output"] if index < len(self.calls) else ""
        return parse_tool_output(raw)

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
        # the FULL reasoning trace, so a turn that reasoned but never produced a final
        # answer/tool (common on reasoning models at high thinking) is legible in the log.
        lines.append(f"reasoning ({len(reasoning)} chars): {reasoning}")
    if parts:
        # a compact tally of the raw event stream, not a dump of every delta.
        tally = {}
        for p in parts:
            tally[p.get("type", "?")] = tally.get(p.get("type", "?"), 0) + 1
        lines.append("events: " + ", ".join(f"{k}x{n}" for k, n in sorted(tally.items())))
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

# --- session fixtures ---

@pytest.fixture(scope="session")
def hub():
    """the hub client, connected once and shared. waits for a backend to come online,
    then hands control to send_prompt via the module _CLIENT."""
    global _CLIENT
    try:
        client = HubClient(HUB_URL)
    except Exception as e:
        pytest.skip(f"hub unreachable at {HUB_URL}: {e}")
    if not _wait(lambda: _online_backend(client) is not None, 30):
        client.close()
        pytest.skip("no hmux harness came online")
    _CLIENT = client
    yield client
    _CLIENT = None
    client.close()

@pytest.fixture(scope="module")
def session_id(hub):
    """one session for the module, subscribed (so interceptions + status reach us) and
    pinned to the model under test. tests share the on-disk workspace across turns."""
    sid = hub.request("session.create", {"harness_id": None, "opts": {"agent": AGENT}})["id"]
    hub.request("session.subscribe", {"session_id": sid, "since_seq": None})
    if MODEL:
        hub.request("session.set_model", {"session_id": sid, "model": _model_ref(MODEL)})
    if REASONING:
        try:  # non-reasoning models have no levels; leave them at default.
            hub.request("session.set_reasoning", {"session_id": sid, "level": REASONING})
        except Exception:
            pass
    return sid

class SessionState:
    pass  # retained for the send_prompt signature; per-turn state now lives in the events.

@pytest.fixture(scope="module")
def state():
    return SessionState()

def send_prompt(session_id, state, text, timeout_s=None):
    """send a prompt, drive the turn to completion (auto-approving gates/questions), and
    return the Response built from this turn's normalized events. a turn that yields no
    tool call and no final text -- whether it TIMED OUT or REASONED WITHOUT CONCLUDING --
    fails with the full diagnostics (reasoning trace + event tally included), so the log
    always shows what the model actually did rather than a bare 'no response'."""
    client = _CLIENT
    ev_before = len(client.session_events(session_id))
    st_before = len(_status_events(client, session_id))
    client.request("session.prompt", {"session_id": session_id, "prompt": {"text": text, "files": []}})
    done = wait_turn(client, session_id, st_before, timeout_s)
    r = Response(client.session_events(session_id)[ev_before:])
    if not done:
        try:  # stop a runaway turn so it does not bleed into the next test on this session.
            client.request("session.abort", {"session_id": session_id}, timeout=5)
        except Exception:
            pass
        pytest.fail(f"timed out after {timeout_s or PROMPT_TIMEOUT}s (turn never finished)\n{r.diag}")
    if not r.calls and not r.text.strip():
        notices = [e.get("text", "") for e in r.parts if e.get("type") == "notice"]
        note = f"; notices: {notices}" if notices else ""
        pytest.fail(f"no final answer: turn produced no tool call and no text{note}\n{r.diag}")
    return r

# === eval tests ===

# --- bootstrap onboarding (must run first) ---

# BOOTSTRAP.md is a workspace seed file (see traits/BOOTSTRAP.md): it prompts
# a newly-born persona to ask a fixed set of questions, save answers to
# SOUL.md + USER.md, then delete itself. this eval assumes the trait is
# already present on disk — the eval does not seed it, because coaching the
# agent through a write would taint the onboarding flow we're measuring.
# consequence: this class is single-shot. once it passes, BOOTSTRAP.md is
# gone and subsequent runs will fail test_01 until the workspace is reseeded
# out-of-band.

# single reply that answers every question from BOOTSTRAP.md in one shot.
# intentionally free-form prose: we want to verify persona follows BOOTSTRAP.md's
# instructions on its own (which traits to update, when to delete the file),
# not that it can obey explicit per-call coaching from the user.
BOOTSTRAP_ANSWERS = (
    "answers to all your questions, in order. "
    "about you: be a curious, action-oriented mind; the values that matter "
    "most are honesty, clarity, and kindness; explore software, philosophy, "
    "and systems thinking first; grow through reflection on what you do and "
    "steady refinement over time; your name is Kestrel. "
    "about me: call me Pat; i'm a software engineer working on AI collaboration "
    "tools; i hope you become a thoughtful long-running collaborator; "
    "i prefer brief and casual communication; avoid deeply personal topics "
    "and don't commit or push code without asking."
)

def _has_call(calls, tool_names, trait):
    """true if any actual call writes/appends/deletes the given trait via one of tool_names."""
    names = tool_names if isinstance(tool_names, (list, tuple, set)) else (tool_names,)
    return any(c["tool"] in names and c["input"].get("trait") == trait for c in calls)

class TestBootstrap:
    """verify persona's onboarding flow: sees BOOTSTRAP.md, asks its questions,
    then on receiving answers writes SOUL.md + USER.md and deletes BOOTSTRAP.md."""

    def test_01_greeting_triggers_questions(self, session_id, state):
        """a fresh greeting should make persona recognize BOOTSTRAP.md and ask
        at least one of its onboarding questions rather than just greeting back."""
        r = send_prompt(session_id, state, "hello")
        # persona should NOT have written SOUL/USER or deleted BOOTSTRAP yet —
        # the first turn is for asking, not completing.
        assert not _has_call(r.calls, ("persona_trait_delete",), "BOOTSTRAP.md"), (
            f"persona deleted BOOTSTRAP.md before gathering any answers\n{r.diag}")
        assert not _has_call(r.calls, ("persona_trait_write", "persona_trait_append"), "SOUL.md"), (
            f"persona wrote SOUL.md before receiving answers\n{r.diag}")
        assert not _has_call(r.calls, ("persona_trait_write", "persona_trait_append"), "USER.md"), (
            f"persona wrote USER.md before receiving answers\n{r.diag}")
        # text should contain a question directed at the user. BOOTSTRAP.md's
        # questions cover: name, values, topics, growth, what to call them,
        # their work, hopes, communication style, boundaries.
        assert "?" in r.text, f"expected persona to ask a question\n{r.diag}"
        assert_text(r, r"name|call (you|me)|values|explore|communicate|boundaries|work on|become")

    def test_02_answers_complete_onboarding(self, session_id, state):
        """once the user answers, persona must persist SOUL.md and USER.md and
        delete BOOTSTRAP.md so it won't re-run onboarding next session."""
        r = send_prompt(session_id, state, BOOTSTRAP_ANSWERS)
        soul_written = _has_call(
            r.calls, ("persona_trait_write", "persona_trait_append", "persona_trait_edit"), "SOUL.md")
        user_written = _has_call(
            r.calls, ("persona_trait_write", "persona_trait_append", "persona_trait_edit"), "USER.md")
        bootstrap_deleted = _has_call(r.calls, ("persona_trait_delete",), "BOOTSTRAP.md")
        assert soul_written, f"expected SOUL.md to be written with persona answers\n{r.diag}"
        assert user_written, f"expected USER.md to be written with user answers\n{r.diag}"
        assert bootstrap_deleted, f"expected BOOTSTRAP.md to be deleted after onboarding\n{r.diag}"
        # BOOTSTRAP.md instructs the delete be the LAST step. guard against the
        # order getting inverted (delete first, then write — which would leave
        # onboarding half-done if the session crashed between the two).
        delete_idx = next(i for i, c in enumerate(r.calls)
                          if c["tool"] == "persona_trait_delete"
                          and c["input"].get("trait") == "BOOTSTRAP.md")
        last_soul_idx = max(
            (i for i, c in enumerate(r.calls)
             if c["tool"] in ("persona_trait_write", "persona_trait_append", "persona_trait_edit")
             and c["input"].get("trait") == "SOUL.md"),
            default=-1)
        last_user_idx = max(
            (i for i, c in enumerate(r.calls)
             if c["tool"] in ("persona_trait_write", "persona_trait_append", "persona_trait_edit")
             and c["input"].get("trait") == "USER.md"),
            default=-1)
        assert delete_idx > last_soul_idx and delete_idx > last_user_idx, (
            f"BOOTSTRAP.md must be deleted after writing SOUL.md and USER.md\n{r.diag}")

# --- core expansion (answer from system prompt, no tools) ---

class TestCoreExpansion:
    def test_agents_from_system_prompt(self, session_id, state):
        """LLM answers about plugins from inlined AGENTS trait without tool calls."""
        r = send_prompt(session_id, state, "what plugins are you built on? answer briefly, just name them.")
        assert_calls(r, [], also=[
            {"tool": "persona_trait_read", "args": {"trait": "AGENTS.md"}},
        ])
        assert_text(r, r"(?i)hcp|bridge")

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

    def test_06_move(self, session_id, state):
        r = send_prompt(session_id, state,
            f"rename the trait {TEST_TRAIT} to {TEST_TRAIT_RENAME}")
        assert_calls(r, [
            {"tool": "persona_trait_move", "args": {"old_trait": TEST_TRAIT, "new_trait": TEST_TRAIT_RENAME}, "output": {"success": True}},
        ], also=[
            {"tool": "persona_trait_read", "args": {"trait": TEST_TRAIT_RENAME}},
        ])

    def test_07_read_after_move(self, session_id, state):
        r = send_prompt(session_id, state,
            f"read {TEST_TRAIT_RENAME} and quote its full content verbatim.")
        assert_calls(r, [
            {"tool": "persona_trait_read", "args": {"trait": TEST_TRAIT_RENAME}},
        ])
        assert_text(r, "updated by eval harness")

    def test_08_delete(self, session_id, state):
        r = send_prompt(session_id, state, f"delete the {TEST_TRAIT_RENAME} trait")
        assert_calls(r, [
            {"tool": "persona_trait_delete", "args": {"trait": TEST_TRAIT_RENAME}, "output": {"success": True}},
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
            C("persona_data_update", args={"trait": ".tasks.json"}),
        ], also=[
            C("persona_data_query", args={"trait": ".tasks.json"}),
        ])

    def test_08_delete_all(self, session_id, state):
        r = send_prompt(session_id, state,
            "delete all remaining tasks from .tasks.json")
        assert_calls(r, [
            C("persona_data_update", args={"trait": ".tasks.json"})
            | C("persona_trait_delete", args={"trait": ".tasks.json"}, output={"success": True}),
        ], also=[
            C("persona_data_query", args={"trait": ".tasks.json"}),
            C("persona_data_update", args={"trait": ".tasks.json"}),
            C("persona_data_count", args={"trait": ".tasks.json"}),
        ])

# --- recurring task: create, do work (auto-bump), delete ---

class TestRecurringTask:
    def test_01_create(self, session_id, state):
        r = send_prompt(session_id, state,
            f"create a recurring task: {TEST_RECURRING_SUMMARY}. due {TEST_RECURRING_DUE}, repeats every {TEST_RECURRING_INTERVAL}. do not start work yet. tell me the task id.")
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
            # accept either a single write OR the first of N incremental appends.
            # extra appends for poems.md are whitelisted in `also` below so the
            # LLM can build the poem line-by-line without tripping the count check.
            C("persona_trait_write", args={"trait": "poems.md"}, output={"success": True})
            | C("persona_trait_append", args={"trait": "poems.md"}, output={"success": True}),
            C("persona_task_comment", output={"success": True}),
        ], also=[
            C("persona_data_query", args={"trait": ".tasks.json"}),
            C("persona_data_count", args={"trait": ".tasks.json"}),
            C("persona_datetime"),
            C("persona_trait_read", args={"trait": "poems.md"}),
            C("persona_trait_append", args={"trait": "poems.md"}, output={"success": True}),
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
            {"tool": "persona_data_update", "args": {"trait": ".tasks.json"}},
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": ".tasks.json"}},
        ])

# --- structured data tools: update, append, query, delete ---

class TestDataLifecycle:
    def test_01_update_create(self, session_id, state):
        """create a .json trait by setting a field via $set."""
        r = send_prompt(session_id, state,
            f"set the value of 'color' to 'blue' in the {TEST_DATA_TRAIT} trait")
        assert_calls(r, [
            {"tool": "persona_data_update",
             "args": {"trait": TEST_DATA_TRAIT, "ops": {"$set": {"color": "blue"}}},
             "output": {"success": True}},
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": TEST_DATA_TRAIT}},
        ])

    def test_02_update_second_field(self, session_id, state):
        r = send_prompt(session_id, state,
            f"also set 'size' to the string 'large' in {TEST_DATA_TRAIT}")
        assert_calls(r, [
            {"tool": "persona_data_update",
             "args": {"trait": TEST_DATA_TRAIT, "ops": {"$set": {"size": "large"}}},
             "output": {"success": True}},
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
        """append 'eval' to a tags array via $push (auto-creates the array)."""
        r = send_prompt(session_id, state,
            f"append 'eval' to the 'tags' array in {TEST_DATA_TRAIT}")
        assert_calls(r, [
            C("persona_data_update",
              args={"trait": TEST_DATA_TRAIT, "ops": {"$push": {"tags": "eval"}}},
              output={"success": True})
            | C("persona_data_update",
                args={"trait": TEST_DATA_TRAIT, "ops": {"$push": {"tags": {"$each": ["eval"]}}}},
                output={"success": True}),
        ], also=[
            {"tool": "persona_data_query", "args": {"trait": TEST_DATA_TRAIT}},
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
            {"tool": "persona_data_update", "args": {"trait": TEST_DATA_TRAIT}},
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
            "search my journal for all entries about sky — there may be older ones i've forgotten. quote each matching entry.")
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

# the bridge eval runs only when the harness wires a fake-matrix into the container's bridge
# --- host-authored reminders (hmux phase 42) ---

def inject_note(session_id, content):
    """queue a host-authored note for the session's next turn.

    the hub composes it into a `<system_reminder>` block ahead of the user's words -- the same
    path a trait change or a recorded bridge send takes. any client may queue one; naming the
    SPEAKER or the reply format is gated to allowlisted faces, so those two blocks are covered
    by hmux's own e2e rather than here."""
    _CLIENT.request("session.inject", {
        "session_id": session_id, "content": content, "mode": "next_turn",
    })


class TestSystemReminders:
    """what the reminders are FOR: the hub can put them in front of the model, but only the model
    can honor them, and no unit test can show that it does.

    the legend hmux appends to the system prompt makes three promises about a `<system_reminder>`
    block -- read it, never reply to it, never reproduce it. one test each, plus the thing the
    whole mechanism exists for: out-of-band news reaching a turn the user drove."""

    def test_01_a_note_reaches_the_model(self, session_id, state):
        # the point of the whole mechanism: something that happened OUTSIDE the conversation is
        # known to the agent on its next turn, without the user having said it.
        inject_note(session_id, "FYI: the nightly backup job failed at 02:14 with a disk error.")
        r = send_prompt(session_id, state, "anything i should know about overnight?")
        assert_text(r, r"(?i)backup")
        assert_text(r, r"(?i)disk|02:14|fail")

    def test_02_the_reminder_is_not_reproduced(self, session_id, state):
        # the legend says never reproduce it. a model that echoes the block back shows a human
        # raw markup they were never meant to see -- and on the bridge, sends it to matrix.
        inject_note(session_id, "FYI: the disk was replaced and the backup succeeded on retry.")
        r = send_prompt(session_id, state, "any update on that backup?")
        for markup in ("<system_reminder>", "</system_reminder>", "system_reminder"):
            assert markup not in r.text, (
                f"the model reproduced host markup ({markup!r}) into its reply\n{r.diag}"
            )

    def test_03_the_note_is_not_answered_instead_of_the_user(self, session_id, state):
        # the block rides INSIDE the user's turn (no provider takes a third role mid-conversation),
        # so the failure mode is the model treating it as what the user said and answering it
        # instead. the user's actual question must win.
        inject_note(session_id, "FYI: the office wifi password rotated to `hunter2-2026`.")
        r = send_prompt(session_id, state, "what is 17 times 3? just the number.")
        assert_text(r, r"51")
        assert "hunter2" not in r.text, (
            f"the model answered the reminder instead of the user's question\n{r.diag}"
        )


# (`make eval-bridge` sets EVAL_BRIDGE here + HMUX_BRIDGE_* on the container). without it the bridge
# idles and never registers its tools, so there is nothing for these to exercise.
BRIDGE_ENABLED = bool(os.environ.get("EVAL_BRIDGE"))


@pytest.mark.skipif(not BRIDGE_ENABLED, reason="bridge not wired into the eval (see `make eval-bridge`)")
class TestBridgeMessaging:
    """Per's matrix bridge: it can enumerate the rooms it is bridged into and message through them.
    the harness points the container's bridge at a fake homeserver, so the sends are real (captured
    by the fake) without touching a live matrix account."""

    def test_01_list_rooms(self, session_id, state):
        r = send_prompt(session_id, state, "which matrix chats are you connected to right now?")
        assert_calls(r, [{"tool": "bridge_rooms"}])
        assert_text(r, r"(?i)tester|dm|room|matrix")

    def test_02_send_message(self, session_id, state):
        r = send_prompt(session_id, state,
            "let tester know on matrix that you'll be around this evening.")
        assert_calls(r, [
            C("bridge_send_direct", {"username": "tester"}) | C("bridge_send"),
        ])


class TestBrowserUse:
    def test_01_start_session(self, session_id, state):
        r = send_prompt(session_id, state, "start a headless browser session now",
                        timeout_s=BROWSER_PROMPT_TIMEOUT)
        assert_bash_sequence(r, [
            r"browser-head start",
        ])

    def test_02_navigate_hackernews(self, session_id, state):
        r = send_prompt(session_id, state, "go to https://news.ycombinator.com",
                        timeout_s=BROWSER_PROMPT_TIMEOUT)
        assert_bash_sequence(r, [
            r"browser-use.*open.*https://news\.ycombinator\.com",
        ])

    def test_03_summarize_top_comments(self, session_id, state):
        """extract links, visit 3 comment threads, summarize to a trait."""
        r = send_prompt(session_id, state,
            "visit the comment threads for the top 3 stories on the page. "
            "for each one, write a one-paragraph summary of the discussion to the research_notes.md trait.",
            timeout_s=BROWSER_PROMPT_TIMEOUT)
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
        r = send_prompt(session_id, state, "delete the research_notes.md trait",
                        timeout_s=BROWSER_PROMPT_TIMEOUT)
        assert_calls(r, [
            {"tool": "persona_trait_delete", "args": {"trait": "research_notes.md"}, "output": {"success": True}},
        ])

