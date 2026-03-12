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

workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    for expected in ("trait_list", "trait_read", "trait_write", "trait_patch", "tool_discover", "tool_invoke"):
        check(f"discover includes {expected}", expected in names, f"got: {names}")
    check("discover returns at least 6 tools", len(r["tools"]) >= 6, f"got: {len(r['tools'])}")
    check("discover logs tool names", any("tools:" in l for l in logs))

    # --- discover tool parameter schemas ---

    tools_by_name = {t["name"]: t for t in r["tools"]}
    expected_counts = {"trait_list": 1, "trait_read": 1, "trait_write": 2, "trait_patch": 3}
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

    # --- trait_write ---

    r, _, _ = call_tool(hook, "trait_write", {"trait": "NEW.md", "content": "hello world"})
    check("trait_write returns result key", has_key(r, "result"))
    check("trait_write result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("trait_write returns success", "successfully wrote" in r["result"])
    check("trait_write reports modified", has_key(r, "modified"))
    check("trait_write modified list correct", r.get("modified") == ["NEW.md"])
    content = open(os.path.join(tmp, "traits", "NEW.md")).read()
    check("trait_write wrote file", content == "hello world")

    # --- trait_patch ---

    open(os.path.join(tmp, "traits", "PATCH.md"), "w").write("old text here")

    r, _, _ = call_tool(hook, "trait_patch", {"trait": "PATCH.md", "old_string": "old text", "new_string": "new text"})
    check("trait_patch returns result key", has_key(r, "result"))
    check("trait_patch result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("trait_patch returns success", "successfully patched" in r["result"])
    check("trait_patch reports modified", has_key(r, "modified"))
    content = open(os.path.join(tmp, "traits", "PATCH.md")).read()
    check("trait_patch updated file", content == "new text here")

    r, _, _ = call_tool(hook, "trait_patch", {"trait": "PATCH.md", "old_string": "nonexistent", "new_string": "x"})
    check("trait_patch not found", "not found" in r["result"])

    open(os.path.join(tmp, "traits", "DUP.md"), "w").write("aaa")
    r, _, _ = call_tool(hook, "trait_patch", {"trait": "DUP.md", "old_string": "a", "new_string": "b"})
    check("trait_patch multiple matches fails", "3 matches" in r["result"])

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

    # --- tool_discover ---

    r, logs, _ = call_tool(hook, "tool_discover")
    check("tool_discover returns result key", has_key(r, "result"))
    check("tool_discover result is str", isinstance(r.get("result"), str), f"got: {type(r.get('result')).__name__}")
    check("tool_discover includes header", "available tools:" in r["result"])
    for name in ("trait_list", "trait_read", "tool_invoke"):
        check(f"tool_discover lists {name}", name in r["result"], f"got: {r['result']}")
    check("tool_discover logs tool name", any("tool=tool_discover" in l for l in logs))

    # --- tool_invoke ---

    open(os.path.join(tmp, "traits", "INV.md"), "w").write("invoked content")

    r, logs, _ = call_tool(hook, "tool_invoke", {"name": "trait_read", "args": json.dumps({"trait": "INV.md"})})
    check("tool_invoke returns result key", has_key(r, "result"))
    check("tool_invoke dispatches correctly", "invoked content" in r["result"])
    check("tool_invoke logs tool name", any("tool=tool_invoke" in l for l in logs))

    r, _, _ = call_tool(hook, "tool_invoke", {"name": "nonexistent"})
    check("tool_invoke unknown tool", "unknown tool" in r["result"])

    r, _, _ = call_tool(hook, "tool_invoke", {"name": "trait_read", "args": "not json{"})
    check("tool_invoke bad json", "invalid args JSON" in r["result"])

    os.remove(os.path.join(tmp, "traits", "INV.md"))

    # --- tool handlers return notify ---

    open(os.path.join(tmp, "traits", "NOTIFY.md"), "w").write("x")

    r, _, _ = call_tool(hook, "trait_write", {"trait": "NOTIFY.md", "content": "updated"})
    check("trait_write returns notify", has_key(r, "notify"))
    check("trait_write notify is list", isinstance(r.get("notify"), list))
    check("trait_write notify has trait_changed", any(n.get("type") == "trait_changed" for n in r.get("notify", [])))
    check("trait_write notify includes file", any("NOTIFY.md" in n.get("files", []) for n in r.get("notify", [])))

    r, _, _ = call_tool(hook, "trait_patch", {"trait": "NOTIFY.md", "old_string": "updated", "new_string": "patched"})
    check("trait_patch returns notify", has_key(r, "notify"))
    check("trait_patch notify has trait_changed", any(n.get("type") == "trait_changed" for n in r.get("notify", [])))

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
    check("format_notification message has trait-update", "trait-update" in r.get("message", ""))
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

    r, _, _ = call_tool(hook, "tool_discover")
    check("tool_discover result has avatar prefix", r.get("result", "").startswith("🌀 "))

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

finally:
    shutil.rmtree(tmp)

# --- summary ---

total = PASS + FAIL
print(f"\n{total} tests, {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
