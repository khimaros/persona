# ROADMAP

```
[ ] fix breakage and simplify incant-user.sh plugin installation
[ ] create tools for editing agents/per.md frontmatter
    [ ] permission_add("bash", "cat /etc/*", "ask")
    [ ] permission_remove("bash", "cat /etc/*", "ask")
    [ ] permission_set("webfetch", "deny")
    [~] tools_enable("question")
    [~] tools_disable("patch")
    [~] browser_url_add("https://github.com/khimaros/*", "allow")
    [~] browser_url_remove("https://github.com/khimaros/*", "allow")
    [~] patch("~/.config/opencode/plugins/persona.ts", oldString, newString)
    [~] patch("~/.config/opencode/tools/persona.ts", oldString, newString)
[ ] fix tool descriptions to prevent trait_delete on .json traits and trait_append on new traits
[ ] speech-to-text and text-to-speech for chats
[~] enable default ask and whitelist allow for browser-use
[~] immutable system prompt + one-round-delay notification messages

[x] trait tools: directory cleanup on delete/move, auto-create dirs on write/move
[x] task comments: add comments field, task_comment tool
[x] switch browser-use open --new-tab to separate new-tab and open calls (code and skill)
[x] allow pushing dev branch of browser-use manually into guest
[x] install forked opencode-ai
[x] record_fields tool: introspect unique fields/values in .jsonl traits
[x] add description field to task data structure
[x] extract opencode-evolve plugin package
[x] move tests into workspace/
[x] heartbeat sessions should occasionally be reset (size based? every turn?)
[x] hook-based plugin architecture (Python hooks, TS shim, IPC via subprocess)
[x] dynamic tool discovery and invocation (discover_tools + invoke_tool)
[x] builtin hook/prompt editing tools with test validation (write/patch_hook, write/patch_prompt)
[x] compaction hook to preserve persona context during session compaction
[x] persist discovered per model to persona-state.json
[x] notify sessions when traits change
[x] add HEARTBEAT mechanism for plugin
[x] send heartbeat messages to LLM in dedicated session
[x] send proactive follow-ups to active per sessions
[x] install forked browser-use
[x] MongoDB-style filters, structured output, {trait:} format, no avatar prefix
[x] add trait edit diffs to debug log
[x] version traits with git and commit after each change
[x] always ask permission for changes to core.md trait
```
