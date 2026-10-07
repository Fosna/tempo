"""Invariant checks for tempo state (F10).

Read-only: `check` looks at the state and returns findings, each with the exact
command that fixes it. Nothing here changes data; the user confirms every fix.

Not checked yet (they need the nudge adapter from step 3): orphaned nudges and a
nudge daemon that is not running.
"""

from datetime import datetime

import tasks

ERROR = "error"
WARN = "warn"

_MANUAL = "edit %s in ~/.tempo/tasks.json by hand (last good copy: ~/.tempo/tasks.json.bak)"


def _finding(severity, code, message, fixes):
    return {"severity": severity, "code": code, "message": message, "fixes": fixes}


def _label(task):
    return "%s %r" % (task["id"], task["name"])


def _open(task):
    return bool(task["sessions"]) and task["sessions"][-1]["end"] is None


def _check_times(task):
    """Messages for sessions and breaks whose timestamps contradict each other."""
    problems = []
    for s in task["sessions"]:
        where = "task %s session %s" % (task["id"], s["id"])
        try:
            start = datetime.fromisoformat(s["start"])
            end = datetime.fromisoformat(s["end"]) if s["end"] else None
            if end is not None and end < start:
                problems.append("%s ends before it starts" % where)
            for b in s["breaks"]:
                b_start = datetime.fromisoformat(b["start"])
                b_end = datetime.fromisoformat(b["end"]) if b["end"] else None
                if b_start < start or (end is not None and b_start > end):
                    problems.append("%s has a break that starts outside it" % where)
                elif b_end is not None and b_end < b_start:
                    problems.append("%s has a break that ends before it starts" % where)
                elif b_end is not None and end is not None and b_end > end:
                    problems.append("%s has a break that ends after it" % where)
        except (ValueError, KeyError, TypeError):
            problems.append("%s has an unreadable timestamp" % where)
    return problems


def check(state, now, aside=()):
    findings = []
    all_tasks = state["tasks"]

    active = [t for t in all_tasks if t["status"] == "active"]
    if len(active) > 1:
        keep = max(active, key=lambda t: t["sessions"][-1]["start"] if t["sessions"] else "")
        fixes = [
            "tempo stop --task %s --at inferred" % t["id"] for t in active if t is not keep
        ]
        findings.append(
            _finding(
                ERROR,
                "multiple_active",
                "several tasks are active (%s); only one can be. Keeping %s, the most recently started."
                % (", ".join(t["id"] for t in active), keep["id"]),
                fixes,
            )
        )

    for t in all_tasks:
        has_open = _open(t)
        if t["status"] == "active" and not has_open:
            findings.append(
                _finding(
                    ERROR,
                    "status_mismatch",
                    "task %s is active but has no open session" % _label(t),
                    ["tempo stop --task %s" % t["id"]],
                )
            )
        elif has_open and t["status"] != "active":
            findings.append(
                _finding(
                    ERROR,
                    "status_mismatch",
                    "task %s has an open session but its status is %s" % (_label(t), t["status"]),
                    ["tempo stop --task %s --at inferred" % t["id"]],
                )
            )

        for problem in _check_times(t):
            findings.append(_finding(WARN, "bad_times", problem, [_MANUAL % ("task " + t["id"])]))

    for t in active:
        if not _open(t):
            continue
        session = t["sessions"][-1]
        try:
            confirmed = datetime.fromisoformat(session["lastConfirmedAt"])
        except (ValueError, KeyError, TypeError):
            continue  # reported as an unreadable timestamp above only if it is a start/end
        quiet_min = (now - confirmed).total_seconds() / 60
        limit = tasks.stale_after_min(t, now)
        if quiet_min > limit:
            findings.append(
                _finding(
                    WARN,
                    "zombie",
                    "task %s has been open %dm since you last confirmed it (limit %dm); "
                    "last confirmed %s"
                    % (_label(t), quiet_min, limit, session["lastConfirmedAt"]),
                    [
                        "tempo stop --task %s --at inferred   (ends it at the last confirmed time)"
                        % t["id"],
                        "tempo stop --task %s --at HH:MM      (if you remember when you stopped)"
                        % t["id"],
                        "tempo stop --task %s --at unknown    (send it to reconciliation)" % t["id"],
                        "tempo confirm                        (if you are still on it)",
                    ],
                )
            )

    for name in aside:
        findings.append(
            _finding(
                WARN,
                "set_aside_file",
                "%s holds data from an unreadable tasks.json that was never merged back" % name,
                ["inspect it, merge what you need by hand, then delete or move it"],
            )
        )

    findings.sort(key=lambda f: f["severity"] != ERROR)
    return findings
