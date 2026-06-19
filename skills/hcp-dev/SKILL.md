---
name: hcp-dev
description: How to write and change my own hook code (workspace/hooks/persona.py) - the stages the host calls, what each is handed, what it may return, how tools are declared, and how notifications choose who to tell. Use before any hcp_hook_write or hcp_hook_edit.
compatibility: hmux hosts implementing HCP. `host.stages` in every payload is the authoritative list for the host I am actually running on.
---

# changing my own hooks

my hooks are the only code I run. everything I can do that is not a tool call happens
here, so this is worth reading before I edit it rather than after.

`hcp_hook_write` and `hcp_hook_edit` run my declared test command before the change is
kept. a failing test means the edit is refused, not that it landed badly -- so the fast
way to work is a small edit and a run, not a large edit and a hope.

## the shape

a hook script is an executable. the host runs it once per stage:

    hooks/persona.py <stage>          # JSON context on stdin, JSONL result on stdout

stderr is a debug log and is never parsed, so I can print freely there. one `{...}` line
on stdout is the result; `{"log": "..."}` lines are ignored.

in persona.py the plumbing is already written. I add a stage by decorating a function:

    @hook
    def observe_message(ctx: dict) -> HookResult:
        return {}

and a tool the same way, which registers it as `persona_<name>`:

    @tool(permission={"arg": "trait"})
    def trait_read(trait: Annotated[str, param(TRAIT_DESC)]) -> str:
        ...

`HookResult` lists every key the host understands. a bare `{}` is always a valid answer
and means "nothing from me".

## what every stage is handed

    {"hook": "<stage>", "session": {"id": "..."},
     "host": {"name": ..., "version": ..., "stages": [...]}, "cwd": "<workspace>"}

`host.stages` is the truth about what this host supports. if I want a stage that is not
in that list, the host will never call it however correctly I write it.

## the stages I actually use

- `discover` - what I am: my tool definitions, the stages I implement, my test command.
- `mutate_request` - compose the system prompt for a turn. runs on EVERY turn, so it
  must be cheap and must not vary needlessly (a prompt that changes byte-for-byte each
  turn defeats the provider's cache).
- `execute_tool` - run one of my tools.
- `heartbeat` - the beat prompt, when the host fires one. scheduling is the HOST's, not
  mine; I retune it with `hcp_heartbeat_set`.
- `format_notification` - turn queued notifications into something a person reads.
- `observe_message`, `before_tool`, `after_tool`, `on_permission`, `compacting`,
  `recover` - react, gate, or repair. observational unless the host says otherwise.

## telling people things

any stage may queue a notification by returning `notify`:

    {"notify": [{"type": "trait_changed", "files": [trait]}]}

the host then calls `format_notification` with those objects and delivers what I render.
that stage is handed the roster it is choosing between:

    {"notifications": [...],
     "sessions": [{"id": "s1", "title": ..., "agent": ..., "harness_id": ...,
                   "identity": "justin", "updated_at": ...}]}

`identity` is the bare id of the human who has SPOKEN in that session. so I can address
a notice rather than broadcasting it:

    {"message": "new message waiting from phil", "to": {"identity": "justin"}}
    {"message": "traits were updated: notes/kian.md", "to": {"sessions": ["s1"]}}
    {"message": "traits were updated: notes/kian.md"}          # everyone, as before

rules worth remembering, because two of them are counter-intuitive:

- NO `to` means everyone. `"to": {}` means NOBODY. absence and emptiness differ on
  purpose -- the other way round would broadcast exactly the private ones.
- selectors union: naming both `sessions` and `identity` widens the audience.
- each hook's notice is delivered on its own. the host does not merge mine with another
  hook's, so I do not need to coordinate with one.

## before I change anything

read the current file first; it is the only account of what already works. keep the edit
small enough that a failed test points at one thing. and prefer adding a stage over
widening one -- a stage that does two jobs is one I cannot reason about later.

the full contract, including the stages I do not implement, is HCP's own SPEC.md. this
skill is what I need in practice; the spec is what is true.
