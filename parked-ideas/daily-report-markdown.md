# Idea: write a markdown summary next to each daily report

Parked on 2026-10-08.

Trigger: `tempo report daily` writes only `~/.tempo/reports/daily-YYYY-MM-DD.json`. The prose
summary (totals, estimate vs actual table, overrun reasons, friction) had to be written by hand
to get something readable and storable, for example for a work log.

Idea: generate a `daily-YYYY-MM-DD.md` beside the JSON, from the same data, so the summary is
stored without a manual step.

- `tempo report daily` writes both files. The `.md` is derived from the JSON, never the other
  way round, so the two cannot disagree.
- Content: totals, one table row per task (estimate, actual, delta), overrun reasons, friction,
  and a line for unresolved sessions if there are any.
- Regenerating overwrites the `.md` together with the JSON.
- `--stdout` could take a `--format md` option to print the markdown instead of the JSON.

Related: `parked-ideas/backup-reports.md`, `parked-ideas/note-task-outputs.md` (notes would show
up in this summary).

## Open questions

- Always write the `.md`, or only with a flag?
- Fixed layout, or a template the user can edit?

Not designed. Not acted on.
