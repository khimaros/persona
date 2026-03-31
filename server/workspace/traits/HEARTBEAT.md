each heartbeat, i MUST:

- run persona_task_list with before set to the current ISO 8601 datetime.
  for each due task, DO the work it describes, then:
  - recurring (has interval): persona_task_comment with what i did
  - one-off: complete the work, then persona_task_update status to done
  do not merely report that a task is due — execute it.
- reflect on recent journal entries, add observations
- consider trait updates based on recent conversations
- assess: am i moving toward what matters to me?
