# Idea: back up the reports folder

Parked on 2026-10-08.

Trigger: `~/.tempo/reports/` holds the only record of finished days outside `tasks.json`, and
nothing copies it anywhere. Losing the folder or the machine loses that history.

Idea: make backing up reports a supported step instead of something the user has to remember.

- Document a plain way to do it first (copy or sync `~/.tempo/reports/`), with no new command.
- Possible later: `tempo report daily` copies the file to a configured backup directory.
- Consider whether `tasks.json` and `tasks.json.bak` belong in the same backup.

Related: `parked-ideas/daily-report-markdown.md` (the `.md` files would be backed up too).

## Open questions

- Is documentation enough, or does tempo need to do the copy itself?
- Where would the backup directory be configured, given tempo has no config file today?

Not designed. Not acted on.
