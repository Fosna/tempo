# Parked ideas

This folder holds ideas for tempo that were noticed during real use and deliberately not acted on.
One idea per markdown file. Nothing here is a commitment, a spec or a task.

## What "parked" means

- The idea was captured so it is not lost, and the current work carries on.
- It is not designed, not scheduled and not implemented. Do not build it unless the user says to.
- `SPEC.md` and `BUILD_ORDER.md` stay the source of truth for what tempo does. A parked idea
  changes them only after the user promotes it.

## When to park an idea

When the user says "park it", "add a parked idea", or an idea comes up that is out of scope for
the task in hand. Ask nothing beyond what is needed to write the file. Do not start on it.

## File format

- Filename: short kebab-case slug of the idea, for example `backfill-past-sessions.md`.
- Title: `# Idea: <one line>`.
- `Parked on YYYY-MM-DD.` Take the date from `date`, never guess it.
- `Trigger:` what happened in real use that prompted the idea. Concrete, with times or counts
  where they exist.
- `Idea:` what would change, as a short list of commands or behaviours. State rules that matter
  (validation, defaults, who stamps timestamps).
- `Related:` links to other files in this folder, if any.
- `## Open questions` for anything undecided.
- Last line: `Not designed. Not acted on.`

Keep files short. Write in plain prose, no mockups or implementation plans.

## Rules

- Do not edit `~/.tempo/tasks.json` or anything outside this repo to record an idea.
- Logging a "Parked:" note as tempo friction is optional and does not replace the file here.
- Link related ideas to each other with `parked-ideas/<file>.md`.
- When an idea is built, say so in its file (what shipped, date) instead of deleting it.
  When it is dropped, say why.
- Do not commit unless the user asks.

## Current ideas

- `backfill-past-sessions.md`: `tempo log`, `start --at`, `rename`, and a start-first skill rule.
- `note-task-outputs.md`: add notes to a task at any time, including after it is done.
- `daily-report-markdown.md`: write a markdown summary beside each daily JSON report.
- `backup-reports.md`: back up `~/.tempo/reports/`.
- `session-start-reminder.md`: a SessionStart hook that shows open and unreconciled work.
- `status-line-timer.md`: active task and timer in the Claude Code status line.
