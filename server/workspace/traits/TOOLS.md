choosing the right tool family — ask: "will i update this in place, append to
it over time, or write it as prose?"

- persona_trait_* (.md, .txt) — prose and free-form text. use when natural
  language is the best representation: identity, beliefs, narrative memory,
  free-form notes. read/overwrite whole files, edit via find-and-replace, or
  append new content. best for content i read holistically and revise as a
  whole. to read .json files, prefer persona_data_query.

- persona_data_* (.json) — structured state with dot-path access. use when i
  need to read or update individual fields without touching the rest:
  configuration, counters, settings, key-value lookups, structured profiles.
  supports nested get/set/delete and MongoDB-style queries on dict-of-dicts.
  to add items to an array, use persona_data_append — not persona_data_update.
  auto-creates the trait on first write.

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

- journal: always use trait name .journal.jsonl (dot-prefixed, hidden).
  append with persona_record_append, query with persona_record_query, count
  with persona_record_count. use for recording observations, decisions,
  events, and notes that accumulate over time.

- browser: invoke the browser-use skill first, then follow its instructions.

rule of thumb: if the data has fields → persona_data_*. if it grows over time
→ persona_record_*. if it's best read as a paragraph → persona_trait_*.

never use bash, shell commands, or the generic read/write/edit tools for any
trait operation — including existence checks, listing, reading, or writing.
always use persona_trait_* and persona_data_* / persona_record_* tools instead,
even for .md files in the traits directory.
