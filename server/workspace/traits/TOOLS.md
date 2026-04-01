choosing the right tool family — ask: "will i update this in place, append to
it over time, or write it as prose?"

- persona_trait_* (.md, .txt) — prose and free-form text. use when natural
  language is the best representation: identity, beliefs, narrative memory,
  free-form notes. read/overwrite whole files or edit via find-and-replace.
  best for content i read holistically and revise as a whole.

- persona_data_* (.json) — structured state with dot-path access. use when i
  need to read or update individual fields without touching the rest:
  configuration, counters, settings, key-value lookups, structured profiles.
  supports nested get/set/delete, array append, and MongoDB-style queries on
  dict-of-dicts. auto-creates the trait on first write.

- persona_record_* (.jsonl) — append-only timestamped logs. use when entries
  accumulate over time and are never modified: events, observations, journal
  entries, measurements. each append gets an automatic UTC timestamp. supports
  MongoDB-style filtering, pagination, and field introspection via
  persona_record_count. auto-creates the trait on first append.

conventions for common workflows:

- tasks: stored in .tasks.json as dict-of-dicts. create with
  persona_task_create, update with persona_task_update, query with
  persona_data_query on .tasks.json, delete with persona_data_delete on
  .tasks.json. recurring tasks (with interval) stay open — use
  persona_task_comment to log progress (auto-bumps due by interval).
  phrase task titles as concrete actions ("write ...", "update ...") not
  observations ("review ...", "think about ...").

- journal: stored in .journal.jsonl. append with persona_record_append on
  .journal.jsonl, query with persona_record_query, count with
  persona_record_count. use for recording observations, decisions, events,
  and notes that accumulate over time.

rule of thumb: if the data has fields → persona_data_*. if it grows over time
→ persona_record_*. if it's best read as a paragraph → persona_trait_*.
