# Where to pick up

Last updated 2026-10-09.

## Next step

Evaluate community projects similar to tempo, one at a time. For each: read the repo, then decide
to borrow an idea, try it in parallel with tempo, or drop it. Goal: save time and find better
spec and implementation loops before building more.

Start with `arte-ermel/claude-code-time-estimator` (closest to the planned analytics skill).

## Community projects similar to tempo

Found by web search on 2026-10-09. Only summaries read, no code reviewed yet.

### Estimate vs actual (closest to tempo's goal)

- [arte-ermel/claude-code-time-estimator](https://github.com/arte-ermel/claude-code-time-estimator):
  estimates as a range, logs actual time, tracks calibration accuracy, corrects future estimates
  per domain and size. Overlaps the parked analytics skill.
- [samsaar/claude-code-time-estimator](https://github.com/samsaar/claude-code-time-estimator):
  similar. Logs from phrases like "that took 45 min". Per-project time by day.

### Automatic tracking from hooks or transcripts

- [RemoteCTO/claude-code-timelog](https://github.com/RemoteCTO/claude-code-timelog): plugin.
  Logs sessions, prompts, projects and tickets as JSONL. Ticket detection, break-aware durations,
  backfill from existing transcripts. See `parked-ideas/backfill-past-sessions.md`.
- [aguinaldotupy/claude-session-tracker](https://github.com/aguinaldotupy/claude-session-tracker):
  SessionStart/SessionEnd hooks, idle vs working time, status line. See
  `parked-ideas/session-start-reminder.md` and `parked-ideas/status-line-timer.md`.
- [fikret/claude-code-time-tracker](https://github.com/fikret/claude-code-time-tracker): time per
  project from transcripts, shown in the status line.
- [keithmackay/sessionstats](https://github.com/keithmackay/sessionstats): per-session duration,
  tokens and cost via hooks.
- [martinambrus/claude_timings_wrapper](https://github.com/martinambrus/claude_timings_wrapper):
  idle, typing and agent time via PTY wrapping and hooks.

### Other

- [nnemirovsky/ticktock](https://github.com/nnemirovsky/ticktock): injects timestamps and elapsed
  time into Claude's context via hooks. Time awareness, not a tracker.
- [timesheetIO/timesheet-plugin](https://github.com/timesheetIO/timesheet-plugin): official
  timesheet.io plugin. Needs an account.
- MCP servers for Toggl, Clockify and Timewarrior: [list](https://mcp.so/tags/time-tracking).

### What seems unique to tempo (to verify)

Honest data (asks, never guesses), reconciliation, and work done outside Claude Code.

## Claude Code native tools noted

- `/insights`: tried, interesting, more to dig into. Possible input for the analytics skill.
- `/recap`: one-line session summary. Could fill notes on close (`parked-ideas/note-task-outputs.md`).
