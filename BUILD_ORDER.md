# tempo: build order

Follow nudge's layout: flat Python modules, tests alongside, an `install.py` that installs the CLI and skills globally. Each step ships with tests before the next starts.

1. **Store + CLI core.** `store.py` (flock, atomic write, `.bak`). Corrupt-file recovery needs its own deep-dive design here: tracking must continue on a fresh file, and repair/merge comes later. Also `timespec.py` (copied from nudge), `tempo.py` with create, start, stop, break, resume, friction, done, list.
2. **Doctor.** `tempo doctor` with the F10 checks. Built early so every later step can lean on it.
3. **Nudge integration.** Schedule and cancel nudges per F4; store `nudgeIds` on the session; update `lastConfirmedAt` on reply. Also the two doctor checks that need the nudge adapter: orphaned nudges and a daemon that is not running.
4. **Tempo skill.** Chat interface over the CLI: runs doctor first, announces auto-closed tasks, asks for missing data.
5. **Report.** `tempo report daily` writing JSON to `~/.tempo/reports/`, including the unresolved list.
6. **Reconcile.** `tempo reconcile` to resolve `unknown` and `inferred` sessions.
7. **Analytics skill.** Reads report JSON, drives reconciliation, writes data, graphs, narrative, wins, misses, learnings and decision to `~/.tempo/analysis/`.
8. **Install.** `install.py` copies the CLI and both skills into place.

**Later:** recurring report generation, weekly report, nudge reply buttons.
