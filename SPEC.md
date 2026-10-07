# tempo: MVP spec

Track tasks, estimates and actual time from a Claude Code chat. Review estimate-vs-actual through generated reports and an analytics skill.

## Principles

- **Claude Code CLI is the interface.** No separate UI.
- **One writer.** A small CLI (`tempo`) owns every write to disk. Skills never edit the JSON directly.
- **Numbers from code, interpretation from LLM.** A script computes the report. The analytics skill reads it.
- **Don't over-engineer.** Collect data first. Analytics will show which fields are missing.

## Architecture

```
 Claude Code chat ──> tempo skill ──> tempo CLI ──> ~/.tempo/tasks.json
                          │                │
                          │                └──> nudge CLI (sticky macOS alerts)
                          │
                          └──(forked task, on demand)──> tempo report ──> ~/.tempo/reports/*.json

 Analytics session:
   analytics skill ──reads──> reports/*.json
        │  unresolved items? ──> asks user ──> tempo CLI (reconcile) ──> regenerate report
        └──> data, graphs, narrative, wins, misses, learnings, decision
```

- No daemon of our own. In-session timing is delegated to `/Users/fosna/Programming/nudge`.
- Nudge alerts are macOS alerts, which stay on screen until dismissed. They do not expire.
- Nudge has no reply buttons. You respond in the CLI. Buttons can be added later if they serve a purpose.

## Storage and integrity

Modeled on nudge's `store.py`:
- One JSON file, `~/.tempo/tasks.json`.
- A separate lock file (`tasks.lock`) held with `flock(LOCK_EX)` for every read-modify-write.
- Atomic write: temp file in the same directory, then `os.replace`.
- `schemaVersion` in the file.

**One deliberate difference:** nudge treats a corrupt queue as empty and overwrites it on the next write. Tempo never destroys an unreadable file: it sets it aside, keeps tracking on a new one, and warns (see F10). A `tasks.json.bak` copy is kept before each write. File naming and recovery details are decided during implementation.

## Data

```json
{
  "schemaVersion": 1,
  "tasks": [
    {
      "id": "a1b2c3",
      "name": "Write onboarding doc",
      "estimateMin": 90,
      "notes": "free text",
      "status": "todo | active | done",
      "overrunReason": null,
      "createdAt": "2026-10-07T09:00:00+02:00",
      "doneAt": null,
      "sessions": [
        {
          "id": "s1",
          "start": "2026-10-07T09:05:00+02:00",
          "end": null,
          "endState": "open | confirmed | inferred | unknown",
          "lastConfirmedAt": "2026-10-07T09:35:00+02:00",
          "breaks": [{ "start": "...", "end": "..." }],
          "nudgeIds": ["3f1a9c"]
        }
      ],
      "friction": [{ "at": "...", "text": "free text" }]
    }
  ]
}
```

- **Actual time** is computed: sum of session durations minus breaks. Never typed in.
- **Estimates** are entered free-form ("45", "1h30m") and parsed to integer minutes, reusing nudge's `timespec.py`. Claude may propose an estimate; the user adjusts. Only the final value is stored.
- **Notes** and **friction** are free text. Friction is zero or more timestamped entries, added during or after work, never at planning time.
- **Out of scope:** subtasks, categories, projects, tags.

## Functional requirements

### F1. Tasks
- Create a task with name, estimate, optional notes.
- Statuses: `todo`, `active`, `done`.
- Complete a task with an optional overrun reason (asked when actual exceeds the estimate).

### F2. Sessions
- Start and stop a session; the CLI stamps the current time.
- Breaks are pause/resume timestamp pairs. No reason or length is recorded.
- **One active task at a time.** Starting a task auto-stops the current session, returns that task to `todo`, cancels its nudges, and the skill tells the user.
- On auto-stop the skill suggests an end time. The user either confirms it or says they don't remember.

### F3. Session end states
| State | Meaning |
|---|---|
| `open` | session running |
| `confirmed` | user gave or approved the stop time |
| `inferred` | auto-closed at `lastConfirmedAt`, user accepted the suggestion |
| `unknown` | user doesn't remember; goes to reconciliation |

