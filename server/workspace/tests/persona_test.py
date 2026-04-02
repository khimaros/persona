#!/usr/bin/env python3
"""end-to-end tests for persona hook dispatcher (JSONL IPC)."""

import json, os, re, shutil, subprocess, sys, tempfile

PASS = FAIL = 0

def call_hook(hook_path, name, ctx=None):
    """call a hook and return (merged_result, logs, exit_code)."""
    input_data = json.dumps(ctx or {})
    proc = subprocess.run(
        [hook_path, name], input=input_data, capture_output=True, text=True,
    )
    result, logs = {}, []
    for line in proc.stdout.strip().splitlines():
        if not line:
            continue
        obj = json.loads(line)
        if "log" in obj:
            logs.append(obj["log"])
        else:
            result.update(obj)
    return result, logs, proc.returncode

def call_tool(hook_path, name, args=None):
    """shorthand for calling a tool via execute_tool hook."""
    return call_hook(hook_path, "execute_tool", {"tool": name, "args": args or {}})

def check(desc, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {desc}")
        if detail:
            print(f"  {detail}")

def has_key(result, key):
    return key in result

def has_value(result, key, substring):
    return key in result and substring in str(result[key])

def result_json(r):
    """parse the result string as JSON."""
    return json.loads(r["result"])

# --- setup ---

workspace = os.environ.get("OPENCODE_EVOLVE_WORKSPACE",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
tmp = tempfile.mkdtemp()

try:
    # copy hook into temp workspace
    for d in ("hooks", "traits", "prompts"):
        os.makedirs(os.path.join(tmp, d))
    shutil.copy2(os.path.join(workspace, "hooks", "persona.py"), os.path.join(tmp, "hooks", "persona.py"))
    hook = os.path.join(tmp, "hooks", "persona.py")
    for name, content in [("preamble.md", "preamble"), ("chat.md", "chat"),
                          ("heartbeat.md", "heartbeat"), ("recover.md", "recover"),
                          ("compaction.md", "compaction")]:
        open(os.path.join(tmp, "prompts", name), "w").write(content)

    # --- error handling ---

    r, _, rc = call_hook(hook, "nonexistent")
    check("unknown hook returns error", has_key(r, "error"))

    proc = subprocess.run([hook], capture_output=True, text=True)
    check("no args returns error", proc.returncode != 0 or "error" in proc.stdout)

    # --- discover ---

    r, logs, _ = call_hook(hook, "discover")
    check("discover returns tools key", has_key(r, "tools"))
    check("discover has no typo keys", not has_key(r, "tool"))
    names = [t["name"] for t in r["tools"]]
    for expected in ("trait_list", "trait_read", "trait_write", "trait_edit",
                     "trait_append", "trait_delete", "trait_move",
                     "data_query", "data_update", "data_delete", "data_append", "data_count",
                     "record_append", "record_query", "record_count",
                     "task_create", "task_update", "task_comment"):
        check(f"discover includes {expected}", expected in names, f"got: {names}")
    # removed tools must not appear
    for removed in ("data_read", "data_list", "record_list", "record_fields",
                    "task_list", "task_read", "task_delete",
                    "journal_append", "journal_list", "journal_count"):
        check(f"discover excludes {removed}", removed not in names, f"got: {names}")
    check("discover returns exactly 18 tools", len(r["tools"]) == 18, f"got: {len(r['tools'])}")
    check("discover logs tool names", any("tools:" in l for l in logs))

    # --- discover tool parameter schemas ---

    tools_by_name = {t["name"]: t for t in r["tools"]}
    expected_counts = {"trait_list": 1, "trait_read": 3, "trait_write": 2, "trait_edit": 4}
    for name, count in expected_counts.items():
        actual = len(tools_by_name[name]["parameters"])
        check(f"{name} has {count} params", actual == count, f"got: {actual}")

    # data_query fields param must be array type
    dq_fields = tools_by_name["data_query"]["parameters"].get("fields", {})
    check("data_query fields is array[string] type",
          isinstance(dq_fields, dict) and dq_fields.get("type") == "array[string]",
          f"got: {dq_fields}")

    # record_query fields param must be array[string] type
    rq_fields = tools_by_name["record_query"]["parameters"].get("fields", {})
    check("record_query fields is array[string] type",
          isinstance(rq_fields, dict) and rq_fields.get("type") == "array[string]",
          f"got: {rq_fields}")

    # --- mutate_request ---

    r, logs, _ = call_hook(hook, "mutate_request")
    check("request returns system key", has_key(r, "system"))
    check("request has no tools key", not has_key(r, "tools"))
    system_text = "\n".join(r.get("system", []))
    check("request includes preamble", "preamble" in system_text)
    check("request includes chat prompt", "chat" in system_text)
    check("request logs core traits", any("core:" in l for l in logs))

    # --- mutate_request marker detection ---

    r, _, _ = call_hook(hook, "mutate_request", {"system": ["some prompt <~ PERSONA AGENT MARKER ~> end"]})
    check("request with marker returns system", has_key(r, "system"))

    r, _, _ = call_hook(hook, "mutate_request", {"system": ["no marker here"]})
    check("request without marker returns no system", not has_key(r, "system"))

    r, _, _ = call_hook(hook, "mutate_request", {"system": []})
    check("request with empty system returns no system", not has_key(r, "system"))

    # backwards compat: no system key in context still works (defaults to matching)
    r, _, _ = call_hook(hook, "mutate_request")
    check("request without system key returns system", has_key(r, "system"))

    # --- mutate_request without pending_updates ---

    r2, _, _ = call_hook(hook, "mutate_request")
    check("request without pending has system", has_key(r2, "system"))
    system_text2 = "\n".join(r2.get("system", []))
    check("request without pending has no trait-update", "trait-update" not in system_text2)

    # --- mutate_request trait visibility ---

    open(os.path.join(tmp, "traits", "CORE.md"), "w").write("core content")
    open(os.path.join(tmp, "traits", "notes.md"), "w").write("lowercase content")
    open(os.path.join(tmp, "traits", ".secret.md"), "w").write("hidden")

    r, _, _ = call_hook(hook, "mutate_request")
    system_text = "\n".join(r.get("system", []))
    check("request inlines core trait", "core content" in system_text)
    check("request does not inline lowercase trait", "lowercase content" not in system_text)
    # verify inlined trait uses {trait:NAME} format
    check("inlined core trait uses trait: format", "{trait:CORE.md}" in system_text,
          f"got: {system_text!r}")
    # content must appear verbatim after the trait tag
    tag_idx = system_text.find("{trait:CORE.md}")
    after_tag = system_text[tag_idx + len("{trait:CORE.md}"):].lstrip("\n")
    check("inlined core trait content is verbatim", after_tag.startswith("core content"),
          f"got: {after_tag!r}")
    # listed traits use {trait:} format too
    check("request lists lowercase trait with trait: format", "{trait:notes.md}" in system_text,
          f"got: {system_text!r}")
    check("request hides dot-prefixed content", "hidden" not in system_text)
    check("request hides dot-prefixed name", ".secret.md" not in system_text)

    for f in ("CORE.md", "notes.md", ".secret.md"):
        os.remove(os.path.join(tmp, "traits", f))

    # --- mutate_request core detection with non-alpha chars ---

    open(os.path.join(tmp, "traits", "V2_PLAN.md"), "w").write("v2 plan content")
    open(os.path.join(tmp, "traits", "MY.TRAIT.txt"), "w").write("my trait content")

    r, _, _ = call_hook(hook, "mutate_request")
    system_text = "\n".join(r.get("system", []))
    check("request inlines ALLCAPS with digits+underscore", "v2 plan content" in system_text)
    check("request inlines ALLCAPS with dots+any ext", "my trait content" in system_text)
    check("request does not list ALLCAPS digit trait", "V2_PLAN.md" not in system_text.split("additional traits")[-1] if "additional traits" in system_text else True)

    for f in ("V2_PLAN.md", "MY.TRAIT.txt"):
        os.remove(os.path.join(tmp, "traits", f))

    # --- heartbeat ---

    r, logs, _ = call_hook(hook, "heartbeat")
    check("heartbeat returns system key", has_key(r, "system"))
    check("heartbeat returns user key", has_key(r, "user"))
    system_text = "\n".join(r.get("system", []))
    check("heartbeat includes preamble", "preamble" in system_text)
    check("heartbeat includes heartbeat prompt", "heartbeat" in system_text)
    check("heartbeat logs core traits", any("core:" in l for l in logs))

    # --- recover ---

    r, logs, _ = call_hook(hook, "recover", {"failed_hook": "mutate_request", "error": "boom"})
    check("recover returns system key", has_key(r, "system"))
    check("recover returns user key", has_key(r, "user"))
    system_text = "\n".join(r.get("system", []))
    check("recover includes preamble", "preamble" in system_text)
    check("recover includes recover prompt", "recover" in system_text)
    check("recover logs context", any("recovering from mutate_request" in l for l in logs))

    # --- observe_message ---

    _, logs, _ = call_hook(hook, "observe_message", {"session": {"id": "abc", "agent": "per"}})
    check("observe_message logs session", any("session=abc" in l for l in logs))
    check("observe_message logs agent", any("agent=per" in l for l in logs))

    # --- idle ---

    r, logs, _ = call_hook(hook, "idle", {"session": {"id": "s1", "agent": "per"}, "answer": "hello"})
    check("idle returns empty by default", not has_key(r, "continue"))
    check("idle logs session", any("session=s1" in l for l in logs))
    check("idle logs answer length", any("answer_len=5" in l for l in logs))

    # --- compacting ---

    r, logs, _ = call_hook(hook, "compacting")
    check("compacting returns prompt key", has_key(r, "prompt"))
    check("compacting logs core traits", any("core:" in l for l in logs))

    # --- trait_list ---

    open(os.path.join(tmp, "traits", "CORE.md"), "w").write("a")
    open(os.path.join(tmp, "traits", "notes.md"), "w").write("b")
    open(os.path.join(tmp, "traits", ".hidden.md"), "w").write("c")
    open(os.path.join(tmp, "traits", "V2_PLAN.md"), "w").write("d")
    open(os.path.join(tmp, "traits", "MY.TRAIT.txt"), "w").write("e")

    r, logs, _ = call_tool(hook, "trait_list")
    check("trait_list returns result key", has_key(r, "result"))
    check("trait_list result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("trait_list has no results typo", not has_key(r, "results"))
    # must use {trait:NAME} format
    check("trait_list uses trait: format for CORE", "{trait:CORE.md}" in r["result"])
    check("trait_list uses trait: format for notes", "{trait:notes.md}" in r["result"])
    check("trait_list excludes hidden", ".hidden.md" not in r["result"])
    check("trait_list includes digits+underscore core", "{trait:V2_PLAN.md}" in r["result"])
    check("trait_list includes dot-stem any-ext core", "{trait:MY.TRAIT.txt}" in r["result"])
    check("trait_list logs tool name", any("tool=trait_list" in l for l in logs))
    # no avatar prefix
    check("trait_list no avatar prefix", not r["result"].startswith("🌀"))

    r, _, _ = call_tool(hook, "trait_list", {"include_hidden": "true"})
    check("trait_list hidden includes hidden", "{trait:.hidden.md}" in r["result"])
    check("trait_list hidden includes core", "{trait:CORE.md}" in r["result"])

    # bool args from JSON (LLM sends true not "true")
    r, _, _ = call_tool(hook, "trait_list", {"include_hidden": True})
    check("trait_list bool true includes hidden", ".hidden.md" in r["result"])

    r, _, _ = call_tool(hook, "trait_list", {"include_hidden": False})
    check("trait_list bool false excludes hidden", ".hidden.md" not in r["result"])

    # trait_list includes subdirectory traits with relative paths
    os.makedirs(os.path.join(tmp, "traits", "topics"), exist_ok=True)
    open(os.path.join(tmp, "traits", "topics", "music.md"), "w").write("f")
    r, _, _ = call_tool(hook, "trait_list")
    check("trait_list includes subdir trait", "{trait:topics/music.md}" in r["result"])
    os.remove(os.path.join(tmp, "traits", "topics", "music.md"))
    os.rmdir(os.path.join(tmp, "traits", "topics"))

    for f in ("CORE.md", "notes.md", ".hidden.md", "V2_PLAN.md", "MY.TRAIT.txt"):
        os.remove(os.path.join(tmp, "traits", f))

    # --- trait_read + format_trait contract ---

    open(os.path.join(tmp, "traits", "A.md"), "w").write("test content")

    r, _, _ = call_tool(hook, "trait_read", {"trait": "A.md"})
    check("trait_read returns result key", has_key(r, "result"))
    check("trait_read result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("trait_read returns content", "test content" in r["result"])
    # format_trait must produce: \n{trait:A.md}\n<content>\n
    trait_text = r["result"]
    check("format_trait has trait tag", "{trait:A.md}" in trait_text, f"got: {trait_text!r}")
    # no avatar prefix
    check("trait_read no avatar prefix", not trait_text.startswith("🌀"))
    # content must appear verbatim after trait tag
    tag_end = trait_text.find("{trait:A.md}") + len("{trait:A.md}")
    after_tag = trait_text[tag_end:].strip()
    check("format_trait content is verbatim", after_tag == "test content",
          f"got: {after_tag!r}")

    r, _, _ = call_tool(hook, "trait_read", {"trait": "MISSING.md"})
    check("trait_read missing returns result key", has_key(r, "result"))
    check("trait_read missing returns empty marker", "(empty)" in r["result"])

    # --- trait_read offset/limit ---

    open(os.path.join(tmp, "traits", "LINES.md"), "w").write("line1\nline2\nline3\nline4\nline5")

    r, _, _ = call_tool(hook, "trait_read", {"trait": "LINES.md", "offset": "3", "limit": "2"})
    check("trait_read offset/limit returns result", has_key(r, "result"))
    check("trait_read offset skips lines", "line1" not in r["result"])
    check("trait_read offset starts at right line", "line3" in r["result"])
    check("trait_read limit caps lines", "line5" not in r["result"])

    r, _, _ = call_tool(hook, "trait_read", {"trait": "LINES.md", "offset": "1"})
    check("trait_read offset-only returns first line", "line1" in r["result"])
    check("trait_read offset-only returns all lines", "line5" in r["result"])

    r, _, _ = call_tool(hook, "trait_read", {"trait": "LINES.md", "limit": "2"})
    check("trait_read limit-only returns first line", "line1" in r["result"])

    # --- trait_write ---

    r, _, _ = call_tool(hook, "trait_write", {"trait": "NEW.md", "content": "hello world"})
    check("trait_write returns result key", has_key(r, "result"))
    # structured response
    parsed = result_json(r)
    check("trait_write returns success json", parsed.get("success") is True, f"got: {parsed}")
    check("trait_write reports modified", has_key(r, "modified"))
    check("trait_write modified list correct", r.get("modified") == ["NEW.md"])
    content = open(os.path.join(tmp, "traits", "NEW.md")).read()
    check("trait_write wrote file", content == "hello world")

    # --- trait_edit ---

    open(os.path.join(tmp, "traits", "PATCH.md"), "w").write("old text here")

    r, _, _ = call_tool(hook, "trait_edit", {"trait": "PATCH.md", "oldString": "old text", "newString": "new text"})
    check("trait_edit returns result key", has_key(r, "result"))
    parsed = result_json(r)
    check("trait_edit returns success json", parsed.get("success") is True, f"got: {parsed}")
    check("trait_edit reports modified", has_key(r, "modified"))
    content = open(os.path.join(tmp, "traits", "PATCH.md")).read()
    check("trait_edit updated file", content == "new text here")

    r, _, _ = call_tool(hook, "trait_edit", {"trait": "PATCH.md", "oldString": "nonexistent", "newString": "x"})
    parsed = result_json(r)
    check("trait_edit not found returns error", "error" in parsed, f"got: {parsed}")

    open(os.path.join(tmp, "traits", "DUP.md"), "w").write("aaa")
    r, _, _ = call_tool(hook, "trait_edit", {"trait": "DUP.md", "oldString": "a", "newString": "b"})
    parsed = result_json(r)
    check("trait_edit multiple matches returns error", "error" in parsed, f"got: {parsed}")

    # --- trait_append ---

    open(os.path.join(tmp, "traits", "APPEND.md"), "w").write("line one")

    r, logs, _ = call_tool(hook, "trait_append", {"trait": "APPEND.md", "content": "line two"})
    check("trait_append returns result key", has_key(r, "result"))
    parsed = result_json(r)
    check("trait_append returns success json", parsed.get("success") is True, f"got: {parsed}")
    check("trait_append reports modified", r.get("modified") == ["APPEND.md"])
    content = open(os.path.join(tmp, "traits", "APPEND.md")).read()
    check("trait_append appended content", content == "line one\nline two")

    r, _, _ = call_tool(hook, "trait_append", {"trait": "NEWFILE.md", "content": "created by append"})
    parsed = result_json(r)
    check("trait_append creates new file", parsed.get("success") is True, f"got: {parsed}")
    content = open(os.path.join(tmp, "traits", "NEWFILE.md")).read()
    check("trait_append new file content", content == "\ncreated by append")

    # --- trait_delete ---

    open(os.path.join(tmp, "traits", "DEL.md"), "w").write("delete me")

    r, logs, _ = call_tool(hook, "trait_delete", {"trait": "DEL.md"})
    check("trait_delete returns result key", has_key(r, "result"))
    parsed = result_json(r)
    check("trait_delete returns success json", parsed.get("success") is True, f"got: {parsed}")
    check("trait_delete reports modified", r.get("modified") == ["DEL.md"])
    check("trait_delete removed file", not os.path.exists(os.path.join(tmp, "traits", "DEL.md")))
    check("trait_delete logs tool name", any("tool=trait_delete" in l for l in logs))

    r, _, _ = call_tool(hook, "trait_delete", {"trait": "DEL.md"})
    parsed = result_json(r)
    check("trait_delete not found returns error", "error" in parsed, f"got: {parsed}")

    # --- trait_move ---

    open(os.path.join(tmp, "traits", "SRC.md"), "w").write("move me")

    r, logs, _ = call_tool(hook, "trait_move", {"old_trait": "SRC.md", "new_trait": "DST.md"})
    check("trait_move returns result key", has_key(r, "result"))
    parsed = result_json(r)
    check("trait_move returns success json", parsed.get("success") is True, f"got: {parsed}")
    check("trait_move reports both modified", set(r.get("modified", [])) == {"SRC.md", "DST.md"})
    check("trait_move removed src", not os.path.exists(os.path.join(tmp, "traits", "SRC.md")))
    check("trait_move created dst", os.path.exists(os.path.join(tmp, "traits", "DST.md")))
    content = open(os.path.join(tmp, "traits", "DST.md")).read()
    check("trait_move preserved content", content == "move me")
    check("trait_move logs tool name", any("tool=trait_move" in l for l in logs))

    r, _, _ = call_tool(hook, "trait_move", {"old_trait": "MISSING.md", "new_trait": "X.md"})
    parsed = result_json(r)
    check("trait_move not found returns error", "error" in parsed, f"got: {parsed}")

    open(os.path.join(tmp, "traits", "EXIST.md"), "w").write("x")
    r, _, _ = call_tool(hook, "trait_move", {"old_trait": "DST.md", "new_trait": "EXIST.md"})
    parsed = result_json(r)
    check("trait_move already exists returns error", "error" in parsed, f"got: {parsed}")

    for f in ("DST.md", "EXIST.md"):
        os.remove(os.path.join(tmp, "traits", f))

    # --- trait directory handling ---

    # trait_write creates parent directories
    r, _, _ = call_tool(hook, "trait_write", {"trait": "sub/deep/NESTED.md", "content": "nested"})
    check("trait_write creates parent dirs", os.path.exists(os.path.join(tmp, "traits", "sub", "deep", "NESTED.md")))
    parsed = result_json(r)
    check("trait_write nested success", parsed.get("success") is True)
    content = open(os.path.join(tmp, "traits", "sub", "deep", "NESTED.md")).read()
    check("trait_write nested content correct", content == "nested")

    # trait_delete removes empty parent directories
    r, _, _ = call_tool(hook, "trait_delete", {"trait": "sub/deep/NESTED.md"})
    parsed = result_json(r)
    check("trait_delete nested success", parsed.get("success") is True)
    check("trait_delete removed nested file", not os.path.exists(os.path.join(tmp, "traits", "sub", "deep", "NESTED.md")))
    check("trait_delete removed empty deep dir", not os.path.exists(os.path.join(tmp, "traits", "sub", "deep")))
    check("trait_delete removed empty sub dir", not os.path.exists(os.path.join(tmp, "traits", "sub")))
    check("trait_delete preserves traits dir", os.path.isdir(os.path.join(tmp, "traits")))

    # trait_delete does not remove non-empty parent directories
    os.makedirs(os.path.join(tmp, "traits", "keep", "inner"), exist_ok=True)
    open(os.path.join(tmp, "traits", "keep", "sibling.md"), "w").write("keep me")
    open(os.path.join(tmp, "traits", "keep", "inner", "DEL.md"), "w").write("delete me")
    r, _, _ = call_tool(hook, "trait_delete", {"trait": "keep/inner/DEL.md"})
    check("trait_delete removed inner file", not os.path.exists(os.path.join(tmp, "traits", "keep", "inner", "DEL.md")))
    check("trait_delete removed empty inner dir", not os.path.exists(os.path.join(tmp, "traits", "keep", "inner")))
    check("trait_delete kept non-empty parent", os.path.isdir(os.path.join(tmp, "traits", "keep")))
    check("trait_delete kept sibling file", os.path.exists(os.path.join(tmp, "traits", "keep", "sibling.md")))
    os.remove(os.path.join(tmp, "traits", "keep", "sibling.md"))
    os.rmdir(os.path.join(tmp, "traits", "keep"))

    # trait_move creates destination parent directories
    open(os.path.join(tmp, "traits", "MVSRC.md"), "w").write("move to subdir")
    r, _, _ = call_tool(hook, "trait_move", {"old_trait": "MVSRC.md", "new_trait": "newdir/deep/MVDST.md"})
    check("trait_move creates dst dirs", os.path.exists(os.path.join(tmp, "traits", "newdir", "deep", "MVDST.md")))
    parsed = result_json(r)
    check("trait_move to subdir success", parsed.get("success") is True)
    content = open(os.path.join(tmp, "traits", "newdir", "deep", "MVDST.md")).read()
    check("trait_move to subdir content", content == "move to subdir")

    # trait_move cleans up empty source parent directories
    r, _, _ = call_tool(hook, "trait_move", {"old_trait": "newdir/deep/MVDST.md", "new_trait": "MVBACK.md"})
    parsed = result_json(r)
    check("trait_move from subdir success", parsed.get("success") is True)
    check("trait_move cleaned empty deep dir", not os.path.exists(os.path.join(tmp, "traits", "newdir", "deep")))
    check("trait_move cleaned empty newdir", not os.path.exists(os.path.join(tmp, "traits", "newdir")))
    check("trait_move preserves traits dir", os.path.isdir(os.path.join(tmp, "traits")))
    os.remove(os.path.join(tmp, "traits", "MVBACK.md"))

    # --- tool handlers return notify ---

    open(os.path.join(tmp, "traits", "NOTIFY.md"), "w").write("x")

    r, _, _ = call_tool(hook, "trait_write", {"trait": "NOTIFY.md", "content": "updated"})
    check("trait_write returns notify", has_key(r, "notify"))
    check("trait_write notify is list", isinstance(r.get("notify"), list))
    check("trait_write notify has trait_changed", any(n.get("type") == "trait_changed" for n in r.get("notify", [])))
    check("trait_write notify includes file", any("NOTIFY.md" in n.get("files", []) for n in r.get("notify", [])))

    r, _, _ = call_tool(hook, "trait_edit", {"trait": "NOTIFY.md", "oldString": "updated", "newString": "patched"})
    check("trait_edit returns notify", has_key(r, "notify"))
    check("trait_edit notify has trait_changed", any(n.get("type") == "trait_changed" for n in r.get("notify", [])))

    r, _, _ = call_tool(hook, "trait_move", {"old_trait": "NOTIFY.md", "new_trait": "NOTIFY2.md"})
    check("trait_move returns notify", has_key(r, "notify"))
    check("trait_move notify includes both files",
          any(set(n.get("files", [])) == {"NOTIFY.md", "NOTIFY2.md"} for n in r.get("notify", [])))

    r, _, _ = call_tool(hook, "trait_delete", {"trait": "NOTIFY2.md"})
    check("trait_delete returns notify", has_key(r, "notify"))
    check("trait_delete notify has trait_changed", any(n.get("type") == "trait_changed" for n in r.get("notify", [])))

    # --- format_notification ---

    r, _, _ = call_hook(hook, "format_notification", {
        "notifications": [{"type": "trait_changed", "files": ["FOO.md", "BAR.md"]}],
    })
    check("format_notification returns message", has_key(r, "message"))
    check("format_notification message has update text", "traits were updated" in r.get("message", ""))
    check("format_notification message includes files", "BAR.md" in r.get("message", "") and "FOO.md" in r.get("message", ""))

    r, _, _ = call_hook(hook, "format_notification", {"notifications": []})
    check("format_notification empty returns no message", not has_key(r, "message"))

    r, _, _ = call_hook(hook, "format_notification", {})
    check("format_notification missing key returns no message", not has_key(r, "message"))

    # --- no avatar prefix on any tool results ---

    open(os.path.join(tmp, "traits", "AV.md"), "w").write("avatar test")

    r, _, _ = call_tool(hook, "trait_list")
    check("trait_list no avatar", "🌀" not in r.get("result", ""))

    r, _, _ = call_tool(hook, "trait_read", {"trait": "AV.md"})
    check("trait_read no avatar", "🌀" not in r.get("result", ""))

    r, _, _ = call_tool(hook, "trait_write", {"trait": "AV.md", "content": "x"})
    check("trait_write no avatar", "🌀" not in r.get("result", ""))

    r, _, _ = call_tool(hook, "trait_delete", {"trait": "AV.md"})
    check("trait_delete no avatar", "🌀" not in r.get("result", ""))

    # --- avatar prefix still on hook debug logs ---

    _, logs, _ = call_hook(hook, "mutate_request")
    check("hook debug logs have avatar prefix", any(l.startswith("[🌀]") for l in logs),
          f"got: {logs}")

    # --- unknown tool ---

    r, _, _ = call_tool(hook, "nonexistent")
    check("unknown tool returns result key", has_key(r, "result"))
    parsed = result_json(r)
    check("unknown tool returns error json", "error" in parsed, f"got: {parsed}")

    # --- bad args ---

    r, _, _ = call_tool(hook, "trait_read", {"wrong": "param"})
    check("bad args returns result key", has_key(r, "result"))
    parsed = result_json(r)
    check("bad args returns error json", "error" in parsed, f"got: {parsed}")

    # --- empty stdin ---

    r, _, _ = call_hook(hook, "mutate_request")
    check("empty context returns system key", has_key(r, "system"))

    # --- history passthrough ---
    # hooks must accept a history field in context without breaking

    sample_history = [
        {"role": "user", "agent": "per", "parts": [{"type": "text", "text": "hello"}]},
        {"role": "assistant", "agent": "per", "parts": [{"type": "text", "text": "hi there"}]},
    ]

    r, _, _ = call_hook(hook, "mutate_request", {"history": sample_history})
    check("mutate_request with history returns system", has_key(r, "system"))

    r, _, _ = call_hook(hook, "mutate_request", {"history": sample_history})
    check("mutate_request with history returns system", has_key(r, "system"))

    r, logs, _ = call_hook(hook, "observe_message", {
        "session": {"id": "h1", "agent": "per"}, "history": sample_history,
    })
    check("observe_message with history logs session", any("session=h1" in l for l in logs))

    r, logs, _ = call_hook(hook, "idle", {
        "session": {"id": "h2", "agent": "per"}, "answer": "ok", "history": sample_history,
    })
    check("idle with history returns ok", not has_key(r, "error"))
    check("idle with history logs session", any("session=h2" in l for l in logs))

    r, _, _ = call_hook(hook, "heartbeat", {"history": sample_history})
    check("heartbeat with history returns system", has_key(r, "system"))

    r, _, _ = call_hook(hook, "compacting", {"history": sample_history})
    check("compacting with history returns prompt", has_key(r, "prompt"))

    r, _, _ = call_hook(hook, "recover", {"failed_hook": "test", "error": "x", "history": sample_history})
    check("recover with history returns system", has_key(r, "system"))

    r, _, _ = call_hook(hook, "format_notification", {
        "notifications": [{"type": "trait_changed", "files": ["X.md"]}], "history": sample_history,
    })
    check("format_notification with history returns message", has_key(r, "message"))

    r, _, _ = call_hook(hook, "tool_before", {
        "session": {"id": "h3"}, "tool": "t", "callID": "c", "args": {}, "history": sample_history,
    })
    check("tool_before with history ok", not has_key(r, "error"))

    r, _, _ = call_hook(hook, "tool_after", {
        "session": {"id": "h3"}, "tool": "t", "callID": "c", "title": "", "output": "", "history": sample_history,
    })
    check("tool_after with history ok", not has_key(r, "error"))

    # --- path traversal ---

    traversal_paths = ["../hooks/persona.py", "../../etc/passwd", "foo/../../bar.md"]

    for bad in traversal_paths:
        r, _, _ = call_tool(hook, "trait_read", {"trait": bad})
        parsed = result_json(r)
        check(f"trait_read rejects traversal ({bad})", "error" in parsed,
              f"got: {parsed}")

        r, _, _ = call_tool(hook, "trait_write", {"trait": bad, "content": "pwned"})
        parsed = result_json(r)
        check(f"trait_write rejects traversal ({bad})", "error" in parsed,
              f"got: {parsed}")
        # verify no file was written at the traversal target
        target = os.path.normpath(os.path.join(tmp, "traits", bad))
        if os.path.exists(target):
            check(f"trait_write did not modify outside traits ({bad})",
                  open(target).read() != "pwned", f"file at {target} was overwritten")
        else:
            check(f"trait_write did not write outside traits ({bad})", True)

        r, _, _ = call_tool(hook, "trait_edit", {"trait": bad, "oldString": "x", "newString": "y"})
        parsed = result_json(r)
        check(f"trait_edit rejects traversal ({bad})", "error" in parsed,
              f"got: {parsed}")

        open(os.path.join(tmp, "traits", "SAFE.md"), "w").write("safe")
        r, _, _ = call_tool(hook, "trait_delete", {"trait": bad})
        parsed = result_json(r)
        check(f"trait_delete rejects traversal ({bad})", "error" in parsed,
              f"got: {parsed}")

        r, _, _ = call_tool(hook, "trait_move", {"old_trait": "SAFE.md", "new_trait": bad})
        parsed = result_json(r)
        check(f"trait_move rejects traversal dst ({bad})", "error" in parsed,
              f"got: {parsed}")

        r, _, _ = call_tool(hook, "trait_move", {"old_trait": bad, "new_trait": "SAFE2.md"})
        parsed = result_json(r)
        check(f"trait_move rejects traversal src ({bad})", "error" in parsed,
              f"got: {parsed}")

        os.remove(os.path.join(tmp, "traits", "SAFE.md"))

    # absolute path traversal
    r, _, _ = call_tool(hook, "trait_write", {"trait": "/etc/passwd", "content": "pwned"})
    parsed = result_json(r)
    check("trait_write rejects absolute path", "error" in parsed,
          f"got: {parsed}")

    # --- data_query (replaces data_read + data_list) ---

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('{"a": 1, "b": {"c": [10, 20, 30]}}')

    # basic read: returns full dict
    r, _, _ = call_tool(hook, "data_query", {"trait": ".test.json"})
    parsed = result_json(r)
    check("data_query returns full object", parsed == {"a": 1, "b": {"c": [10, 20, 30]}},
          f"got: {parsed}")

    # dot-path key selector
    r, _, _ = call_tool(hook, "data_query", {"trait": ".test.json", "key": "a"})
    parsed = result_json(r)
    check("data_query key selector", parsed == 1, f"got: {parsed}")

    r, _, _ = call_tool(hook, "data_query", {"trait": ".test.json", "key": "b.c"})
    parsed = result_json(r)
    check("data_query nested selector", parsed == [10, 20, 30], f"got: {parsed}")

    r, _, _ = call_tool(hook, "data_query", {"trait": ".test.json", "key": "b.c.1"})
    parsed = result_json(r)
    check("data_query array index", parsed == 20, f"got: {parsed}")

    # error cases
    r, _, _ = call_tool(hook, "data_query", {"trait": "noext"})
    parsed = result_json(r)
    check("data_query rejects non-.json", "error" in parsed, f"got: {parsed}")

    r, _, _ = call_tool(hook, "data_query", {"trait": ".missing.json"})
    parsed = result_json(r)
    check("data_query missing file", "error" in parsed, f"got: {parsed}")

    # --- data_query on dict-of-dicts (replaces data_list) ---

    open(os.path.join(tmp, "traits", ".dl.json"), "w").write(json.dumps({
        "id1": {"title": "alpha", "status": "open", "due": "2026-04-01T00:00:00.000+00:00"},
        "id2": {"title": "beta", "status": "done", "due": "2026-05-01T00:00:00.000+00:00"},
        "id3": {"title": "gamma", "status": "open", "owner": "tom"},
    }))

    # no filter: returns full dict
    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json"})
    parsed = result_json(r)
    check("data_query dict returns all keys", set(parsed.keys()) == {"id1", "id2", "id3"},
          f"got: {list(parsed.keys())}")
    check("data_query dict preserves values", parsed["id1"]["title"] == "alpha")

    # --- data_query MongoDB-style filter: exact match ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"status": "open"}})
    parsed = result_json(r)
    check("data_query filter exact match", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: $in ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"status": {"$in": ["open", "done"]}}})
    parsed = result_json(r)
    check("data_query filter $in", set(parsed.keys()) == {"id1", "id2", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: id matching via $in ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"id": {"$in": ["id1", "id3"]}}})
    parsed = result_json(r)
    check("data_query filter id $in", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # single id match
    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"id": "id2"}})
    parsed = result_json(r)
    check("data_query filter id exact", set(parsed.keys()) == {"id2"},
          f"got: {list(parsed.keys())}")

    # missing id returns empty dict
    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"id": "missing"}})
    parsed = result_json(r)
    check("data_query filter id missing", parsed == {}, f"got: {parsed}")

    # --- data_query MongoDB-style filter: $lt, $gt, $lte, $gte ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"due": {"$lt": "2026-04-15T00:00:00.000+00:00"}}})
    parsed = result_json(r)
    check("data_query filter $lt", set(parsed.keys()) == {"id1"},
          f"got: {list(parsed.keys())}")

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"due": {"$gte": "2026-05-01T00:00:00.000+00:00"}}})
    parsed = result_json(r)
    check("data_query filter $gte", set(parsed.keys()) == {"id2"},
          f"got: {list(parsed.keys())}")

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"due": {"$gt": "2026-04-01T00:00:00.000+00:00",
                                                                  "$lte": "2026-05-01T00:00:00.000+00:00"}}})
    parsed = result_json(r)
    check("data_query filter $gt + $lte range", set(parsed.keys()) == {"id2"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: $eq ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"status": {"$eq": "open"}}})
    parsed = result_json(r)
    check("data_query filter $eq", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: $regex ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"title": {"$regex": "^a"}}})
    parsed = result_json(r)
    check("data_query filter $regex", set(parsed.keys()) == {"id1"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: $regex with $options ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"title": {"$regex": "^A", "$options": "i"}}})
    parsed = result_json(r)
    check("data_query filter $regex + $options i", set(parsed.keys()) == {"id1"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: $not ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"status": {"$not": "done"}}})
    parsed = result_json(r)
    check("data_query filter $not", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: $ne (alias for $not) ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"status": {"$ne": "done"}}})
    parsed = result_json(r)
    check("data_query filter $ne", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: $nin ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"status": {"$nin": ["done", "error"]}}})
    parsed = result_json(r)
    check("data_query filter $nin", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query MongoDB-style filter: $or (top-level) ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"$or": [
                                                  {"status": "done"},
                                                  {"owner": "tom"}
                                              ]}})
    parsed = result_json(r)
    check("data_query filter $or", set(parsed.keys()) == {"id2", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query filter on entries missing the filtered field ---
    # entries without the filtered field should not match

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"owner": "tom"}})
    parsed = result_json(r)
    check("data_query filter skips entries without field", set(parsed.keys()) == {"id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query with fields param (array type) ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "fields": ["title", "status"]})
    parsed = result_json(r)
    check("data_query fields projection keys", set(parsed.keys()) == {"id1", "id2", "id3"})
    check("data_query fields includes title", "title" in parsed["id1"])
    check("data_query fields includes status", "status" in parsed["id1"])
    check("data_query fields excludes due", "due" not in parsed["id1"])

    # --- data_query with limit and offset ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json", "limit": "1"})
    parsed = result_json(r)
    check("data_query limit", len(parsed) == 1, f"got: {len(parsed)}")

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json", "limit": "1", "offset": "1"})
    parsed = result_json(r)
    check("data_query offset", len(parsed) == 1, f"got: {len(parsed)}")

    # --- data_query filter + fields combined ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"status": "open"},
                                              "fields": ["title"]})
    parsed = result_json(r)
    check("data_query filter + fields", set(parsed.keys()) == {"id1", "id3"})
    check("data_query filter + fields projection", set(parsed["id1"].keys()) == {"title"},
          f"got: {list(parsed['id1'].keys())}")

    # --- data_query bad regex in $regex ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": {"title": {"$regex": "[invalid"}}})
    parsed = result_json(r)
    check("data_query bad $regex returns error", "error" in parsed, f"got: {parsed}")

    # --- data_query string-coerced filter (LLM sends string instead of object) ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": '{"status": "open"}'})
    parsed = result_json(r)
    check("data_query coerced string filter", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query mangled quote tokens in filter ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": '{<|"|>status<|"|>: <|"|>open<|"|>}'})
    parsed = result_json(r)
    check("data_query mangled quote filter", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query mangled filter with unquoted keys and wrong brackets ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": '{status: <|"|>open<|"|>}'})
    parsed = result_json(r)
    check("data_query unquoted keys filter", set(parsed.keys()) == {"id1", "id3"},
          f"got: {list(parsed.keys())}")

    # --- data_query mangled filter with ]] instead of }} ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": '{due:{$lt:<|"|>2026-04-15T00:00:00.000+00:00<|"|>]}'})
    parsed = result_json(r)
    check("data_query bracket mismatch filter", set(parsed.keys()) == {"id1"},
          f"got: {list(parsed.keys())}")

    # --- data_query unparseable filter returns error ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".dl.json",
                                              "filter": "not json at all"})
    parsed = result_json(r)
    check("data_query unparseable filter error", "error" in parsed, f"got: {parsed}")

    os.remove(os.path.join(tmp, "traits", ".dl.json"))
    os.remove(os.path.join(tmp, "traits", ".test.json"))

    # --- data_update ---

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('{"a": 1, "b": {"c": [10, 20, 30]}}')

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "a", "value": 42})
    parsed = result_json(r)
    check("data_update returns success", parsed.get("success") is True, f"got: {parsed}")
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_update value correct", data["a"] == 42)

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "b.c.0", "value": 99})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_update nested array index", data["b"]["c"][0] == 99)

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "new_key", "value": "hello"})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_update creates new key in dict", data.get("new_key") == "hello")

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "x.y.z", "value": 1})
    parsed = result_json(r)
    check("data_update unreachable key returns error", "error" in parsed, f"got: {parsed}")

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "value": {"fresh": True}})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_update overwrite whole file", data == {"fresh": True})

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "", "value": {"a": 1}})
    check("data_update returns modified", has_key(r, "modified"))
    check("data_update returns notify", has_key(r, "notify"))

    # auto-create non-existent trait
    new_trait = ".autocreated.json"
    new_path = os.path.join(tmp, "traits", new_trait)
    assert not os.path.exists(new_path), "precondition: trait should not exist yet"

    r, _, _ = call_tool(hook, "data_update", {"trait": new_trait, "key": "foo", "value": "bar"})
    parsed = result_json(r)
    check("data_update auto-creates trait", parsed.get("success") is True)
    data = json.loads(open(new_path).read())
    check("data_update auto-created content", data == {"foo": "bar"})

    r, _, _ = call_tool(hook, "data_update", {"trait": ".autocreated2.json", "value": [1, 2, 3]})
    data = json.loads(open(os.path.join(tmp, "traits", ".autocreated2.json")).read())
    check("data_update auto-creates with whole-file overwrite", data == [1, 2, 3])

    # --- data_delete ---

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('{"x": 1, "y": 2, "arr": [10, 20, 30]}')

    r, _, _ = call_tool(hook, "data_delete", {"trait": ".test.json", "key": "x"})
    parsed = result_json(r)
    check("data_delete returns success", parsed.get("success") is True, f"got: {parsed}")
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_delete key gone", "x" not in data)
    check("data_delete other keys intact", data.get("y") == 2)

    r, _, _ = call_tool(hook, "data_delete", {"trait": ".test.json", "key": "arr.1"})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_delete array index", data["arr"] == [10, 30])

    r, _, _ = call_tool(hook, "data_delete", {"trait": ".test.json", "key": "nonexistent"})
    parsed = result_json(r)
    check("data_delete missing key returns error", "error" in parsed, f"got: {parsed}")

    # --- data_append ---

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('{"items": [1, 2]}')

    r, _, _ = call_tool(hook, "data_append", {"trait": ".test.json", "key": "items", "value": 3})
    parsed = result_json(r)
    check("data_append returns success", parsed.get("success") is True, f"got: {parsed}")
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_append value correct", data["items"] == [1, 2, 3])

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('[1, 2]')
    r, _, _ = call_tool(hook, "data_append", {"trait": ".test.json", "value": 3})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_append to root array", data == [1, 2, 3])

    r, _, _ = call_tool(hook, "data_append", {"trait": ".test.json", "key": "notarray", "value": 1})
    parsed = result_json(r)
    check("data_append non-array returns error", "error" in parsed, f"got: {parsed}")

    # auto-create non-existent trait for data_append
    new_trait = ".append_auto.json"
    new_path = os.path.join(tmp, "traits", new_trait)
    assert not os.path.exists(new_path), "precondition: trait should not exist yet"

    r, _, _ = call_tool(hook, "data_append", {"trait": new_trait, "value": "first"})
    parsed = result_json(r)
    check("data_append auto-creates trait", parsed.get("success") is True)
    data = json.loads(open(new_path).read())
    check("data_append auto-created as array", data == ["first"])

    os.remove(new_path)
    os.remove(os.path.join(tmp, "traits", ".test.json"))

    # --- data_count ---

    open(os.path.join(tmp, "traits", ".dc.json"), "w").write(json.dumps({
        "id1": {"title": "alpha", "status": "open", "owner": "tom"},
        "id2": {"title": "beta", "status": "done", "owner": "tom"},
        "id3": {"title": "gamma", "status": "open"},
    }))

    # count all entries
    r, _, _ = call_tool(hook, "data_count", {"trait": ".dc.json"})
    parsed = result_json(r)
    check("data_count returns count", parsed.get("count") == 3, f"got: {parsed}")
    check("data_count returns field counts", "fields" in parsed, f"got: {parsed}")
    check("data_count fields has title", parsed["fields"].get("title") == 3)
    check("data_count fields has owner", parsed["fields"].get("owner") == 2)

    # count with specific field: unique value counts
    r, _, _ = call_tool(hook, "data_count", {"trait": ".dc.json", "field": "status"})
    parsed = result_json(r)
    check("data_count field returns count", parsed.get("count") == 3, f"got: {parsed}")
    check("data_count field has field name", parsed.get("field") == "status")
    check("data_count field has values", "values" in parsed)
    check("data_count field open count", parsed["values"].get("open") == 2)
    check("data_count field done count", parsed["values"].get("done") == 1)

    # count with filter
    r, _, _ = call_tool(hook, "data_count", {"trait": ".dc.json",
                                              "filter": {"status": "open"}})
    parsed = result_json(r)
    check("data_count with filter", parsed.get("count") == 2, f"got: {parsed}")

    # count with field + filter
    r, _, _ = call_tool(hook, "data_count", {"trait": ".dc.json",
                                              "field": "owner",
                                              "filter": {"status": "open"}})
    parsed = result_json(r)
    check("data_count field + filter count", parsed.get("count") == 2)
    check("data_count field + filter values", parsed["values"].get("tom") == 1)

    # count missing field
    r, _, _ = call_tool(hook, "data_count", {"trait": ".dc.json", "field": "nonexistent"})
    parsed = result_json(r)
    check("data_count missing field", parsed.get("count") == 3)
    check("data_count missing field empty values", parsed.get("values") == {})

    # error cases
    r, _, _ = call_tool(hook, "data_count", {"trait": ".missing.json"})
    parsed = result_json(r)
    check("data_count missing file", "error" in parsed, f"got: {parsed}")

    r, _, _ = call_tool(hook, "data_count", {"trait": "noext"})
    parsed = result_json(r)
    check("data_count rejects non-.json", "error" in parsed, f"got: {parsed}")

    os.remove(os.path.join(tmp, "traits", ".dc.json"))

    # --- record_append + record_query + record_count ---

    # auto-create non-existent .jsonl trait
    new_jsonl = ".auto_records.jsonl"
    new_jsonl_path = os.path.join(tmp, "traits", new_jsonl)
    assert not os.path.exists(new_jsonl_path), "precondition: trait should not exist yet"

    r, _, _ = call_tool(hook, "record_append", {"trait": new_jsonl, "fields": {"type": "test"}})
    parsed = result_json(r)
    check("record_append auto-creates trait", parsed.get("success") is True)
    check("record_append auto-created file exists", os.path.exists(new_jsonl_path))

    r, _, _ = call_tool(hook, "record_query", {"trait": new_jsonl})
    check("record_query on auto-created trait", "1/" in r["result"])

    os.remove(new_jsonl_path)

    # record read tools on non-existent trait return error
    r, _, _ = call_tool(hook, "record_query", {"trait": ".nonexistent.jsonl"})
    check("record_query missing trait errors", "error" in r["result"].lower())

    r, _, _ = call_tool(hook, "record_count", {"trait": ".nonexistent.jsonl"})
    parsed = result_json(r)
    check("record_count missing trait errors", "error" in parsed)

    open(os.path.join(tmp, "traits", ".test.jsonl"), "w").write("")

    r, _, _ = call_tool(hook, "record_append", {"trait": ".test.jsonl", "fields": {"type": "note", "content": "hello"}})
    parsed = result_json(r)
    check("record_append succeeds", parsed.get("success") is True)
    check("record_append returns modified", has_key(r, "modified"))

    r, _, _ = call_tool(hook, "record_append", {"trait": ".test.jsonl", "fields": {"type": "obs", "content": "world"}})
    parsed = result_json(r)
    check("record_append second entry", parsed.get("success") is True)

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl"})
    check("record_query shows all", "2/" in r["result"])

    # --- record_query MongoDB-style filter: exact match ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"type": "note"}})
    check("record_query filter exact match", "1/" in r["result"])
    check("record_query filter content", "hello" in r["result"])

    # --- record_query MongoDB-style filter: $eq ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"type": {"$eq": "note"}}})
    check("record_query filter $eq", "1/" in r["result"])
    check("record_query filter $eq content", "hello" in r["result"])

    # --- record_query MongoDB-style filter: $in ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"type": {"$in": ["note", "obs"]}}})
    check("record_query filter $in", "2/" in r["result"])

    # --- record_query MongoDB-style filter: $regex ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"content": {"$regex": "hel"}}})
    check("record_query filter $regex", "1/" in r["result"])

    # --- record_query MongoDB-style filter: $not ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"type": {"$not": "obs"}}})
    check("record_query filter $not", "1/" in r["result"])
    check("record_query filter $not content", "hello" in r["result"])

    # --- record_query MongoDB-style filter: $ne (alias for $not) ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"type": {"$ne": "obs"}}})
    check("record_query filter $ne", "1/" in r["result"])

    # --- record_query MongoDB-style filter: $nin ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"type": {"$nin": ["obs", "error"]}}})
    check("record_query filter $nin", "1/" in r["result"])
    check("record_query filter $nin content", "hello" in r["result"])

    # --- record_query MongoDB-style filter: $lt/$gt (date range) ---

    # get timestamps from records to test date filtering
    lines = open(os.path.join(tmp, "traits", ".test.jsonl")).read().strip().splitlines()
    first_ts = json.loads(lines[0])["timestamp"]

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"timestamp": {"$lte": first_ts}}})
    check("record_query filter $lte on timestamp", "1/" in r["result"])

    # --- record_query MongoDB-style filter: $or ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"$or": [
                                                    {"type": "note"},
                                                    {"content": "world"}
                                                ]}})
    check("record_query filter $or", "2/" in r["result"])

    # --- record_query bad $regex ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"content": {"$regex": "[invalid"}}})
    check("record_query bad $regex errors", "error" in r["result"].lower())

    # --- record_query with limit/offset ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl", "limit": "1"})
    check("record_query with limit", "1/2" in r["result"])

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl", "limit": "1", "offset": "1"})
    check("record_query with offset", "world" in r["result"])

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl", "offset": "-1"})
    check("record_query negative offset", "1/2" in r["result"])
    check("record_query negative offset content", "world" in r["result"])

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl", "offset": "-2", "limit": "1"})
    check("record_query negative offset with limit", "1/2" in r["result"])
    check("record_query negative offset with limit content", "hello" in r["result"])

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl", "offset": "-1", "limit": "50"})
    check("record_query negative offset overlimit", "1/2" in r["result"])
    check("record_query negative offset overlimit content", "world" in r["result"])

    # --- record_query filter multi-field (AND) ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"type": "note", "content": "hello"}})
    check("record_query filter multi-field match", "1/" in r["result"])

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "filter": {"type": "note", "content": "world"}})
    check("record_query filter multi-field mismatch", "0/" in r["result"])

    # --- record_query with fields param (array type) ---

    r, _, _ = call_tool(hook, "record_query", {"trait": ".test.jsonl",
                                                "fields": ["type"]})
    lines = r["result"].split("\n")[1:]
    if lines and lines[0]:
        record = json.loads(lines[0])
        check("record_query fields includes type", "type" in record)
        check("record_query fields excludes content", "content" not in record)

    # --- record_query rejects non-.jsonl ---

    r, _, _ = call_tool(hook, "record_query", {"trait": "noext"})
    check("record_query rejects non-.jsonl", "error" in r["result"].lower())

    # --- record_count (absorbs record_fields) ---

    r, _, _ = call_tool(hook, "record_count", {"trait": ".test.jsonl"})
    parsed = result_json(r)
    check("record_count returns structured count", parsed.get("count") == 2, f"got: {parsed}")
    check("record_count returns field counts", "fields" in parsed)
    check("record_count fields has timestamp", parsed["fields"].get("timestamp") == 2)
    check("record_count fields has type", parsed["fields"].get("type") == 2)
    check("record_count fields has content", parsed["fields"].get("content") == 2)

    # record_count with specific field (unique value counts)
    r, _, _ = call_tool(hook, "record_count", {"trait": ".test.jsonl", "field": "type"})
    parsed = result_json(r)
    check("record_count field returns count", parsed.get("count") == 2)
    check("record_count field has field name", parsed.get("field") == "type")
    check("record_count field has values", "values" in parsed)
    check("record_count field note count", parsed["values"].get("note") == 1)
    check("record_count field obs count", parsed["values"].get("obs") == 1)

    # record_count with filter
    r, _, _ = call_tool(hook, "record_count", {"trait": ".test.jsonl",
                                                "filter": {"type": "obs"}})
    parsed = result_json(r)
    check("record_count with filter", parsed.get("count") == 1, f"got: {parsed}")

    # record_count with missing field
    r, _, _ = call_tool(hook, "record_count", {"trait": ".test.jsonl", "field": "nonexistent"})
    parsed = result_json(r)
    check("record_count missing field", parsed.get("count") == 2)
    check("record_count missing field empty values", parsed.get("values") == {})

    r, _, _ = call_tool(hook, "record_append", {"trait": "noext"})
    parsed = result_json(r)
    check("record_append rejects non-.jsonl", "error" in parsed)

    # --- nested object support ---

    nested_jsonl = ".nested_test.jsonl"
    r, _, _ = call_tool(hook, "record_append", {"trait": nested_jsonl,
        "fields": {"type": "event", "meta": {"source": "web", "tags": ["a", "b"]}, "count": 42}})
    parsed = result_json(r)
    check("record_append nested object succeeds", parsed.get("success") is True, f"got: {parsed}")

    r, _, _ = call_tool(hook, "record_append", {"trait": nested_jsonl,
        "fields": {"type": "event", "meta": {"source": "api", "tags": ["c"]}, "count": 7}})
    parsed = result_json(r)
    check("record_append nested object second", parsed.get("success") is True)

    # query all nested records
    r, _, _ = call_tool(hook, "record_query", {"trait": nested_jsonl})
    check("record_query nested shows all", "2/" in r["result"])

    # exact filter on top-level string still works
    r, _, _ = call_tool(hook, "record_query", {"trait": nested_jsonl,
        "filter": {"type": "event"}})
    check("record_query nested filter top-level", "2/" in r["result"])

    # dot-path filter on nested field
    r, _, _ = call_tool(hook, "record_query", {"trait": nested_jsonl,
        "filter": {"meta.source": "web"}})
    check("record_query dot-path filter", "1/" in r["result"])
    check("record_query dot-path filter content", "web" in r["result"])

    # dot-path filter with operator
    r, _, _ = call_tool(hook, "record_query", {"trait": nested_jsonl,
        "filter": {"meta.source": {"$in": ["web", "cli"]}}})
    check("record_query dot-path $in", "1/" in r["result"])

    # record_count with nested field grouping
    r, _, _ = call_tool(hook, "record_count", {"trait": nested_jsonl, "field": "meta.source"})
    parsed = json.loads(r["result"])
    check("record_count dot-path field", parsed.get("count") == 2, f"got: {parsed}")
    check("record_count dot-path values", parsed["values"].get("web") == 1)
    check("record_count dot-path values api", parsed["values"].get("api") == 1)

    # record_count grouping on non-string nested value uses json serialization
    r, _, _ = call_tool(hook, "record_count", {"trait": nested_jsonl, "field": "meta"})
    parsed = json.loads(r["result"])
    check("record_count nested object grouping", parsed.get("count") == 2)
    # values should be json-serialized keys (double quotes), not python str(dict) (single quotes)
    for k in parsed.get("values", {}):
        check("record_count nested grouping uses json not str", "'" not in k, f"got key: {k}")

    os.remove(os.path.join(tmp, "traits", nested_jsonl))

    os.remove(os.path.join(tmp, "traits", ".test.jsonl"))

    # --- task_create + task_update + task_comment ---

    open(os.path.join(tmp, "traits", ".tasks.json"), "w").write("{}")

    r, _, _ = call_tool(hook, "task_create", {"title": "test task"})
    parsed = result_json(r)
    check("task_create returns success", parsed.get("success") is True, f"got: {parsed}")
    check("task_create returns id", "id" in parsed, f"got: {parsed}")
    task_id = parsed["id"]
    check("task_create uuid format", len(task_id) == 36 and task_id.count("-") == 4,
          f"got: {task_id}")
    check("task_create returns modified", has_key(r, "modified"))

    r, _, _ = call_tool(hook, "task_create", {"title": "due task", "status": "blocked",
                                               "due": "2026-04-01T00:00:00+00:00"})
    parsed = result_json(r)
    check("task_create with due", parsed.get("success") is True)

    # --- task_create with description ---

    r, _, _ = call_tool(hook, "task_create", {"title": "described task",
                                               "description": "detailed info about this task"})
    parsed = result_json(r)
    check("task_create with description", parsed.get("success") is True)
    desc_id = parsed["id"]
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_create stores description", data[desc_id].get("description") == "detailed info about this task")

    r, _, _ = call_tool(hook, "task_create", {"title": "no desc task"})
    no_desc_id = result_json(r)["id"]
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_create without description omits key", "description" not in data[no_desc_id])

    # --- task_create due validation ---

    r, _, _ = call_tool(hook, "task_create", {"title": "bad due", "due": "2026-04-01"})
    parsed = result_json(r)
    check("task_create rejects due without timezone", "error" in parsed, f"got: {parsed}")

    r, _, _ = call_tool(hook, "task_create", {"title": "bad due", "due": "not-a-date"})
    parsed = result_json(r)
    check("task_create rejects invalid due", "error" in parsed, f"got: {parsed}")

    # --- task_create interval validation ---

    r, _, _ = call_tool(hook, "task_create", {"title": "bad interval", "interval": "P1D"})
    parsed = result_json(r)
    check("task_create interval requires due", "error" in parsed, f"got: {parsed}")

    r, _, _ = call_tool(hook, "task_create", {"title": "bad interval", "due": "2026-04-01T00:00:00+00:00",
                                               "interval": "bad"})
    parsed = result_json(r)
    check("task_create rejects invalid interval", "error" in parsed, f"got: {parsed}")

    r, _, _ = call_tool(hook, "task_create", {"title": "recurring task",
                                               "due": "2026-04-01T09:00:00+00:00",
                                               "interval": "P7D"})
    parsed = result_json(r)
    check("task_create with interval", parsed.get("success") is True)
    recur_id = parsed["id"]
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_create stores interval", data[recur_id].get("interval") == "P7D")

    # --- verify tasks via data_query (replaces task_read + task_list) ---

    r, _, _ = call_tool(hook, "data_query", {"trait": ".tasks.json",
                                              "filter": {"id": desc_id}})
    parsed = result_json(r)
    check("data_query task by id", desc_id in parsed)
    check("data_query task has title", parsed[desc_id].get("title") == "described task")
    check("data_query task has description", parsed[desc_id].get("description") == "detailed info about this task")

    r, _, _ = call_tool(hook, "data_query", {"trait": ".tasks.json",
                                              "filter": {"status": "open"}})
    parsed = result_json(r)
    check("data_query tasks filter status", len(parsed) >= 2)

    r, _, _ = call_tool(hook, "data_query", {"trait": ".tasks.json",
                                              "filter": {"status": "blocked"}})
    parsed = result_json(r)
    check("data_query tasks filter blocked", len(parsed) == 1)

    # task_read via data_query with id filter for non-existent task
    r, _, _ = call_tool(hook, "data_query", {"trait": ".tasks.json",
                                              "filter": {"id": "nonexistent-uuid"}})
    parsed = result_json(r)
    check("data_query missing task returns empty", parsed == {}, f"got: {parsed}")

    # task_list with fields via data_query
    r, _, _ = call_tool(hook, "data_query", {"trait": ".tasks.json",
                                              "fields": ["title", "status"]})
    parsed = result_json(r)
    first_val = list(parsed.values())[0]
    check("data_query tasks fields includes title", "title" in first_val)
    check("data_query tasks fields includes status", "status" in first_val)
    check("data_query tasks fields excludes created", "created" not in first_val)

    # clean up extra tasks for subsequent tests
    call_tool(hook, "data_delete", {"trait": ".tasks.json", "key": desc_id})
    call_tool(hook, "data_delete", {"trait": ".tasks.json", "key": no_desc_id})

    # --- task_update ---

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "status": "done"})
    parsed = result_json(r)
    check("task_update returns success", parsed.get("success") is True, f"got: {parsed}")
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_update status changed", data[task_id]["status"] == "done")
    check("task_update has updated timestamp", "updated" in data[task_id])

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "due": "no-tz"})
    parsed = result_json(r)
    check("task_update rejects due without timezone", "error" in parsed, f"got: {parsed}")

    r, _, _ = call_tool(hook, "task_update", {"id": "nonexistent-uuid"})
    parsed = result_json(r)
    check("task_update not found returns error", "error" in parsed, f"got: {parsed}")

    # --- task_update on recurring: NO recurring bump on status=done ---
    # (this is a change from old behavior — recurring bump only happens via task_comment)

    r, _, _ = call_tool(hook, "task_update", {"id": recur_id, "status": "done"})
    parsed = result_json(r)
    check("task_update recurring done returns success", parsed.get("success") is True, f"got: {parsed}")
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_update recurring sets done (no bump)", data[recur_id]["status"] == "done")
    check("task_update recurring due unchanged", data[recur_id]["due"] == "2026-04-01T09:00:00+00:00",
          f"got: {data[recur_id]['due']}")

    # reset for comment tests
    call_tool(hook, "task_update", {"id": recur_id, "status": "open"})

    # --- task_update description ---

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "description": "added a description"})
    parsed = result_json(r)
    check("task_update description", parsed.get("success") is True)
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_update description stored", data[task_id].get("description") == "added a description")

    # --- task_update add interval to existing ---

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "status": "open",
                                               "due": "2026-05-01T00:00:00+00:00",
                                               "interval": "P1M"})
    parsed = result_json(r)
    check("task_update add interval", parsed.get("success") is True)
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_update interval stored", data[task_id].get("interval") == "P1M")

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "interval": "bad"})
    parsed = result_json(r)
    check("task_update rejects invalid interval", "error" in parsed, f"got: {parsed}")

    # --- task deletion via data_delete (replaces task_delete) ---

    r, _, _ = call_tool(hook, "data_delete", {"trait": ".tasks.json", "key": task_id})
    parsed = result_json(r)
    check("data_delete task succeeds", parsed.get("success") is True)
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("data_delete task removed", task_id not in data)

    r, _, _ = call_tool(hook, "data_delete", {"trait": ".tasks.json", "key": "nonexistent-uuid"})
    parsed = result_json(r)
    check("data_delete missing task returns error", "error" in parsed)

    # --- task_comment ---

    # create a fresh task for comment tests
    r, _, _ = call_tool(hook, "task_create", {"title": "commentable task"})
    comment_task_id = result_json(r)["id"]

    r, _, _ = call_tool(hook, "task_comment", {"id": comment_task_id, "text": "first update"})
    parsed = result_json(r)
    check("task_comment returns success", parsed.get("success") is True, f"got: {parsed}")
    check("task_comment returns modified", has_key(r, "modified"))

    # verify comment stored in .tasks_comments.jsonl
    comments_path = os.path.join(tmp, "traits", ".tasks_comments.jsonl")
    lines = open(comments_path).read().strip().splitlines()
    check("task_comment creates jsonl entry", len(lines) == 1)
    entry = json.loads(lines[0])
    check("task_comment has task_id", entry.get("task_id") == comment_task_id)
    check("task_comment has text", entry.get("text") == "first update")
    check("task_comment has timestamp", "timestamp" in entry)

    # add second comment
    r, _, _ = call_tool(hook, "task_comment", {"id": comment_task_id, "text": "second update"})
    parsed = result_json(r)
    check("task_comment second succeeds", parsed.get("success") is True)
    lines = open(comments_path).read().strip().splitlines()
    check("task_comment appends", len(lines) == 2)

    # task_comment updates the task's updated timestamp
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_comment updates task timestamp", "updated" in data[comment_task_id])

    # task_comment requires text
    r, _, _ = call_tool(hook, "task_comment", {"id": comment_task_id})
    parsed = result_json(r)
    check("task_comment requires text", "error" in parsed, f"got: {parsed}")

    # task_comment validates task exists
    r, _, _ = call_tool(hook, "task_comment", {"id": "nonexistent-uuid", "text": "orphan"})
    parsed = result_json(r)
    check("task_comment rejects missing task", "error" in parsed, f"got: {parsed}")

    # comments filterable via record_query
    r, _, _ = call_tool(hook, "record_query", {"trait": ".tasks_comments.jsonl",
                                                "filter": {"task_id": comment_task_id}})
    check("record_query filter finds task comments", "2/2 records" in r["result"])

    # filter with non-matching value returns empty
    r, _, _ = call_tool(hook, "record_query", {"trait": ".tasks_comments.jsonl",
                                                "filter": {"task_id": "nonexistent"}})
    check("record_query filter no match", "0/0 records" in r["result"])

    # --- task_comment on recurring task bumps due ---

    r, _, _ = call_tool(hook, "task_create", {"title": "recurring commentable",
                                               "due": "2026-04-01T09:00:00+00:00",
                                               "interval": "P7D"})
    recur_comment_id = result_json(r)["id"]

    r, _, _ = call_tool(hook, "task_comment", {"id": recur_comment_id, "text": "weekly check-in"})
    parsed = result_json(r)
    check("task_comment recurring returns success", parsed.get("success") is True)
    check("task_comment recurring returns due", "due" in parsed, f"got: {parsed}")
    check("task_comment recurring due value", parsed.get("due") == "2026-04-08T09:00:00.000+00:00",
          f"got: {parsed.get('due')}")
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_comment recurring due bumped in file", data[recur_comment_id]["due"] == "2026-04-08T09:00:00.000+00:00",
          f"got: {data[recur_comment_id]['due']}")
    check("task_comment recurring stays open", data[recur_comment_id]["status"] == "open")

    # cleanup
    call_tool(hook, "data_delete", {"trait": ".tasks.json", "key": comment_task_id})
    call_tool(hook, "data_delete", {"trait": ".tasks.json", "key": recur_comment_id})

    os.remove(os.path.join(tmp, "traits", ".tasks.json"))
    if os.path.exists(comments_path):
        os.remove(comments_path)

    # --- journal via record tools (no dedicated journal tools) ---

    open(os.path.join(tmp, "traits", ".journal.jsonl"), "w").write("")

    r, _, _ = call_tool(hook, "record_append", {"trait": ".journal.jsonl",
                                                 "fields": {"type": "thought", "content": "i exist"}})
    parsed = result_json(r)
    check("journal via record_append succeeds", parsed.get("success") is True)
    check("journal via record_append returns modified", has_key(r, "modified"))

    r, _, _ = call_tool(hook, "record_append", {"trait": ".journal.jsonl",
                                                 "fields": {"type": "obs", "content": "humans sleep"}})

    r, _, _ = call_tool(hook, "record_append", {"trait": ".journal.jsonl",
                                                 "fields": {"type": "event", "content": "auth broke",
                                                            "severity": "high", "tags": "auth,prod"}})
    lines = open(os.path.join(tmp, "traits", ".journal.jsonl")).read().strip().splitlines()
    entry = json.loads(lines[-1])
    check("journal entry has severity", entry.get("severity") == "high")
    check("journal entry has tags", entry.get("tags") == "auth,prod")
    check("journal entry has timestamp", "timestamp" in entry)

    r, _, _ = call_tool(hook, "record_query", {"trait": ".journal.jsonl"})
    check("journal record_query shows all", "3/" in r["result"])

    r, _, _ = call_tool(hook, "record_query", {"trait": ".journal.jsonl",
                                                "filter": {"type": "thought"}})
    check("journal record_query filter type", "1/" in r["result"])

    r, _, _ = call_tool(hook, "record_query", {"trait": ".journal.jsonl",
                                                "filter": {"content": {"$regex": "exist"}}})
    check("journal record_query $regex", "1/" in r["result"])

    r, _, _ = call_tool(hook, "record_count", {"trait": ".journal.jsonl"})
    parsed = result_json(r)
    check("journal record_count total", parsed.get("count") == 3, f"got: {parsed}")

    r, _, _ = call_tool(hook, "record_count", {"trait": ".journal.jsonl", "field": "type"})
    parsed = result_json(r)
    check("journal record_count field type", parsed["values"].get("thought") == 1)
    check("journal record_count field obs", parsed["values"].get("obs") == 1)

    os.remove(os.path.join(tmp, "traits", ".journal.jsonl"))

    # --- discover final tool list ---

    r, _, _ = call_hook(hook, "discover")
    names = sorted(t["name"] for t in r["tools"])
    expected_names = sorted([
        "trait_list", "trait_read", "trait_write", "trait_edit", "trait_append", "trait_delete", "trait_move",
        "data_query", "data_update", "data_delete", "data_append", "data_count",
        "record_append", "record_query", "record_count",
        "task_create", "task_update", "task_comment",
    ])
    check("discover final tool list matches", names == expected_names,
          f"expected: {expected_names}\ngot: {names}")

    # --- discover typed params ---

    tools_by_name = {t["name"]: t for t in r["tools"]}
    value_param = tools_by_name["data_update"]["parameters"].get("value", {})
    check("data_update value param is typed", isinstance(value_param, dict) and value_param.get("type") == "any",
          f"got: {value_param}")

    # --- datetime format consistency ---
    # all timestamps must use evolve_datetime canonical format: YYYY-MM-DDTHH:MM:SS.sss+HH:MM

    import re as re_mod
    DT_RE = re_mod.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}$")

    # format_iso produces canonical format
    from datetime import datetime as dt_cls, timezone as tz_cls
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))
    from persona import format_iso
    now_fmt = format_iso(dt_cls.now(tz_cls.utc))
    check("format_iso uses offset not Z", now_fmt.endswith("+00:00"), f"got: {now_fmt}")
    check("format_iso matches canonical pattern", bool(DT_RE.match(now_fmt)), f"got: {now_fmt}")

    # format_iso with non-UTC input still outputs +00:00
    from datetime import timedelta as td_cls
    est = tz_cls(td_cls(hours=-5))
    est_fmt = format_iso(dt_cls(2026, 4, 1, 12, 0, 0, tzinfo=est))
    check("format_iso converts to UTC offset", est_fmt == "2026-04-01T17:00:00.000+00:00", f"got: {est_fmt}")

    # record_append timestamps use canonical format
    r, _, _ = call_tool(hook, "record_append", {"trait": ".dt_test.jsonl", "fields": {"v": "1"}})
    dt_line = open(os.path.join(tmp, "traits", ".dt_test.jsonl")).read().strip()
    dt_entry = json.loads(dt_line)
    check("record timestamp uses offset format", bool(DT_RE.match(dt_entry["timestamp"])),
          f"got: {dt_entry['timestamp']}")
    check("record timestamp ends with +00:00", dt_entry["timestamp"].endswith("+00:00"),
          f"got: {dt_entry['timestamp']}")
    os.remove(os.path.join(tmp, "traits", ".dt_test.jsonl"))

    # task_create timestamps use canonical format
    open(os.path.join(tmp, "traits", ".tasks.json"), "w").write("{}")
    r, _, _ = call_tool(hook, "task_create", {"title": "dt format check"})
    tid = result_json(r)["id"]
    tasks_data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task created timestamp canonical", bool(DT_RE.match(tasks_data[tid]["created"])),
          f"got: {tasks_data[tid]['created']}")
    check("task updated timestamp canonical", bool(DT_RE.match(tasks_data[tid]["updated"])),
          f"got: {tasks_data[tid]['updated']}")
    os.remove(os.path.join(tmp, "traits", ".tasks.json"))

    # journal timestamps use canonical format (via record_append)
    open(os.path.join(tmp, "traits", ".journal.jsonl"), "w").write("")
    r, _, _ = call_tool(hook, "record_append", {"trait": ".journal.jsonl",
                                                 "fields": {"type": "test", "content": "dt check"}})
    j_line = open(os.path.join(tmp, "traits", ".journal.jsonl")).read().strip()
    j_entry = json.loads(j_line)
    check("journal timestamp uses offset format", bool(DT_RE.match(j_entry["timestamp"])),
          f"got: {j_entry['timestamp']}")
    os.remove(os.path.join(tmp, "traits", ".journal.jsonl"))

    # ISO_DT_DESC references offset format not Z
    from persona import ISO_DT_DESC
    check("ISO_DT_DESC uses offset example", "+00:00" in ISO_DT_DESC, f"got: {ISO_DT_DESC}")
    check("ISO_DT_DESC does not use Z example", "000Z" not in ISO_DT_DESC, f"got: {ISO_DT_DESC}")

    # --- prompt size tracking ---

    from persona import system_prompt as sp_fn, tool_defs as td_fn
    sp_text = "\n".join(sp_fn("chat"))
    td_text = json.dumps(td_fn())
    td_count = len(td_fn())
    print(f"\nprompt: {len(sp_text)} chars, tools: {len(td_text)} chars ({td_count}), total: {len(sp_text) + len(td_text)} chars")

finally:
    shutil.rmtree(tmp)

# --- summary ---

total = PASS + FAIL
print(f"\n{total} tests, {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
