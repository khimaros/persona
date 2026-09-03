#!/usr/bin/env python3
"""end-to-end tests for the persona hook dispatcher (JSONL IPC, both directions).

WHAT THIS FILE STOPPED TESTING (hmux phase 74). it was 94KB, and most of it covered a storage
engine persona no longer has: trait/document/record tools, a mongo filter evaluator, a dot-path
updater. all of that moved into `hmux-memory`, where it is covered by 61 rust tests -- cheaper, and
exhaustive in a way a subprocess-per-case suite could never be.

WHAT IS LEFT IS WHAT PERSONA ACTUALLY DECIDES, and it is worth testing precisely because it is
small: how a system prompt is composed from files, what a task is, and the ORDER of the two writes
a comment makes. the store is a fake here ON PURPOSE -- this suite asserts persona's policy, and
the endpoint's own contract is hmux's to keep (`e2e/memory_face_test.py`, `crates/hmux-memory`).

the hook is driven the way the host drives it: argv names the stage, one json object on stdin,
JSONL on stdout.

AND THE HOST NOW ANSWERS BACK (hmux phase 76e/76f). persona asks memory its questions over the
pipes hcp already holds open, so this file no longer stands up an http server on a thread to be
asked -- it answers on the same stdin it wrote the payload to. `FakeMemory` went from a
`BaseHTTPRequestHandler` with four verb methods, a port and a background thread to a dict and a
`def answer(method, args)`. THE TEST IS NOW A SMALL MODEL OF THE HOST, which is the honest shape:
what it exercises is exactly the loop hcp runs.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "persona.py"
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


# the traits face's root and the rule it applies. MIRRORED HERE RATHER THAN IMPORTED because this
# fake stands in for a rust face; if the two ever disagree the e2e is what says so.
TRAITS_ROOT = "traits"


def _inlined(name):
    """the visibility rule: ALLCAPS is inlined, anything else is named (hmux phase 82)."""
    leaf = name.rsplit("/", 1)[-1]
    stem = leaf.split(".")[0]
    return not leaf.startswith(".") and any(c.isalpha() for c in stem) and stem == stem.upper()


class FakeMemory:
    """the subset of the workspace AND traits faces persona uses, plus a record of what it asked.

    DELIBERATELY NOT A REIMPLEMENTATION of the store: it holds what it is told and hands it back.
    what it is really for is the CALL LOG -- the order and the shape of what persona asks, which is
    the half no rust test can see.

    THE TRAITS ARMS ARE A TRANSLATION AND NOT A SECOND STORE, which is what the real face is too:
    a trait NAME becomes `traits/<name>` and the workspace arms below answer it. A fake that held
    its own trait storage could pass while the real hop was broken.
    """

    def __init__(self, entries=None, docs=None):
        self.entries = entries or []          # [{path, content}]
        self.docs = docs or {}                # path -> {key: value}
        self.log = []                         # [(method, args)]

    def answer(self, kind, method, args):
        """one `spoke.call`, answered. raises to mean the spoke refused."""
        self.log.append((method, args))
        if kind == "traits":
            return self._traits(method, args)
        return self._workspace(method, dict(args))

    def _traits(self, method, args):
        """the traits face: put the root on, then let the workspace arms do the work."""
        args = dict(args)
        name = args.pop("trait", "")
        if method == "traits.index":
            core, listed = [], []
            for e in sorted(self.entries, key=lambda e: e.get("path", "")):
                path = e.get("path", "")
                if not path.startswith(f"{TRAITS_ROOT}/"):
                    continue
                short = path[len(TRAITS_ROOT) + 1 :]
                if short.startswith("."):
                    continue
                if _inlined(short):
                    core.append({"name": short, "content": e.get("content", "")})
                else:
                    listed.append(short)
            return {"core": core, "listed": listed}
        args["path"] = f"{TRAITS_ROOT}/{name}"
        forward = {
            "trait_read": "workspace.read", "trait_write": "workspace.write",
            "trait_append": "workspace.append", "trait_edit": "workspace.edit",
            "trait_delete": "workspace.delete", "trait_data_query": "workspace.read",
            "trait_data_update": "workspace.update",
        }.get(method)
        if not forward:
            raise KeyError(f"`{method}` is not a trait tool")
        return self._workspace(forward, args)

    def _workspace(self, method, args):
        path = args.get("path", "")
        if method == "workspace.list":
            prefix, inline = args.get("prefix"), bool(args.get("content"))
            out = []
            for e in self.entries:
                if prefix and not e.get("path", "").startswith(prefix):
                    continue
                row = {k: v for k, v in e.items() if k != "content"}
                if inline:
                    row["content"] = e.get("content", "")
                out.append(row)
            return {"seq": len(self.log), "entries": out}
        if method == "workspace.read":
            if path in self.docs:
                doc = self.docs[path]
                key = args.get("key")
                return doc.get(key, {}) if key else doc
            for e in self.entries:
                if e.get("path") == path:
                    # TEXT, NOT BYTES -- the fabric's shape, which is what persona now decodes.
                    return {"path": path, "text": e.get("content", "")}
            raise KeyError(f"`{path}` does not exist")
        if method == "workspace.write":
            found = next((e for e in self.entries if e.get("path") == path), None)
            if found:
                found["content"] = args.get("content", "")
            else:
                self.entries.append({"path": path, "visibility": "listed",
                                     "content": args.get("content", ""), "rev": 1})
            return {"path": path, "rev": 1}
        if method == "workspace.update":
            doc = self.docs.setdefault(path, {})
            for dotted, value in ((args.get("ops") or {}).get("$set") or {}).items():
                head, _, tail = dotted.partition(".")
                if tail:
                    doc.setdefault(head, {})[tail] = value
                else:
                    doc[head] = value
            return {"path": path, "rev": 1}
        if method == "workspace.edit":
            for e in self.entries:
                if e.get("path") == path:
                    old, new = args.get("old", ""), args.get("new", "")
                    if old not in (e.get("content") or ""):
                        raise KeyError(f"`{old}` not found in `{path}`")
                    e["content"] = e["content"].replace(old, new, 1)
                    return {"path": path, "rev": 1}
            raise KeyError(f"`{path}` does not exist")
        if method == "workspace.delete":
            before = len(self.entries)
            self.entries = [e for e in self.entries if e.get("path") != path]
            if len(self.entries) == before:
                raise KeyError(f"`{path}` does not exist")
            return {"path": path, "rev": 1}
        if method == "workspace.append":
            found = next((e for e in self.entries if e.get("path") == path), None)
            if found is not None and args.get("text") is not None:
                found["content"] = (found.get("content") or "") + args["text"]
                return {"path": path, "rev": 1}
            return {"path": path, "rev": 1, "duplicate": False}
        raise KeyError(f"`{method}` is not a workspace method")

    def calls(self, method=None):
        return [c for c in self.log if method is None or c[0] == method]

    def close(self):
        """NOTHING TO CLOSE ANY MORE -- it was a socket and a thread, and it is a dict. kept so
        the callers' `finally:` blocks stay honest about owning the fake's lifetime."""


class Unreachable:
    """a store that refuses everything, for the degraded-prompt case. THE POINT IS THE SENTENCE:
    a session with no soul must say so rather than silently keeping the backend's default."""

    log = []

    def answer(self, kind, method, args):
        raise KeyError(f"no spoke of kind `{kind}` is connected")

    def calls(self, method=None):
        return []

    def close(self):
        pass


