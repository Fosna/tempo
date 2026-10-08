# Idea: remind of open and unreconciled work when a Claude Code session starts

Parked on 2026-10-09.

Trigger: tasks are often started in chat, worked on in another tool, and closed in chat later,
sometimes from a different Claude Code session. Closing and reconciling depend on discipline,
and the skill's "run `tempo doctor` first" rule only works when Claude remembers it.

Idea: a Claude Code `SessionStart` hook (startup, resume, clear; not compact) that surfaces
tempo state deterministically.

- Runs one fast, read-only check (`tempo doctor` or a new `tempo status`). Never writes state.
- Silent when nothing is open and nothing awaits reconciliation.
- Otherwise reports the open task, its age against the estimate, and the reconcile count, e.g.
  `▶ 'write onboarding doc' open since 14:10 (1h20m, est 45m) · 2 sessions to reconcile`.
- Fails silent if `tempo` is missing or errors. Never blocks the session.
- Output can go to the user (`systemMessage`), to Claude (`additionalContext`), or both.
  Leaning both: the user sees it, and Claude asks "when did you stop?" in its first reply.
- Could replace the skill's "Every time, first: `tempo doctor`" rule.

Related: `parked-ideas/backfill-past-sessions.md` (closing late needs a past stop time),
`parked-ideas/status-line-timer.md` (possible overlap on the user-facing half; separate idea).

## Open questions

- Shown to the user, to Claude, or both?
- Shipped in the skill's frontmatter, or in `~/.claude/settings.json` via `install.py`?

Not designed. Not acted on.
