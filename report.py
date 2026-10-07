"""Reports (F7): numbers computed from tasks.json, for the analytics skill to read.

Pure over the state dict. A report covers a window of time; sessions that cross the
window's edge are clipped, so a session running 23:00-01:00 counts one hour on each
day. Unresolved sessions are listed across all history, not just the window, because
an unreconciled session stays unreconciled until someone resolves it.
"""

import json
import os
import tempfile
from datetime import datetime, time, timedelta

import tasks

SCHEMA_VERSION = 1


def _ts(s):
    return datetime.fromisoformat(s)


def _minutes(seconds):
    return round(seconds / 60, 1)


def day_window(day):
    """Local midnight to the next local midnight, so a DST day is 23 or 25 hours."""
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return start, end


def _end_of(session, now):
    return _ts(session["end"]) if session["end"] else now


def _active_intervals(session, now):
    """The stretches of a session actually spent working: the session minus its breaks."""
    end = _end_of(session, now)
    cursor = _ts(session["start"])
    out = []
    for b in session["breaks"]:
        b_start = _ts(b["start"])
        b_end = _ts(b["end"]) if b["end"] else end
        if b_start > cursor:
            out.append((cursor, min(b_start, end)))
        cursor = max(cursor, b_end)
    if end > cursor:
        out.append((cursor, end))
    return out


def _clipped_seconds(session, now, w_start, w_end):
    total = 0.0
    for start, end in _active_intervals(session, now):
        total += max(0.0, (min(end, w_end) - max(start, w_start)).total_seconds())
    return total


def _overlaps(session, now, w_start, w_end):
    return _ts(session["start"]) < w_end and _end_of(session, now) > w_start


def _unresolved_kind(task, session, now):
    """`unknown`, `inferred`, `zombie`, or None for a session that needs nothing."""
    state = session["endState"]
    if state in ("unknown", "inferred"):
        return state
    if state == "open":
        if task["status"] != "active" or session is not task["sessions"][-1]:
            return "zombie"
        quiet = (now - _ts(session["lastConfirmedAt"])).total_seconds() / 60
        if quiet > tasks.stale_after_min(task, now):
            return "zombie"
    return None


def build_window(state, kind, label, w_start, w_end, now):
    in_window = []
    unresolved = []
    by_end_state = {"open": 0, "confirmed": 0, "inferred": 0, "unknown": 0}
    flagged_tasks = set()

    for task in state["tasks"]:
        for session in task["sessions"]:
            problem = _unresolved_kind(task, session, now)
            if problem:
                flagged_tasks.add(task["id"])
                started = _ts(session["start"])
                unresolved.append(
                    {
                        "kind": problem,
                        "taskId": task["id"],
                        "taskName": task["name"],
                        "sessionId": session["id"],
                        "start": session["start"],
                        "end": session["end"],
                        "lastConfirmedAt": session["lastConfirmedAt"],
                        "onDate": started.date().isoformat(),
                        "inWindow": w_start <= started < w_end,
                    }
                )

    worked_seconds = 0.0
    completed = {"count": 0, "estimateMin": 0, "actualMin": 0.0, "deltaMin": 0.0}
    tasks_worked = 0

    for task in state["tasks"]:
        sessions = [s for s in task["sessions"] if _overlaps(s, now, w_start, w_end)]
        window_seconds = sum(_clipped_seconds(s, now, w_start, w_end) for s in sessions)
        friction = [f for f in task["friction"] if w_start <= _ts(f["at"]) < w_end]
        done_here = bool(task["doneAt"]) and w_start <= _ts(task["doneAt"]) < w_end
        if not (sessions or friction or done_here):
            continue

        for s in sessions:
            by_end_state[s["endState"]] += 1
        total_seconds = tasks.actual_seconds(task, now)
        estimate = task["estimateMin"]
        delta = _minutes(total_seconds - estimate * 60)
        in_window.append(
            {
                "id": task["id"],
                "name": task["name"],
                "status": task["status"],
                "estimateMin": estimate,
                "notes": task["notes"],
                "actualWindowMin": _minutes(window_seconds),
                "actualTotalMin": _minutes(total_seconds),
                "deltaMin": delta,
                "overrunReason": task["overrunReason"],
                "reliable": task["id"] not in flagged_tasks,
                "createdAt": task["createdAt"],
                "doneAt": task["doneAt"],
                "sessions": [
                    {
                        "id": s["id"],
                        "start": s["start"],
                        "end": s["end"],
                        "endState": s["endState"],
                        "windowMin": _minutes(_clipped_seconds(s, now, w_start, w_end)),
                        "breaks": len(s["breaks"]),
                    }
                    for s in sessions
                ],
                "friction": friction,
            }
        )
        worked_seconds += window_seconds
        if window_seconds > 0:
            tasks_worked += 1
        if done_here:
            completed["count"] += 1
            completed["estimateMin"] += estimate
            completed["actualMin"] += _minutes(total_seconds)
            completed["deltaMin"] += delta

    completed["actualMin"] = round(completed["actualMin"], 1)
    completed["deltaMin"] = round(completed["deltaMin"], 1)
    unresolved.sort(key=lambda u: u["start"])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": kind,
        "label": label,
        "generatedAt": now.isoformat(),
        "window": {"start": w_start.isoformat(), "end": w_end.isoformat()},
        "totals": {
            "workedMin": _minutes(worked_seconds),
            "tasksWorked": tasks_worked,
            "completed": completed,
        },
        "sessionsByEndState": by_end_state,
        "tasks": in_window,
        "unresolved": unresolved,
    }


def build_daily(state, day, now):
    w_start, w_end = day_window(day)
    return build_window(state, "daily", day.isoformat(), w_start, w_end, now)


def filename(report):
    return "%s-%s.json" % (report["kind"], report["label"])


def write(report, directory):
    """Write the report atomically; running it again for the same window replaces it."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename(report))
    fd, tmp = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
