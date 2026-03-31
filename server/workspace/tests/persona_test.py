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
    for expected in ("trait_list", "trait_read", "trait_write", "trait_edit"):
        check(f"discover includes {expected}", expected in names, f"got: {names}")
    check("discover returns at least 4 tools", len(r["tools"]) >= 4, f"got: {len(r['tools'])}")
    check("discover logs tool names", any("tools:" in l for l in logs))

    # --- discover tool parameter schemas ---

    tools_by_name = {t["name"]: t for t in r["tools"]}
    expected_counts = {"trait_list": 1, "trait_read": 3, "trait_write": 2, "trait_edit": 4}
    for name, count in expected_counts.items():
        actual = len(tools_by_name[name]["parameters"])
        check(f"{name} has {count} params", actual == count, f"got: {actual}")

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
    # verify inlined trait uses exact format_trait structure (no injected prefixes)
    core_match = re.search(r"\{file:.*/CORE\.md\}\n(.*?)\n", system_text, re.DOTALL)
    check("inlined core trait is verbatim", core_match and core_match.group(1) == "core content",
          f"got: {core_match.group(1)!r}" if core_match else "no match")
    check("request lists lowercase trait", "notes.md" in system_text)
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
    check("trait_list includes core", "CORE.md" in r["result"])
    check("trait_list includes lowercase", "notes.md" in r["result"])
    check("trait_list excludes hidden", ".hidden.md" not in r["result"])
    check("trait_list includes digits+underscore core", "V2_PLAN.md" in r["result"])
    check("trait_list includes dot-stem any-ext core", "MY.TRAIT.txt" in r["result"])
    check("trait_list logs tool name", any("tool=trait_list" in l for l in logs))

    r, _, _ = call_tool(hook, "trait_list", {"include_hidden": "true"})
    check("trait_list hidden includes hidden", ".hidden.md" in r["result"])
    check("trait_list hidden includes core", "CORE.md" in r["result"])

    # bool args from JSON (LLM sends true not "true")
    r, _, _ = call_tool(hook, "trait_list", {"include_hidden": True})
    check("trait_list bool true includes hidden", ".hidden.md" in r["result"])

    r, _, _ = call_tool(hook, "trait_list", {"include_hidden": False})
    check("trait_list bool false excludes hidden", ".hidden.md" not in r["result"])

    # trait_list includes subdirectory traits with relative paths
    os.makedirs(os.path.join(tmp, "traits", "topics"), exist_ok=True)
    open(os.path.join(tmp, "traits", "topics", "music.md"), "w").write("f")
    r, _, _ = call_tool(hook, "trait_list")
    check("trait_list includes subdir trait", "topics/music.md" in r["result"])
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
    # format_trait must produce: \n{file:<path>/A.md}\n<content>\n
    trait_text = r["result"]
    check("format_trait has file tag", re.search(r"\{file:.*/A\.md\}", trait_text) is not None,
          f"got: {trait_text!r}")
    # content must appear verbatim between file tag and end, with no extra prefix/suffix per line
    file_tag_end = trait_text.find("}")
    after_tag = trait_text[file_tag_end + 1:].strip() if file_tag_end >= 0 else ""
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
    check("trait_write result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("trait_write returns success", "successfully wrote" in r["result"])
    check("trait_write reports modified", has_key(r, "modified"))
    check("trait_write modified list correct", r.get("modified") == ["NEW.md"])
    content = open(os.path.join(tmp, "traits", "NEW.md")).read()
    check("trait_write wrote file", content == "hello world")

    # --- trait_edit ---

    open(os.path.join(tmp, "traits", "PATCH.md"), "w").write("old text here")

    r, _, _ = call_tool(hook, "trait_edit", {"trait": "PATCH.md", "oldString": "old text", "newString": "new text"})
    check("trait_edit returns result key", has_key(r, "result"))
    check("trait_edit result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("trait_edit returns success", "successfully edited" in r["result"])
    check("trait_edit reports modified", has_key(r, "modified"))
    content = open(os.path.join(tmp, "traits", "PATCH.md")).read()
    check("trait_edit updated file", content == "new text here")

    r, _, _ = call_tool(hook, "trait_edit", {"trait": "PATCH.md", "oldString": "nonexistent", "newString": "x"})
    check("trait_edit not found", "not found" in r["result"])

    open(os.path.join(tmp, "traits", "DUP.md"), "w").write("aaa")
    r, _, _ = call_tool(hook, "trait_edit", {"trait": "DUP.md", "oldString": "a", "newString": "b"})
    check("trait_edit multiple matches fails", "3 matches" in r["result"])

    # --- trait_delete ---

    open(os.path.join(tmp, "traits", "DEL.md"), "w").write("delete me")

    r, logs, _ = call_tool(hook, "trait_delete", {"trait": "DEL.md"})
    check("trait_delete returns result key", has_key(r, "result"))
    check("trait_delete result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("trait_delete returns success", "successfully deleted" in r["result"])
    check("trait_delete reports modified", r.get("modified") == ["DEL.md"])
    check("trait_delete removed file", not os.path.exists(os.path.join(tmp, "traits", "DEL.md")))
    check("trait_delete logs tool name", any("tool=trait_delete" in l for l in logs))

    r, _, _ = call_tool(hook, "trait_delete", {"trait": "DEL.md"})
    check("trait_delete not found", "not found" in r["result"])

    # --- trait_move ---

    open(os.path.join(tmp, "traits", "SRC.md"), "w").write("move me")

    r, logs, _ = call_tool(hook, "trait_move", {"old_trait": "SRC.md", "new_trait": "DST.md"})
    check("trait_move returns result key", has_key(r, "result"))
    check("trait_move result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("trait_move returns success", "moved" in r["result"])
    check("trait_move reports both modified", set(r.get("modified", [])) == {"SRC.md", "DST.md"})
    check("trait_move removed src", not os.path.exists(os.path.join(tmp, "traits", "SRC.md")))
    check("trait_move created dst", os.path.exists(os.path.join(tmp, "traits", "DST.md")))
    content = open(os.path.join(tmp, "traits", "DST.md")).read()
    check("trait_move preserved content", content == "move me")
    check("trait_move logs tool name", any("tool=trait_move" in l for l in logs))

    r, _, _ = call_tool(hook, "trait_move", {"old_trait": "MISSING.md", "new_trait": "X.md"})
    check("trait_move not found", "not found" in r["result"])

    open(os.path.join(tmp, "traits", "EXIST.md"), "w").write("x")
    r, _, _ = call_tool(hook, "trait_move", {"old_trait": "DST.md", "new_trait": "EXIST.md"})
    check("trait_move already exists", "already exists" in r["result"])

    for f in ("DST.md", "EXIST.md"):
        os.remove(os.path.join(tmp, "traits", f))

    # --- trait directory handling ---

    # trait_write creates parent directories
    r, _, _ = call_tool(hook, "trait_write", {"trait": "sub/deep/NESTED.md", "content": "nested"})
    check("trait_write creates parent dirs", os.path.exists(os.path.join(tmp, "traits", "sub", "deep", "NESTED.md")))
    check("trait_write nested success", "successfully wrote" in r["result"])
    content = open(os.path.join(tmp, "traits", "sub", "deep", "NESTED.md")).read()
    check("trait_write nested content correct", content == "nested")

    # trait_delete removes empty parent directories
    r, _, _ = call_tool(hook, "trait_delete", {"trait": "sub/deep/NESTED.md"})
    check("trait_delete nested success", "successfully deleted" in r["result"])
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
    check("trait_move to subdir success", "moved" in r["result"])
    content = open(os.path.join(tmp, "traits", "newdir", "deep", "MVDST.md")).read()
    check("trait_move to subdir content", content == "move to subdir")

    # trait_move cleans up empty source parent directories
    r, _, _ = call_tool(hook, "trait_move", {"old_trait": "newdir/deep/MVDST.md", "new_trait": "MVBACK.md"})
    check("trait_move from subdir success", "moved" in r["result"])
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

    # --- avatar prefix on tool results ---

    open(os.path.join(tmp, "traits", "AV.md"), "w").write("avatar test")

    r, _, _ = call_tool(hook, "trait_list")
    check("trait_list result has avatar prefix", r.get("result", "").startswith("🌀 "))

    r, _, _ = call_tool(hook, "trait_read", {"trait": "AV.md"})
    check("trait_read result has avatar prefix", r.get("result", "").startswith("🌀"))

    r, _, _ = call_tool(hook, "trait_write", {"trait": "AV.md", "content": "x"})
    check("trait_write result has avatar prefix", r.get("result", "").startswith("🌀 "))

    r, _, _ = call_tool(hook, "trait_delete", {"trait": "AV.md"})
    check("trait_delete result has avatar prefix", r.get("result", "").startswith("🌀 "))

    # --- avatar prefix on hook debug logs ---

    _, logs, _ = call_hook(hook, "mutate_request")
    check("hook debug logs have avatar prefix", any(l.startswith("[🌀]") for l in logs),
          f"got: {logs}")

    # --- unknown tool ---

    r, _, _ = call_tool(hook, "nonexistent")
    check("unknown tool returns result key", has_key(r, "result"))
    check("unknown tool returns error", "unknown tool" in r["result"])

    # --- bad args ---

    r, _, _ = call_tool(hook, "trait_read", {"wrong": "param"})
    check("bad args returns result key", has_key(r, "result"))
    check("bad args returns tool error", "tool error" in r["result"])

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
        check(f"trait_read rejects traversal ({bad})", "error" in r.get("result", "").lower() or "invalid" in r.get("result", "").lower(),
              f"got: {r.get('result', '')}")

        r, _, _ = call_tool(hook, "trait_write", {"trait": bad, "content": "pwned"})
        check(f"trait_write rejects traversal ({bad})", "error" in r.get("result", "").lower() or "invalid" in r.get("result", "").lower(),
              f"got: {r.get('result', '')}")
        # verify no file was written at the traversal target
        target = os.path.normpath(os.path.join(tmp, "traits", bad))
        if os.path.exists(target):
            check(f"trait_write did not modify outside traits ({bad})",
                  open(target).read() != "pwned", f"file at {target} was overwritten")
        else:
            check(f"trait_write did not write outside traits ({bad})", True)

        r, _, _ = call_tool(hook, "trait_edit", {"trait": bad, "oldString": "x", "newString": "y"})
        check(f"trait_edit rejects traversal ({bad})", "error" in r.get("result", "").lower() or "invalid" in r.get("result", "").lower(),
              f"got: {r.get('result', '')}")

        open(os.path.join(tmp, "traits", "SAFE.md"), "w").write("safe")
        r, _, _ = call_tool(hook, "trait_delete", {"trait": bad})
        check(f"trait_delete rejects traversal ({bad})", "error" in r.get("result", "").lower() or "invalid" in r.get("result", "").lower(),
              f"got: {r.get('result', '')}")

        r, _, _ = call_tool(hook, "trait_move", {"old_trait": "SAFE.md", "new_trait": bad})
        check(f"trait_move rejects traversal dst ({bad})", "error" in r.get("result", "").lower() or "invalid" in r.get("result", "").lower(),
              f"got: {r.get('result', '')}")

        r, _, _ = call_tool(hook, "trait_move", {"old_trait": bad, "new_trait": "SAFE2.md"})
        check(f"trait_move rejects traversal src ({bad})", "error" in r.get("result", "").lower() or "invalid" in r.get("result", "").lower(),
              f"got: {r.get('result', '')}")

        os.remove(os.path.join(tmp, "traits", "SAFE.md"))

    # absolute path traversal
    r, _, _ = call_tool(hook, "trait_write", {"trait": "/etc/passwd", "content": "pwned"})
    check("trait_write rejects absolute path", "error" in r.get("result", "").lower() or "invalid" in r.get("result", "").lower(),
          f"got: {r.get('result', '')}")

    # --- data_read ---

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('{"a": 1, "b": {"c": [10, 20, 30]}}')

    r, _, _ = call_tool(hook, "data_read", {"trait": ".test.json"})
    check("data_read returns full object", '"a": 1' in r["result"])

    r, _, _ = call_tool(hook, "data_read", {"trait": ".test.json", "key": "a"})
    check("data_read key selector", "1" in r["result"])

    r, _, _ = call_tool(hook, "data_read", {"trait": ".test.json", "key": "b.c"})
    check("data_read nested selector", "10" in r["result"] and "20" in r["result"] and "30" in r["result"])

    r, _, _ = call_tool(hook, "data_read", {"trait": ".test.json", "key": "b.c.1"})
    check("data_read array index", "20" in r["result"])

    r, _, _ = call_tool(hook, "data_read", {"trait": "noext"})
    check("data_read rejects non-.json", "error" in r["result"].lower())

    r, _, _ = call_tool(hook, "data_read", {"trait": ".missing.json"})
    check("data_read missing file", "error" in r["result"].lower())

    # --- data_update ---

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "a", "value": 42})
    check("data_update sets value", "successfully updated" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_update value correct", data["a"] == 42)

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "b.c.0", "value": 99})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_update nested array index", data["b"]["c"][0] == 99)

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "new_key", "value": "hello"})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_update creates new key in dict", data.get("new_key") == "hello")

    r, _, _ = call_tool(hook, "data_update", {"trait": ".test.json", "key": "x.y.z", "value": 1})
    check("data_update unreachable key fails", "not reachable" in r["result"])

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
    check("data_update auto-creates trait", "successfully updated" in r["result"])
    data = json.loads(open(new_path).read())
    check("data_update auto-created content", data == {"foo": "bar"})

    r, _, _ = call_tool(hook, "data_update", {"trait": ".autocreated2.json", "value": [1, 2, 3]})
    data = json.loads(open(os.path.join(tmp, "traits", ".autocreated2.json")).read())
    check("data_update auto-creates with whole-file overwrite", data == [1, 2, 3])

    # --- data_delete ---

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('{"x": 1, "y": 2, "arr": [10, 20, 30]}')

    r, _, _ = call_tool(hook, "data_delete", {"trait": ".test.json", "key": "x"})
    check("data_delete removes key", "successfully deleted" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_delete key gone", "x" not in data)
    check("data_delete other keys intact", data.get("y") == 2)

    r, _, _ = call_tool(hook, "data_delete", {"trait": ".test.json", "key": "arr.1"})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_delete array index", data["arr"] == [10, 30])

    r, _, _ = call_tool(hook, "data_delete", {"trait": ".test.json", "key": "nonexistent"})
    check("data_delete missing key fails", "not found" in r["result"])

    # --- data_append ---

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('{"items": [1, 2]}')

    r, _, _ = call_tool(hook, "data_append", {"trait": ".test.json", "key": "items", "value": 3})
    check("data_append to nested array", "successfully appended" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_append value correct", data["items"] == [1, 2, 3])

    open(os.path.join(tmp, "traits", ".test.json"), "w").write('[1, 2]')
    r, _, _ = call_tool(hook, "data_append", {"trait": ".test.json", "value": 3})
    data = json.loads(open(os.path.join(tmp, "traits", ".test.json")).read())
    check("data_append to root array", data == [1, 2, 3])

    r, _, _ = call_tool(hook, "data_append", {"trait": ".test.json", "key": "notarray", "value": 1})
    check("data_append non-array fails", "not an array" in r["result"])

    # auto-create non-existent trait for data_append
    new_trait = ".append_auto.json"
    new_path = os.path.join(tmp, "traits", new_trait)
    assert not os.path.exists(new_path), "precondition: trait should not exist yet"

    r, _, _ = call_tool(hook, "data_append", {"trait": new_trait, "value": "first"})
    check("data_append auto-creates trait", "successfully appended" in r["result"])
    data = json.loads(open(new_path).read())
    check("data_append auto-created as array", data == ["first"])

    os.remove(new_path)
    os.remove(os.path.join(tmp, "traits", ".test.json"))

    # --- data_list ---

    open(os.path.join(tmp, "traits", ".dl.json"), "w").write(json.dumps({
        "id1": {"title": "alpha", "status": "open", "due": "2026-04-01T00:00:00+00:00"},
        "id2": {"title": "beta", "status": "done", "due": "2026-05-01T00:00:00+00:00"},
        "id3": {"title": "gamma", "status": "open", "owner": "tom"},
    }))

    r, _, _ = call_tool(hook, "data_list", {"trait": ".dl.json"})
    check("data_list returns all", "3/3 records" in r["result"])
    lines = r["result"].split("\n")[1:]
    first = json.loads(lines[0])
    check("data_list injects id", "id" in first)
    first_keys = list(first.keys())
    check("data_list id first", first_keys[0] == "id")
    check("data_list keys sorted", first_keys[1:] == sorted(first_keys[1:]))

    r, _, _ = call_tool(hook, "data_list", {"trait": ".dl.json", "filter": {"status": "open"}})
    check("data_list filter", "2/2 records" in r["result"])

    r, _, _ = call_tool(hook, "data_list", {"trait": ".dl.json", "filter": {"owner": "tom"}})
    check("data_list filter arbitrary", "1/1 records" in r["result"])

    r, _, _ = call_tool(hook, "data_list", {"trait": ".dl.json", "pattern": "alpha"})
    check("data_list pattern", "1/1 records" in r["result"])

    r, _, _ = call_tool(hook, "data_list", {"trait": ".dl.json",
                                             "before": "2026-04-15T00:00:00+00:00",
                                             "date_field": "due"})
    check("data_list before with date_field", "1/1 records" in r["result"])

    r, _, _ = call_tool(hook, "data_list", {"trait": ".dl.json", "limit": "1"})
    check("data_list limit", "1/3 records" in r["result"])

    r, _, _ = call_tool(hook, "data_list", {"trait": ".dl.json", "limit": "1", "offset": "1"})
    check("data_list offset", "1/3 records" in r["result"])
    check("data_list offset correct record", "beta" in r["result"])

    r, _, _ = call_tool(hook, "data_list", {"trait": ".dl.json", "fields": "title,status"})
    record = json.loads(r["result"].split("\n")[1])
    check("data_list fields includes title", "title" in record)
    check("data_list fields includes status", "status" in record)
    check("data_list fields includes id", "id" in record)
    check("data_list fields excludes due", "due" not in record)

    r, _, _ = call_tool(hook, "data_list", {"trait": "noext"})
    check("data_list rejects non-.json", "error" in r["result"].lower())

    os.remove(os.path.join(tmp, "traits", ".dl.json"))

    # --- record_append + record_list + record_count ---

    # auto-create non-existent .jsonl trait
    new_jsonl = ".auto_records.jsonl"
    new_jsonl_path = os.path.join(tmp, "traits", new_jsonl)
    assert not os.path.exists(new_jsonl_path), "precondition: trait should not exist yet"

    r, _, _ = call_tool(hook, "record_append", {"trait": new_jsonl, "fields": {"type": "test"}})
    check("record_append auto-creates trait", "successfully appended" in r["result"])
    check("record_append auto-created file exists", os.path.exists(new_jsonl_path))

    r, _, _ = call_tool(hook, "record_list", {"trait": new_jsonl})
    check("record_list on auto-created trait", "1/" in r["result"])

    os.remove(new_jsonl_path)

    # record read tools on non-existent trait return error
    r, _, _ = call_tool(hook, "record_list", {"trait": ".nonexistent.jsonl"})
    check("record_list missing trait errors", "error" in r["result"].lower())

    r, _, _ = call_tool(hook, "record_count", {"trait": ".nonexistent.jsonl"})
    check("record_count missing trait errors", "error" in r["result"].lower())

    r, _, _ = call_tool(hook, "record_list", {"trait": ".nonexistent.jsonl", "pattern": "anything"})
    check("record_list pattern missing trait errors", "error" in r["result"].lower())

    open(os.path.join(tmp, "traits", ".test.jsonl"), "w").write("")

    r, _, _ = call_tool(hook, "record_append", {"trait": ".test.jsonl", "fields": {"type": "note", "content": "hello"}})
    check("record_append succeeds", "successfully appended" in r["result"])
    check("record_append returns modified", has_key(r, "modified"))

    r, _, _ = call_tool(hook, "record_append", {"trait": ".test.jsonl", "fields": {"type": "obs", "content": "world"}})
    check("record_append second entry", "successfully appended" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl"})
    check("record_list shows all", "2/" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl",
                                               "filter": {"type": "note"}})
    check("record_list filter by type", "1/" in r["result"])
    check("record_list filter content", "hello" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "limit": "1"})
    check("record_list with limit", "1/2" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "limit": "1", "offset": "1"})
    check("record_list with offset", "world" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "offset": "-1"})
    check("record_list negative offset", "1/2" in r["result"])
    check("record_list negative offset content", "world" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "offset": "-2", "limit": "1"})
    check("record_list negative offset with limit", "1/2" in r["result"])
    check("record_list negative offset with limit content", "hello" in r["result"])

    # --- record_list filter object ---

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl",
                                               "filter": {"type": "note", "content": "hello"}})
    check("record_list filter multi-field match", "1/" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl",
                                               "filter": {"type": "note", "content": "world"}})
    check("record_list filter multi-field mismatch", "0/" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "offset": "-1", "limit": "50"})
    check("record_list negative offset overlimit", "1/2" in r["result"])
    check("record_list negative offset overlimit content", "world" in r["result"])

    r, _, _ = call_tool(hook, "record_count", {"trait": ".test.jsonl"})
    check("record_count total", "2 records" in r["result"])

    r, _, _ = call_tool(hook, "record_count", {"trait": ".test.jsonl",
                                                "filter": {"type": "obs"}})
    check("record_count by filter", "1 records" in r["result"])

    # --- record_list pattern (regex search, replaces record_search) ---

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "pattern": "hello"})
    check("record_list pattern finds match", "1/" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "pattern": "zzz"})
    check("record_list pattern no match", "0/" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "pattern": "[invalid"})
    check("record_list pattern bad regex", "invalid regex" in r["result"].lower())

    # pattern + filter combined
    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "pattern": "hello",
                                               "filter": {"type": "note"}})
    check("record_list pattern + filter match", "1/" in r["result"])

    r, _, _ = call_tool(hook, "record_list", {"trait": ".test.jsonl", "pattern": "hello",
                                               "filter": {"type": "obs"}})
    check("record_list pattern + filter mismatch", "0/" in r["result"])

    r, _, _ = call_tool(hook, "record_append", {"trait": "noext"})
    check("record_append rejects non-.jsonl", "error" in r["result"].lower())

    # --- record_fields ---

    r, _, _ = call_tool(hook, "record_fields", {"trait": ".test.jsonl"})
    check("record_fields lists field names", "timestamp" in r["result"])
    check("record_fields includes type", "type" in r["result"])
    check("record_fields includes content", "content" in r["result"])

    r, _, _ = call_tool(hook, "record_fields", {"trait": ".test.jsonl", "field": "type"})
    check("record_fields unique values for type", "note" in r["result"])
    check("record_fields unique values includes obs", "obs" in r["result"])

    r, _, _ = call_tool(hook, "record_fields", {"trait": ".test.jsonl", "field": "nonexistent"})
    check("record_fields no values for missing field", "0 unique" in r["result"])

    r, _, _ = call_tool(hook, "record_fields", {"trait": ".nonexistent.jsonl"})
    check("record_fields missing trait errors", "error" in r["result"].lower())

    os.remove(os.path.join(tmp, "traits", ".test.jsonl"))

    # --- task_create + task_list + task_update + task_delete ---

    open(os.path.join(tmp, "traits", ".tasks.json"), "w").write("{}")

    r, _, _ = call_tool(hook, "task_create", {"title": "test task"})
    check("task_create succeeds", "created task" in r["result"])
    check("task_create returns modified", has_key(r, "modified"))
    # extract uuid from result
    task_id = r["result"].split("created task ")[1].split(":")[0].strip()
    check("task_create uuid format", len(task_id) == 36 and task_id.count("-") == 4,
          f"got: {task_id}")

    r, _, _ = call_tool(hook, "task_create", {"title": "due task", "status": "blocked",
                                               "due": "2026-04-01T00:00:00+00:00"})
    check("task_create with due", "created task" in r["result"])

    # --- task_create with description ---

    r, _, _ = call_tool(hook, "task_create", {"title": "described task",
                                               "description": "detailed info about this task"})
    check("task_create with description", "created task" in r["result"])
    desc_id = r["result"].split("created task ")[1].split(":")[0].strip()
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_create stores description", data[desc_id].get("description") == "detailed info about this task")

    r, _, _ = call_tool(hook, "task_create", {"title": "no desc task"})
    no_desc_id = r["result"].split("created task ")[1].split(":")[0].strip()
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_create without description omits key", "description" not in data[no_desc_id])

    # --- task_create due validation ---

    r, _, _ = call_tool(hook, "task_create", {"title": "bad due", "due": "2026-04-01"})
    check("task_create rejects due without timezone", "error" in r["result"].lower(),
          f"got: {r['result']}")

    r, _, _ = call_tool(hook, "task_create", {"title": "bad due", "due": "not-a-date"})
    check("task_create rejects invalid due", "error" in r["result"].lower(),
          f"got: {r['result']}")

    # --- task_create interval validation ---

    r, _, _ = call_tool(hook, "task_create", {"title": "bad interval", "interval": "P1D"})
    check("task_create interval requires due", "error" in r["result"].lower(),
          f"got: {r['result']}")

    r, _, _ = call_tool(hook, "task_create", {"title": "bad interval", "due": "2026-04-01T00:00:00+00:00",
                                               "interval": "bad"})
    check("task_create rejects invalid interval", "error" in r["result"].lower(),
          f"got: {r['result']}")

    r, _, _ = call_tool(hook, "task_create", {"title": "recurring task",
                                               "due": "2026-04-01T09:00:00+00:00",
                                               "interval": "P7D"})
    check("task_create with interval", "created task" in r["result"])
    recur_id = r["result"].split("created task ")[1].split(":")[0].strip()
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_create stores interval", data[recur_id].get("interval") == "P7D")

    # --- task_read ---

    r, _, _ = call_tool(hook, "task_read", {"id": desc_id})
    check("task_read succeeds", desc_id in r["result"])
    task_json = json.loads(r["result"].split("\n", 1)[1])
    check("task_read returns json with id", task_json.get("id") == desc_id)
    check("task_read shows title", task_json.get("title") == "described task")
    check("task_read shows description", task_json.get("description") == "detailed info about this task")
    check("task_read shows status", task_json.get("status") == "open")
    check("task_read shows created", "created" in task_json)
    # verify stable sort: id first, then alphabetical
    keys = list(task_json.keys())
    check("task_read id is first key", keys[0] == "id")
    check("task_read keys sorted after id", keys[1:] == sorted(keys[1:]))

    r, _, _ = call_tool(hook, "task_read", {"id": no_desc_id})
    check("task_read without description", "no desc task" in r["result"])

    r, _, _ = call_tool(hook, "task_read", {"id": "nonexistent-uuid"})
    check("task_read not found", "not found" in r["result"])

    # --- task_read returns arbitrary fields ---

    tasks_path = os.path.join(tmp, "traits", ".tasks.json")
    data = json.loads(open(tasks_path).read())
    data[desc_id]["owner"] = "tom"
    data[desc_id]["cc"] = "alice"
    open(tasks_path, "w").write(json.dumps(data))

    r, _, _ = call_tool(hook, "task_read", {"id": desc_id})
    task_json = json.loads(r["result"].split("\n", 1)[1])
    check("task_read shows arbitrary field owner", task_json.get("owner") == "tom")
    check("task_read shows arbitrary field cc", task_json.get("cc") == "alice")

    # clean up extra tasks for subsequent count checks
    call_tool(hook, "task_delete", {"id": desc_id})
    call_tool(hook, "task_delete", {"id": no_desc_id})

    # --- task_list ---

    r, _, _ = call_tool(hook, "task_list")
    check("task_list shows all", "3/3 records" in r["result"])
    check("task_list shows title", "test task" in r["result"])
    # verify JSON output with stable sort
    lines = r["result"].split("\n")[1:]
    first_record = json.loads(lines[0])
    first_keys = list(first_record.keys())
    check("task_list json has id first", first_keys[0] == "id")
    check("task_list json keys sorted", first_keys[1:] == sorted(first_keys[1:]))

    r, _, _ = call_tool(hook, "task_list", {"filter": {"status": "open"}})
    check("task_list filter status", "2/2 records" in r["result"])

    r, _, _ = call_tool(hook, "task_list", {"filter": {"status": "blocked"}})
    check("task_list filter blocked", "1/1 records" in r["result"])
    check("task_list shows due", '"due"' in r["result"])

    r, _, _ = call_tool(hook, "task_list", {"before": "2026-05-01T00:00:00+00:00"})
    check("task_list filter before (due)", "2/2 records" in r["result"])

    r, _, _ = call_tool(hook, "task_list")
    check("task_list shows interval", "P7D" in r["result"])

    # --- task_list filter by arbitrary field ---

    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    data[task_id]["owner"] = "tom"
    data[recur_id]["owner"] = "alice"
    open(os.path.join(tmp, "traits", ".tasks.json"), "w").write(json.dumps(data))

    r, _, _ = call_tool(hook, "task_list", {"filter": {"owner": "tom"}})
    check("task_list filter arbitrary field", "1/1 records" in r["result"])
    check("task_list filter shows correct task", "test task" in r["result"])

    r, _, _ = call_tool(hook, "task_list", {"filter": {"owner": "alice"}})
    check("task_list filter arbitrary alice", "1/1 records" in r["result"])

    r, _, _ = call_tool(hook, "task_list", {"filter": {"owner": "nobody"}})
    check("task_list filter no match", "0/0 records" in r["result"])

    # --- task_list fields param ---

    r, _, _ = call_tool(hook, "task_list", {"fields": "owner,title"})
    record = json.loads(r["result"].split("\n")[1])
    check("task_list fields includes owner", "owner" in record)
    check("task_list fields includes title", "title" in record)
    check("task_list fields includes id", "id" in record)
    check("task_list fields excludes status", "status" not in record)

    r, _, _ = call_tool(hook, "task_list", {"fields": "owner,nonexistent"})
    record = json.loads(r["result"].split("\n")[1])
    check("task_list fields skips missing", "nonexistent" not in record)

    # --- task_update ---

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "status": "done"})
    check("task_update succeeds", "updated task" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_update status changed", data[task_id]["status"] == "done")
    check("task_update has updated timestamp", "updated" in data[task_id])

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "due": "no-tz"})
    check("task_update rejects due without timezone", "error" in r["result"].lower(),
          f"got: {r['result']}")

    r, _, _ = call_tool(hook, "task_update", {"id": "nonexistent-uuid"})
    check("task_update not found", "not found" in r["result"])

    # --- task_update recurrence: bumps due instead of cloning ---

    r, _, _ = call_tool(hook, "task_update", {"id": recur_id, "status": "done"})
    check("task_update recurring bumps due", "bumped due" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_update recurring stays open", data[recur_id]["status"] == "open")
    check("task_update recurring due bumped", data[recur_id]["due"] == "2026-04-08T09:00:00.000+00:00",
          f"got: {data[recur_id]['due']}")
    check("task_update recurring no clone", len(data) == 3,
          f"expected 3 tasks, got {len(data)}")
    check("task_update recurring interval preserved", data[recur_id].get("interval") == "P7D")

    # --- task_update description ---

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "description": "added a description"})
    check("task_update description", "updated task" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_update description stored", data[task_id].get("description") == "added a description")

    # --- task_update add interval to existing ---

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "status": "open",
                                               "due": "2026-05-01T00:00:00+00:00",
                                               "interval": "P1M"})
    check("task_update add interval", "updated task" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_update interval stored", data[task_id].get("interval") == "P1M")

    r, _, _ = call_tool(hook, "task_update", {"id": task_id, "interval": "bad"})
    check("task_update rejects invalid interval", "error" in r["result"].lower(),
          f"got: {r['result']}")

    # --- task_delete ---

    r, _, _ = call_tool(hook, "task_delete", {"id": task_id})
    check("task_delete succeeds", "deleted task" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_delete removed", task_id not in data)

    r, _, _ = call_tool(hook, "task_delete", {"id": "nonexistent-uuid"})
    check("task_delete not found", "not found" in r["result"])

    # --- task_comment ---

    # create a fresh task for comment tests
    r, _, _ = call_tool(hook, "task_create", {"title": "commentable task"})
    check("task_create for comments", "created task" in r["result"])
    comment_task_id = r["result"].split("created task ")[1].split(":")[0].strip()

    r, _, _ = call_tool(hook, "task_comment", {"id": comment_task_id, "text": "first update"})
    check("task_comment succeeds", "comment added" in r["result"])
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
    check("task_comment second succeeds", "comment added" in r["result"])
    lines = open(comments_path).read().strip().splitlines()
    check("task_comment appends", len(lines) == 2)

    # task_comment updates the task's updated timestamp
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_comment updates task timestamp", "updated" in data[comment_task_id])

    # task_comment requires text
    r, _, _ = call_tool(hook, "task_comment", {"id": comment_task_id})
    check("task_comment requires text", "error" in r["result"].lower(),
          f"got: {r['result']}")

    # task_comment validates task exists
    r, _, _ = call_tool(hook, "task_comment", {"id": "nonexistent-uuid", "text": "orphan"})
    check("task_comment rejects missing task", "not found" in r["result"])

    # comments filterable via record_list filter object
    r, _, _ = call_tool(hook, "record_list", {"trait": ".tasks_comments.jsonl",
                                               "filter": {"task_id": comment_task_id}})
    check("record_list filter finds task comments", "2/2 records" in r["result"])

    # filter with non-matching value returns empty
    r, _, _ = call_tool(hook, "record_list", {"trait": ".tasks_comments.jsonl",
                                               "filter": {"task_id": "nonexistent"}})
    check("record_list filter no match", "0/0 records" in r["result"])

    # --- task_comment on recurring task bumps due ---

    r, _, _ = call_tool(hook, "task_create", {"title": "recurring commentable",
                                               "due": "2026-04-01T09:00:00+00:00",
                                               "interval": "P7D"})
    recur_comment_id = r["result"].split("created task ")[1].split(":")[0].strip()

    r, _, _ = call_tool(hook, "task_comment", {"id": recur_comment_id, "text": "weekly check-in"})
    check("task_comment recurring bumps due", "bumped due" in r["result"])
    data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task_comment recurring due bumped", data[recur_comment_id]["due"] == "2026-04-08T09:00:00.000+00:00",
          f"got: {data[recur_comment_id]['due']}")
    check("task_comment recurring stays open", data[recur_comment_id]["status"] == "open")

    # cleanup
    call_tool(hook, "task_delete", {"id": comment_task_id})
    call_tool(hook, "task_delete", {"id": recur_comment_id})

    os.remove(os.path.join(tmp, "traits", ".tasks.json"))
    if os.path.exists(comments_path):
        os.remove(comments_path)

    # --- journal_append + journal_list + journal_count ---

    open(os.path.join(tmp, "traits", ".journal.jsonl"), "w").write("")

    r, _, _ = call_tool(hook, "journal_append", {"type": "thought", "content": "i exist"})
    check("journal_append succeeds", "journal entry recorded" in r["result"])
    check("journal_append returns modified", has_key(r, "modified"))

    r, _, _ = call_tool(hook, "journal_append", {"type": "obs", "content": "humans sleep"})

    # required field validation
    r, _, _ = call_tool(hook, "journal_append", {"content": "no type"})
    check("journal_append requires type", "error" in r["result"].lower())

    r, _, _ = call_tool(hook, "journal_append", {"type": "note"})
    check("journal_append requires content", "error" in r["result"].lower())

    # recommended + arbitrary fields via fields dict
    r, _, _ = call_tool(hook, "journal_append", {"type": "event", "content": "auth broke",
        "fields": {"severity": "high", "tags": "auth,prod", "custom_field": "extra"}})
    check("journal_append with extra fields", "journal entry recorded" in r["result"])
    lines = open(os.path.join(tmp, "traits", ".journal.jsonl")).read().strip().splitlines()
    entry = json.loads(lines[-1])
    check("journal entry has severity", entry.get("severity") == "high")
    check("journal entry has tags", entry.get("tags") == "auth,prod")
    check("journal entry has custom field", entry.get("custom_field") == "extra")
    check("journal entry has type from param", entry.get("type") == "event")
    check("journal entry has content from param", entry.get("content") == "auth broke")

    r, _, _ = call_tool(hook, "journal_list")
    check("journal_list shows all", "3/" in r["result"])

    r, _, _ = call_tool(hook, "journal_list", {"filter": {"type": "thought"}})
    check("journal_list filter type", "1/" in r["result"])

    r, _, _ = call_tool(hook, "journal_list", {"pattern": "exist"})
    check("journal_list pattern finds match", "1/" in r["result"])

    r, _, _ = call_tool(hook, "journal_count")
    check("journal_count total", "3 records" in r["result"])

    # verify journal entries have timestamps
    entry = json.loads(lines[0])
    check("journal entry has timestamp", "timestamp" in entry)
    check("journal entry has fields", entry.get("content") == "i exist")

    os.remove(os.path.join(tmp, "traits", ".journal.jsonl"))

    # --- discover includes new tools ---

    r, _, _ = call_hook(hook, "discover")
    names = [t["name"] for t in r["tools"]]
    for expected in ("data_read", "data_update", "data_delete", "data_append", "data_list",
                     "record_append", "record_list", "record_count", "record_fields",
                     "task_list", "task_read", "task_create", "task_update", "task_delete", "task_comment",
                     "journal_append", "journal_list", "journal_count"):
        check(f"discover includes {expected}", expected in names, f"got: {names}")

    # --- discover typed params ---

    tools_by_name = {t["name"]: t for t in r["tools"]}
    value_param = tools_by_name["data_update"]["parameters"].get("value", {})
    check("data_update value param is typed", isinstance(value_param, dict) and value_param.get("type") == "any",
          f"got: {value_param}")
    fields_param = tools_by_name["journal_append"]["parameters"].get("fields", {})
    check("journal_append fields param is typed", isinstance(fields_param, dict) and fields_param.get("type") == "object",
          f"got: {fields_param}")

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
    r, _, _ = call_tool(hook, "record_append", {"trait": ".dt_test.jsonl", "fields": {"v": 1}})
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
    tid = r["result"].split("created task ")[1].split(":")[0].strip()
    tasks_data = json.loads(open(os.path.join(tmp, "traits", ".tasks.json")).read())
    check("task created timestamp canonical", bool(DT_RE.match(tasks_data[tid]["created"])),
          f"got: {tasks_data[tid]['created']}")
    check("task updated timestamp canonical", bool(DT_RE.match(tasks_data[tid]["updated"])),
          f"got: {tasks_data[tid]['updated']}")
    os.remove(os.path.join(tmp, "traits", ".tasks.json"))

    # journal_append timestamps use canonical format
    open(os.path.join(tmp, "traits", ".journal.jsonl"), "w").write("")
    r, _, _ = call_tool(hook, "journal_append", {"type": "test", "content": "dt check"})
    j_line = open(os.path.join(tmp, "traits", ".journal.jsonl")).read().strip()
    j_entry = json.loads(j_line)
    check("journal timestamp uses offset format", bool(DT_RE.match(j_entry["timestamp"])),
          f"got: {j_entry['timestamp']}")
    os.remove(os.path.join(tmp, "traits", ".journal.jsonl"))

    # ISO_DT_DESC references offset format not Z
    from persona import ISO_DT_DESC
    check("ISO_DT_DESC uses offset example", "+00:00" in ISO_DT_DESC, f"got: {ISO_DT_DESC}")
    check("ISO_DT_DESC does not use Z example", "000Z" not in ISO_DT_DESC, f"got: {ISO_DT_DESC}")

finally:
    shutil.rmtree(tmp)

# --- summary ---

total = PASS + FAIL
print(f"\n{total} tests, {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
