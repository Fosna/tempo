# Idea: record a task's outputs after it exists

Parked on 2026-10-08.

Trigger: finished an incident summary task and wanted to note what the session produced
(summary draft, two chat messages, a parked idea, findings). tempo cannot do it. `notes` is
only settable at creation (`tempo add -n`), and the only free-text command afterwards is
`friction`, which is for things that slowed work down, not for results.

Idea: let a task carry notes that can be added at any time, including after it is done, so a
task record shows what came out of it and not just how long it took.

- `tempo note [ID] "text"` - append a line to the task's notes. Defaults to the active task,
  like `friction`. Allowed on done tasks, like `friction --task ID`.
- Notes stay free text. Each appended line is timestamped by the CLI, never by the caller.
- `tempo list` stays unchanged. Notes show up in `report`.

Related: `parked-ideas/backfill-past-sessions.md` (same pattern: things that happen before or
after the task is named, and tempo has no way to record them).

## Open questions

- Append-only, or should an existing note be editable or removable?
- Should reports include notes, and if so verbatim or only a count?
- Does it need a separate "outputs" field (links, files), or are plain notes enough?
- Should `tempo done` prompt for outputs, as it already does for friction?

Not designed. Not acted on.
