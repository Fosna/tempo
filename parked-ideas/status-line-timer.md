# Idea: show the active task and its timer in the Claude Code status line

Parked on 2026-10-09. The user loves this one.

Trigger: while evaluating Claude Code native features for tempo, the status line stood out as a
way to keep the open task visible without asking Claude or waiting for a nudge.

Idea: a Claude Code status line script that prints tempo's state in one line.

- New read-only command, e.g. `tempo status --line`. Never writes state.
- Status line config with `refreshInterval: 60` so the timer moves while the session is idle.
- Active: `▶ write onboarding doc · 23m / 45m`. Past the estimate: same line in a warning colour.
- On break: `⏸ on break · 12m`.
- Unreconciled sessions: append `· 2 to reconcile`.
- No task: nothing, or a dim `tempo idle`.
- Only visible while a Claude Code window is in front of the user, not in other tools.

Related: `parked-ideas/session-start-reminder.md` (possible overlap: the status line covers the
user-facing half of that reminder, all the time. It is a separate idea: it cannot make Claude
ask "when did you stop?", which the hook can.)

## Open questions

- Installed by `install.py`, or documented as a manual settings step?
- How to combine with a status line the user already has?

Not designed. Not acted on.
