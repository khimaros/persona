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

rule of thumb: if the data has fields → data_*. if it grows over time → record_*.
if it's best read as a paragraph → trait_*.
