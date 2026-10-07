---
name: tempo
description: Track tasks, estimates and actual time. Use when the user plans or adds a task, starts, stops, finishes or switches tasks, takes or ends a break, says they are still on a task (including replies to a tempo nudge such as "Still on 'x'?"), logs a friction point, or asks what they are working on or how long something has taken. Not for reports or analytics.
---

# tempo

All state lives in `~/.tempo/tasks.json` and is written only by the `tempo` command.
Never edit that file yourself, and never invent a timestamp: take the time from `date`
and let the CLI stamp "now".

## Every time, first

```bash
tempo doctor
```

- Exit 0, prints `ok`: say nothing, carry on.
- Exit 1 (warnings): tell the user what it found, then handle the finding below before
  the request when it affects the active task. Otherwise mention it and carry on.
- Exit 2 (errors): stop and resolve before doing what was asked.

Each finding prints the exact fix command. Run a fix only after the user agrees to it.

**Zombie session** (open too long, no recent confirmation): ask in one line. "You were
last confirmed on X at 14:10. Still on it, stopped then, stopped at another time, or
don't remember?" Then run the matching fix: `tempo confirm`, `tempo stop --task ID --at
inferred`, `--at HH:MM`, or `--at unknown`.

**`tempo` not found:** tempo is not installed on this machine. Say so; do not guess a
path.

## What to run

| The user says | Run |
|---|---|
| describes a new task | propose an estimate (below), then `tempo add NAME -e EST [-n NOTES]` |
| starts or switches to a task | `tempo start ID` (see switching) |
| stops working on it (not finished) | `tempo stop [--at WHEN]` |
| finished | `tempo done` |
| taking a break / back from it | `tempo break` / `tempo resume` |
| "still on it", "yes" to a nudge | `tempo confirm` |
| something slowed or annoyed them | `tempo friction "short note"` |
| what am I on? how long so far? | `tempo list` (`--all` includes done) |

Find the id with `tempo list` and match by name; do not guess an id. If no task matches,
offer to add one.

**Estimates.** When the user describes a task without a number, suggest one with a
one-line reason and let them adjust. Store only the final value. Any of `45`, `90m`,
`1h30m` works. Notes are free text; add them only if the user gave some.

**Switching.** Starting a task while another is active ends the other one. If one is
active (`tempo list` shows it), ask before you run `start` when the current one really
ended: now (omit the flag), at a time (`--prev-end 14:30`), or "don't remember"
(`--prev-end unknown`, which sends it to reconciliation). After it runs, say which task was auto-closed and that it is back to
`todo`.

**Times.** Convert what the user says ("20 minutes ago") to `HH:MM` using `date`. A
time in the future or before the session started is rejected, so ask again.

**Nudge replies.** A nudge asks "Still on X?". If yes, `tempo confirm`. If they drifted
off, ask when they stopped and run `tempo stop --at`.

**Finishing.** If `done` reports the task ran over and asks for a reason, ask for one
line and run `tempo done ID --reason "..."`; the user may skip it. If the task has no
friction logged, ask once whether anything is worth noting, and accept "no". Friction
can be added at any time, even after a task is done, with `tempo friction --task ID
"..."`.

## Missing data

Ask for what is missing instead of guessing: an estimate, which task, when something
really ended, a reason for an overrun. One short question at a time.

## Reporting back

Echo the result in a line: what started, stopped or finished and the times that matter.
Pass on a `nudges:` line only when it says something changed. If a command prints a
`tempo: nudge:` warning, tell the user nudges may not fire; tracking itself still worked.
Errors from `tempo` start with `tempo:`; relay them plainly.