def call_hook(stage, ctx=None, memory=None):
    """drive one stage the way hcp does, INCLUDING ANSWERING ITS QUESTIONS.

    this is the duplex loop in fifteen lines: write the payload as one line, then read stdout --
    a `{"id", "call"}` line is a question to answer on stdin, anything else is a result. it is
    deliberately the same switch `hooks.rs` runs, because a fake that answered a DIFFERENT shape
    would pass while the real host failed.
    """
    full = dict(ctx or {})
    full["host"] = dict(full.get("host") or {"name": "hmux", "version": 3})
    proc = subprocess.Popen(
        [sys.executable, str(HOOK), stage],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    proc.stdin.write(json.dumps(full) + "\n")
    proc.stdin.flush()

    result, logs = {}, []
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if "log" in obj:
            logs.append(obj["log"])
            continue
        ask = obj.get("call")
        if ask and memory is not None:
            try:
                answer = {"id": obj["id"],
                          "result": memory.answer(ask["kind"], ask["method"], ask["args"])}
            except KeyError as e:
                answer = {"id": obj["id"], "error": str(e).strip("'\"")}
            proc.stdin.write(json.dumps(answer) + "\n")
            proc.stdin.flush()
            continue
        result.update(obj)
    proc.stdin.close()
    proc.wait(timeout=20)
    return result, logs


def call_tool(name, args, memory):
    result, _ = call_hook("execute_tool", {"tool": name, "args": args}, memory=memory)
    raw = result.get("result", "")
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw


def a_store():
    return FakeMemory(entries=[
        {"path": "prompts/preamble.md", "content": "you are per.\n"},
        {"path": "prompts/chat.md", "content": "be brief.\n"},
        {"path": "prompts/heartbeat.md", "content": "look around.\n"},
        {"path": "traits/SOUL.md", "content": "curious"},
        {"path": "traits/USER.md", "content": "prefers ascii"},
        {"path": "traits/federation.md", "content": "long prose"},
    ])


def main():
    # --- composition: what a system prompt is made of -----------------------------------------
    mem = a_store()
    try:
        result, _ = call_hook("mutate_request", memory=mem)
        system = "".join(result.get("system") or [])
        check("the preamble and the mode's prompt both reach the system prompt",
              "you are per." in system and "be brief." in system, system[:200])
        check("a core trait is inlined, by name and content",
              "{trait:SOUL.md}" in system and "curious" in system, system[:200])
        check("a listed trait is NAMED but not inlined",
              "federation.md" in system and "long prose" not in system, system[:300])
        check("and the prompt says how to read one, in the tool that now serves it",
              "trait_read" in system, system[-200:])
        # PERSONA OWNS THE SYSTEM PROMPT, so it replaces rather than appends -- an append would
        # leave the backend's own default in front of the soul.
        check("the result REPLACES the backend default", result.get("system_mode") == "replace")
        # ONE LISTING FOR THE PROMPTS AND ONE INDEX FOR THE TRAITS. the traits face reads the
        # inlined ones itself, which is where the per-file cost went -- and why it reads only what
        # it inlines rather than pulling the whole directory back (`room.json` was 53KB of it).
        check("composition is one call per directory, not one per file",
              len(mem.calls("workspace.list")) == 1 and len(mem.calls("traits.index")) == 1,
              str(mem.calls()))
    finally:
        mem.close()

    # --- composition when the store is unreachable ---------------------------------------------
    result, _ = call_hook("mutate_request", memory=Unreachable())
    system = "".join(result.get("system") or [])
    # A SESSION WITH NO SOUL MUST SAY SO. returning nothing would leave the backend default in
    # place, which reads as persona simply not being loaded.
    check("an unreachable store degrades VISIBLY rather than silently",
          "degraded" in system and result.get("system_mode") == "replace", system[:200])

    # --- the heartbeat stage ------------------------------------------------------------------
    mem = a_store()
    try:
        result, _ = call_hook("heartbeat", memory=mem)
        check("the heartbeat carries its own prompt as the user turn",
              result.get("user", "").strip() == "look around.", str(result.get("user")))
        check("and composes the soul beside it",
              "curious" in "".join(result.get("system") or []))
    finally:
        mem.close()

    mem = FakeMemory(entries=[])
    try:
        result, _ = call_hook("heartbeat", memory=mem)
        check("no heartbeat prompt means no beat, not an empty one", result == {}, str(result))
    finally:
        mem.close()

    # --- tasks: persona's schema over memory's shapes ------------------------------------------
    mem = a_store()
    try:
        got = call_tool("task_create", {"title": "water the plants"}, mem)
        patches = mem.calls("trait_data_update")
        check("creating a task returns its id", isinstance(got, dict) and "id" in got, str(got))
        # ONE `$set` ON THE TASK'S OWN KEY. rewriting the whole document would make two creations
        # racing lose one another, which is what the per-entry shape exists to prevent.
        ops = patches[0][1]["ops"]["$set"] if patches else {}
        check("and writes ONE key of the tasks document",
              len(patches) == 1 and list(ops) == [got.get("id")], str(patches))
        check("it never reads the other tasks in order to write one",
              not any(str(c[1].get("trait", "")).endswith(".tasks.json")
                      for c in mem.calls("trait_data_query")),
              str(mem.calls("trait_data_query")))

        task_id = got["id"]
        mem.docs["traits/.tasks.json"] = {task_id: {"title": "water the plants", "status": "open"}}
        mem.log.clear()
        call_tool("task_update", {"id": task_id, "status": "blocked"}, mem)
        ops = mem.calls("trait_data_update")[0][1]["ops"]["$set"]
        # SET ONLY WHAT CHANGED, by dot-path: writing the whole task back would put every field it
        # read back with it, so two edits in flight would each undo the other's.
        check("updating a task sets only the fields that changed",
              set(ops) == {f"{task_id}.status", f"{task_id}.updated"}, str(ops))

        got = call_tool("task_update", {"id": "no-such-task", "status": "open"}, mem)
        check("updating a task that does not exist says so",
              isinstance(got, dict) and "not found" in got.get("error", ""), str(got))
    finally:
        mem.close()

    mem = a_store()
    try:
        got = call_tool("task_create", {"title": "weekly", "interval": "P1W"}, mem)
        check("a recurrence with no due date is refused",
              isinstance(got, dict) and "interval requires a due date" in got.get("error", ""),
              str(got))
    finally:
        mem.close()

    # --- the ordering that has no transaction to protect it ------------------------------------
    mem = a_store()
    try:
        task_id = "t-1"
        mem.docs["traits/.tasks.json"] = {
            task_id: {"title": "weekly", "status": "open", "interval": "P1W",
                      "due": "2026-01-01T09:00:00.000+00:00"}}
        got = call_tool("task_comment", {"id": task_id, "text": "did it"}, mem)
        check("commenting on a recurring task bumps its due date by the interval",
              got.get("due") == "2026-01-08T09:00:00.000+00:00", str(got))
        writes = [c for c in mem.log if c[0] in ("trait_append", "trait_data_update")]
        # THE ORDER IS LOAD-BEARING AND THERE IS NO TRANSACTION ACROSS TWO FILES. a crash between
        # them must leave a comment against a stale due date -- visible and self-correcting -- and
        # never a bumped task with no record of why, which is invisible.
        check("the comment is appended BEFORE the due date moves",
              [w[0] for w in writes] == ["trait_append", "trait_data_update"], str(writes))
        check("and the comment carries the task it is about",
              writes[0][1]["fields"]["task_id"] == task_id, str(writes[0]))
    finally:
        mem.close()

    # --- prompts round-trip through the endpoint -----------------------------------------------
    mem = a_store()
    try:
        call_tool("prompt_write", {"prompt": "chat.md", "content": "be briefer."}, mem)
        check("writing a prompt PUTs it under prompts/",
              mem.calls("workspace.write")
              and mem.calls("workspace.write")[0][1]["path"] == "prompts/chat.md",
              str(mem.calls("workspace.write")))
        got = call_tool("prompt_read", {"prompt": "chat.md"}, mem)
        check("and reading it back gives what was written", got == "be briefer.", str(got))
        listed = call_tool("prompt_list", {}, mem)
        check("listing names the prompt files", "chat.md" in str(listed), str(listed))

        # A REFUSAL MUST NOT LOOK LIKE A PROMPT (user-reported, 2026-09-03: a missing prompt came
        # back as `spoke.call failed: workspace.read: ... does not exist (-32603)`). This tool
        # answers with the arbitrary CONTENTS of a file, so a bare sentence cannot be told apart
        # from a prompt whose text happens to read like an error -- the model has no way to know
        # which it got. Every other tool here already answered `{"error": ...}`; this one did not,
        # and no test looked at its failure path, which is how it stayed the odd one out.
        missing = call_tool("prompt_read", {"prompt": "no-such-prompt.md"}, mem)
        check("a prompt that does not exist is a STRUCTURED error, not a bare string",
              isinstance(missing, dict) and "error" in missing, repr(missing))
        check("and the error still says which file it was",
              "no-such-prompt.md" in str(missing), repr(missing))
    finally:
        mem.close()

    # --- what is NOT here any more: the trait wrappers (hmux phase 82) -------------------------
    #
    # SIX TOOLS AND EVERY TEST OF THEM MOVED TO THE TRAITS FACE, where the scope is the face's root
    # rather than a prefix this hook remembered to put on. `e2e/traits_face_test.py` in hmux is the
    # one that matters: it proves a model with the traits face running and `workspace_*` disabled
    # cannot reach `hooks/persona.py` BY ANY TOOL -- which is the property the wrappers were an
    # approximation of, and which no test of a python wrapper could ever establish.
    #
    # WHAT REMAINS BELOW IS THE GUARD THAT THEY REALLY WENT: a `trait_*` still declared here would
    # be a second implementation shadowing the face, and the model would get whichever registered
    # last.

    # --- the tool surface ----------------------------------------------------------------------
    mem = a_store()
    try:
        result, _ = call_hook("discover", memory=mem)
        names = {t["name"] for t in result.get("tools", [])}
        check("discover declares persona's remaining tools",
              {"task_create", "task_update", "task_comment", "prompt_read", "datetime"} <= names,
              str(sorted(names)))
        # THE MIGRATION'S REGRESSION GUARD. what moved out of this hook is the STORAGE ENGINE --
        # a mongo filter evaluator, a dot-path updater, the document and record tools on them --
        # and then, in phase 82, the trait tools too. A copy left behind here would be a second
        # implementation of the thing each move existed to have one of, and the model would get
        # whichever registered last.
        gone = {n for n in names if n.startswith(("data_", "record_", "trait_"))}
        check("no storage or trait tool survived the move to the faces", not gone, str(gone))
        # WITHOUT THIS DECLARATION EVERY PROMPT SILENTLY DEGRADES. hcp shuts stdin for a hook that
        # did not ask, so the payload still arrives and the first QUESTION reads EOF -- persona
        # raises, catches its own MemoryError and composes "system degraded". Nothing crashes and
        # nothing in this suite noticed, because `call_hook` answers whether or not the hook asked
        # to be answered. Found by deleting the line and watching all 26 stay green.
        check("discover asks for the duplex channel every other stage depends on",
              result.get("duplex") is True, str(result.get("duplex")))
        gated = {t["name"] for t in result.get("tools", []) if t.get("permission")}
        check("the mutating task tools still carry their permission arg",
              {"task_update", "task_comment"} <= gated, str(sorted(gated)))
    finally:
        mem.close()

    # --- the clock -----------------------------------------------------------------------------
    got = call_tool("datetime", {}, "http://127.0.0.1:1")
    check("the datetime tool answers in canonical ISO 8601 UTC, with no store involved",
          isinstance(got, str) and got.endswith("+00:00") and got[4] == "-", str(got))

    print(f"\n{PASS + FAIL} tests, {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