### F4. Nudges
Rules are based on the task estimate and on cumulative actual time across its sessions.
- Estimate under 30 min: no nudges.
- Estimate of exactly 30 min: one nudge at 15 min.
- Longer estimates: a nudge every 30 min.
- Once cumulative actual reaches the estimate: a nudge every 15 min.
- The skill may schedule several nudges at once.
- Each nudge id is stored on the session (`nudgeIds`) so stop, break and auto-close can cancel them.
- Replying "still on it" updates `lastConfirmedAt`.

### F5. Zombie prevention
- Every skill call first checks for sessions that are open past their nudge window with no recent confirmation.
- Such a session is closed at `lastConfirmedAt` and marked `inferred`, after asking the user (see F2).
- Reports list `inferred` and `unknown` sessions separately from `confirmed` ones.

### F6. Friction log
- Add a timestamped free-text entry to the active (or a named) task at any time.

### F7. Reports
- `tempo report daily|weekly` is a script that reads `tasks.json` and writes JSON to `~/.tempo/reports/` (for example `daily-2026-10-07.json`).
- Contents: per-task estimate, actual and delta; totals; session counts by end state; friction entries; and an **unresolved** list (`unknown` sessions and open zombies).
- The tempo skill runs it on demand as a forked Claude task.
- Reports are read-only views. They never write to `tasks.json`.

### F8. Reconciliation
- Lives in the tempo CLI and skill, so writes stay in one place.
- Resolves `unknown` sessions: the user supplies an end time or discards the session.
- Driven by the analytics skill (F9): it reads the report's unresolved list, asks the user, calls the tempo CLI, and regenerates the report.

### F9. Analytics skill
- Separate skill and session. Read-only on the data; it takes report JSON as input.
- First step: resolve anything in the unresolved list through F8.
- Output: data, graphs, narrative, wins, misses, learnings, decision.

### F10. Recovery and troubleshooting
`tempo doctor` checks invariants and prints findings. It is read-only. Each finding has a severity and the exact command that fixes it, so the user confirms before anything changes. The skill runs it at the start of every call and surfaces only non-empty findings.

| Finding | Detection | Fix offered |
|---|---|---|
| `tasks.json` unreadable | JSON parse fails | keep tracking first: set the bad file aside and continue on a new one; warn the user; repair later (see principle below) |
| Zombie session | open past nudge window, no recent confirmation (F5) | close at `lastConfirmedAt` as `inferred`, or ask for the real end |
| More than one active task | invariant check | pick which stays active; the rest return to `todo` |
| Session end before start, or break outside its session | invariant check | edit the timestamp or mark `unknown` |
| Orphaned nudge | pending nudge id belongs to a closed session or done task | `nudge cancel <id>` |
| Nudge daemon not running | `nudge status` | warn: no nudges will fire, so zombies are likely; point to nudge's install |

Flagging: severity `error` blocks the requested action until resolved (multiple active tasks); `warn` is shown and the action proceeds.

**Priority principle:** capturing new tasks and sessions matters more than repairing old data. A corrupted file must never stop the user from tracking. Tempo sets the bad file aside, keeps recording on a fresh one, and flags the problem; repair and merge happen later, at the user's convenience. The recovery mechanics (restore from `.bak`, merging the moved-aside file with new data) are an implementation-step design, not decided here.

## MVP cut

**In**
- `tasks.json` + `tempo` CLI: create, start, stop, break, resume, friction, done, list.
- The tempo skill (chat interface).
- Nudge integration (F4).
- Zombie recovery with `inferred`/`unknown` (F3, F5).
- `tempo doctor` (F10).
- `tempo report daily` (F7).
- Reconcile command (F8), because the analytics skill depends on it.
- Analytics skill (F9).

**Later**
- Recurring report generation (launchd or cron).
- `tempo report weekly`.
- Nudge reply buttons.
- Structured fields (categories, subtasks), only if the analytics ask for them.

## Open items

None blocking. Build order is in `BUILD_ORDER.md`.

## Decisions

- Repo layout and install follow nudge (copy its `install.py` approach).
- Timestamps are ISO with offset; a report day is local midnight to local midnight.
- Unreadable `tasks.json` never blocks tracking: set it aside, continue on a new file, warn. Recovery design is an implementation step.
- Nudge basis: task estimate plus cumulative actual time.
- Analytics output goes to `~/.tempo/analysis/`.
- `inferred` sessions count as unresolved in reports.
