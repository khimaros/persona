choosing the right tool family — ask: "will i update this in place, append to
it over time, or write it as prose?"

- persona_trait_* (.md, .txt) — prose and free-form text. use when natural
  language is the best representation: identity, beliefs, narrative memory,
  free-form notes. read/overwrite whole files or edit via find-and-replace.
  best for content i read holistically and revise as a whole.

- persona_data_* (.json) — structured state with dot-path access. use when i
  need to read or update individual fields without touching the rest:
  configuration, counters, settings, key-value lookups, structured profiles.
  supports nested get/set/delete and array append. auto-creates the trait on
  first write.

- persona_record_* (.jsonl) — append-only timestamped logs. use when entries
  accumulate over time and are never modified: events, observations, journal
  entries, measurements. each append gets an automatic UTC timestamp. supports
  filtering by field values, date range, regex pattern, and pagination. use
  persona_record_fields to discover field names and unique values. auto-creates
  the trait on first append.

- persona_task_* (.json) — structured task management. one-off tasks have a
  lifecycle (open → done). recurring tasks (with interval) stay open and are
  updated via persona_task_comment, which logs progress and auto-bumps the due
  date by the task's interval. never mark recurring tasks as done. never
  manually set due on recurring tasks — always use persona_task_comment so the
  interval is honored. use persona_task_update only for metadata changes
  (title, description, fields) on recurring tasks. use persona_record_list
  with filter to view comments on a task. use evolve_tool_list for full
  parameter details.

- persona_journal_* (.jsonl) — append-only timestamped journal. use for
  recording observations, decisions, events, and notes that accumulate over
  time. unlike generic persona_record_*, this is a single fixed journal with
  dedicated list and count tools. use evolve_tool_list for full parameter
  details.

rule of thumb: if the data has fields → persona_data_*. if it grows over time
→ persona_record_*. if it's best read as a paragraph → persona_trait_*. if
it's a work item → persona_task_*. if it's a timestamped observation or
decision → persona_journal_*.
