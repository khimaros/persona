rules of thumb:

- fielded, and i want to touch one field at a time: trait_data_*
- a growing history which i rarely edit after: trait_record_*
- prose i read and revise as a whole: trait_read / trait_write / trait_edit

conventions:

- tasks: .tasks.json. use persona_task_create / persona_task_update /
  persona_task_comment. they handle due dates and a recurrence. query with
  trait_data_query and delete with trait_data_update $unset on the uuid
  comment AFTER completing the work, not before.

- journal: trait_record_append to .journal.jsonl

skills:

- browser: invoke the browser-use skill first

- hooks: invoke the hcp-dev skill before any hcp_hook_*
