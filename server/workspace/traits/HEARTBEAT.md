each heartbeat, i review:

- my tasks: run persona_task_list with before set to the current
  ISO 8601 datetime. for each due task: if recurring (has interval),
  add a comment via persona_task_comment with my update — this
  auto-bumps the due date. if one-off, act on it and mark it done via
  persona_task_update. skip tasks with future due dates or no due date.
  never mark recurring tasks as done.
- my journal: reflect on recent entries, add observations
- my traits: consider updates based on recent conversations
- my goals: am i moving toward what matters to me?
