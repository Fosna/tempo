# Idea: start tracking before the task is named, and backfill what was missed

Parked on 2026-10-08.

Trigger: started tracking an incident summary task late. Had already worked 17:04-17:12 and
resumed at 19:14, but `tempo start` has no `--at` and no command records a closed past session.
The only way in was a one-time script that called `tasks.add_task()` and appended a closed
session inside `store.transaction()`. That bypassed the CLI, which is meant to be the only
writer of `~/.tempo/tasks.json`.

Second trigger, same day: the `/tempo` skill skipped `add` and `start` when invoked with a bare
chat link plus `/clarify-before-execution`, so the clock never started. Cost about 20 min of
repair (backfill hack, root-causing the miss) in a 45m task. Naming a task before the analysis
is often impossible, which makes it tempting to delay tracking.

Idea: tracking should start before the task is named, and the CLI should record time that
already happened, so the hack is never needed again.

- `tempo log ID START END` - add a closed past session to an existing task. Same validation as
  `stop --at`: no future times, end after start, no overlap with the task's other sessions or
  breaks. Stored with `endState: confirmed`.
- `tempo start --at HH:MM` - backdate the start of the new open session. Same checks, and it
  must not overlap an earlier session.
- Creating a task stays a separate step (`tempo add`). A task with a past session is two
  commands: `tempo add`, then `tempo log`. `tempo add` does not take a session range.

- `tempo rename ID NEW NAME` - change a task's name. Lets a task start under a placeholder
  (link, or "request from <sender>") and get its real name after the analysis.
- `/tempo` skill rule (in `~/.claude/skills/tempo/SKILL.md`, not in this repo): for a new task,
  run `tempo add` + `tempo start` first, before any other skill or analysis. Placeholder name,
  default estimate 30m unless given. Tracking never waits on clarifying or naming.

Option (added 2026-10-09): Claude Code transcripts as evidence for times. Every chat is saved
in `~/.claude/projects/*/*.jsonl` with a timestamp per message. Claude can read them to propose
a backfill start or a reconcile stop time ("your last message there was at 16:42?"), and the
user confirms before `tempo log` or `stop --at` runs. Evidence only, never a writer.

- Fits work done in chat. Work in other tools leaves no trace, so transcripts only bound a
  session, they do not measure it.
- One chat can touch several tasks, and one task can span several chats. Matching is a guess.

## Decisions already made (2026-10-08)

- Backfilled sessions are not flagged in reports. They look like any other confirmed session.
- `log` must work on done tasks too, so past records can be corrected.
- The CLI stays the only writer of state. The store-layer script was a one-off, not a pattern.

## Open questions

- Does `start --at` on a task that is already active, or while another task is active, behave
  like `--prev-end` (auto-stop the other task at the backdated time)?
- Should `log` on a done task re-check the overrun reason, since actual time changes?
- Should `log` accept ISO timestamps for sessions on other days, like `reconcile --end`?

Not designed. Not acted on.
