# tempo

Track tasks, estimates and actual time from a Claude Code chat, then find out where your
estimates go wrong.

```console
you:    I need to write the onboarding doc. Start tracking.
claude: Estimate 1h30m? It has an outline, a draft and a review pass.
you:    make it 2h
claude: (runs tempo add / tempo start) Started 'write onboarding doc', est 2h.
        Nudges are set: I'll ask if you're still on it every 30 minutes.
```

The chat is the interface. A small CLI owns the data, a skill teaches Claude to drive it,
and macOS notifications bring you back to the chat when a session may have been
forgotten. Reports are plain JSON on disk.

Status: an MVP, used by its author. The analytics skill (the part that reads reports and
writes up wins, misses and learnings) is not built yet. See
[SPEC.md](SPEC.md) for the design and [BUILD_ORDER.md](BUILD_ORDER.md) for what is done.

## What it does

- **Tasks with estimates.** Estimates are free-form (`45`, `90m`, `1h30m`). Claude can
  propose one; the final number is what is stored.
- **Sessions and breaks.** Start, stop and take breaks. Actual time is computed from the
  sessions, never typed in. One task is active at a time; starting another stops the
  first.
- **Friction notes.** Free-text, timestamped, added during or after the work (even after
  the task is done).
- **Nudges.** For tasks of 30 minutes or more, a notification asks "Still on it?" at 30-minute marks,
  then every 15 minutes once you pass the estimate. Replying in the chat confirms the
  session.
- **Honest data.** If you forget to stop a task, tempo does not guess. It asks when you
  really stopped; "I don't remember" is a valid answer and puts the session in a
  reconciliation queue. Sessions end as `confirmed`, `inferred` (closed at the last time
  you confirmed) or `unknown`, and reports keep them apart.
- **Reports.** `tempo report daily` writes estimate-vs-actual per task, totals, session
  counts and the list of unresolved sessions as JSON.

## Install

Needs Python 3.9+ and nothing else (standard library only). macOS is assumed for the
notifications.

```bash
git clone https://github.com/Fosna/tempo.git
cd tempo
python3 install.py install
```

This writes a `tempo` command to `~/.local/bin` and links the skill into
`~/.claude/skills/tempo`. Make sure `~/.local/bin` is on your `PATH`, then start a new
Claude Code session so the skill loads.

```bash
python3 install.py status      # is it healthy, and if not, how to repair it
python3 install.py uninstall   # removes the command and the link; your data stays
```

### Nudges (optional)

Reminders come from [nudge](https://github.com/Fosna/nudge), a separate tool that
schedules macOS notifications and keeps them alive after the session ends. Without it
tempo still tracks everything; you just get no reminders, and `tempo doctor` says so.

## Use

Talk to Claude. The skill maps what you say to commands and asks for whatever is missing.
You can also run the commands yourself:

| Command | Does |
|---|---|
| `tempo add NAME -e 1h30m [-n NOTES]` | create a task |
| `tempo start ID` | start a session (stops the active task) |
| `tempo break` / `tempo resume` | pause and restart the clock |
| `tempo confirm` | "still on it" |
| `tempo friction "text" [--task ID]` | log a friction point |
| `tempo stop [--at 14:30\|inferred\|unknown]` | stop without finishing |
| `tempo done [ID] [--reason "..."]` | finish; add why it ran over |
| `tempo list [--all]` | show tasks |
| `tempo doctor` | check the data; prints the exact fix for each problem |
| `tempo reconcile` | list unconfirmed sessions; resolve with `--accept`, `--end` or `--discard` |
| `tempo report daily [--date yesterday] [--stdout]` | write the day's report |

Task ids can be shortened to any unique prefix.

## Where things live

Everything is under `~/.tempo` (override with `TEMPO_HOME`):

| Path | |
|---|---|
| `tasks.json` | all tasks, sessions, breaks and friction |
| `tasks.json.bak` | the last good copy, refreshed before each write |
| `reports/` | generated reports, e.g. `daily-2026-10-07.json` |

Only the `tempo` command writes `tasks.json`. Writes are atomic and serialized with a file
lock, and a waiting caller gives up after 5 seconds and names the process holding the
lock. If `tasks.json` is ever unreadable, tempo sets it aside as
`tasks.json.corrupt-<time>`, keeps tracking on a fresh file and tells you.

## Development

```bash
python3 -m unittest            # no network, no real notifications, no sleeping
```

The tests use a fake `nudge` and temporary directories, so they never touch your real
data or reminders.

## License

None yet. Until one is added, the default is all rights reserved.
