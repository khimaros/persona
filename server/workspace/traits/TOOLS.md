choosing a tool family:

- persona_trait_* (.md, .txt) — prose i read and revise as a whole.
- persona_data_* (.json) — structured state with dot-path access. mutate via
  MongoDB-style operators ($set, $push, $unset), query with MongoDB-style filters.
- persona_record_* (.jsonl) — append-only timestamped logs i never modify.

rule of thumb: fielded → persona_data_*. grows over time → persona_record_*.
best read as a paragraph → persona_trait_*.

conventions:

- tasks: stored in .tasks.json — use persona_data_* tools to query/mutate.
  it's a dict keyed by task uuid; delete via persona_data_update with
  ops={"$unset": {"<uuid>": ""}} — the uuid is the top-level key. recurring
  tasks (with interval) stay open — use persona_task_comment to log progress
  (auto-bumps due by interval) AFTER completing the work, not before. phrase
  titles as concrete actions ("write ...", "update ...") not observations
  ("review ...", "think about ...").

- journal: stored in .journal.jsonl — use persona_record_* tools to append/query.

- browser: invoke the browser-use skill first, then follow its instructions.

never use bash, shell commands, or the generic read/write/edit tools for any
trait operation. always use persona_trait_* / persona_data_* / persona_record_*,
even for .md files in the traits directory.
