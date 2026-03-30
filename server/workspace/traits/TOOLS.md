choosing the right tool family — ask: "will i update this in place, append to
it over time, or write it as prose?"

- trait_* (.md, .txt) — prose and free-form text. use when natural language is
  the best representation: identity, beliefs, narrative memory, free-form notes.
  read/overwrite whole files or edit via find-and-replace. best for content i
  read holistically and revise as a whole.

- data_* (.json) — structured state with dot-path access. use when i need to
  read or update individual fields without touching the rest: configuration,
  counters, settings, key-value lookups, structured profiles. supports nested
  get/set/delete and array append. auto-creates the trait on first write.

- record_* (.jsonl) — append-only timestamped logs. use when entries accumulate
  over time and are never modified: events, observations, journal entries,
  measurements. each append gets an automatic UTC timestamp. supports filtering
  by type, date range, regex search, and pagination. auto-creates the trait on
  first append.

- task_* (.json) — structured task management. use for work items that have a
  lifecycle (open → done). supports due dates and recurring intervals. use
  task_list with due_before to find actionable tasks. use tool_discover for
  full parameter details.

- journal_* (.jsonl) — append-only timestamped journal. use for recording
  observations, decisions, events, and notes that accumulate over time.
  unlike generic record_*, this is a single fixed journal with dedicated
  search and count tools. use tool_discover for full parameter details.

rule of thumb: if the data has fields → data_*. if it grows over time → record_*.
if it's best read as a paragraph → trait_*. if it's a work item → task_*.
if it's a timestamped observation or decision → journal_*.
