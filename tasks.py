"""Task and session rules.

Pure functions over the state dict from `store`, so the rules can be tested without
touching disk. Every function that needs the time takes `now`.
"""

import re
import secrets
from datetime import datetime

UNKNOWN = "unknown"
INFERRED = "inferred"

# When a session counts as a zombie (F5). Tunable; the nudge cadence (F4) drives it.
NO_NUDGE_GRACE_MIN = 60  # tasks under 30m get no nudges, so allow this long unconfirmed
BREAK_GRACE_MIN = 90  # a break may legitimately be long (lunch)


class TempoError(Exception):
    pass


def now_local():
    return datetime.now().astimezone().replace(microsecond=0)


def _ts(s):
    return datetime.fromisoformat(s)


def _iso(dt):
    return dt.isoformat()


def parse_when(text, now):
    """`unknown`, `inferred`, `HH:MM` (today), or an ISO timestamp (naive means local)."""
    text = text.strip()
    if text.lower() in (UNKNOWN, INFERRED):
        return text.lower()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    try:
        if m:
            return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise TempoError("bad time: %r (try 14:30, an ISO timestamp, 'inferred' or 'unknown')" % text)
    return dt if dt.tzinfo else dt.replace(tzinfo=now.tzinfo)


def find_task(state, prefix):
    for t in state["tasks"]:
        if t["id"] == prefix:
            return t
    matches = [t for t in state["tasks"] if t["id"].startswith(prefix)]
    if not matches:
        raise TempoError("no such task: %s" % prefix)
    if len(matches) > 1:
        raise TempoError(
            "ambiguous id %s: %s" % (prefix, ", ".join(t["id"] for t in matches))
        )
    return matches[0]


def active_task(state):
    active = [t for t in state["tasks"] if t["status"] == "active"]
    if len(active) > 1:
        raise TempoError(
            "more than one active task (%s); fix the data before continuing"
            % ", ".join(t["id"] for t in active)
        )
    return active[0] if active else None


def _open_session(task):
    if task["sessions"] and task["sessions"][-1]["end"] is None:
        return task["sessions"][-1]
    raise TempoError("task %s has no open session" % task["id"])


def _touch(task, now):
    """Record that the user was demonstrably on the task at `now`."""
    if task["sessions"] and task["sessions"][-1]["end"] is None:
        task["sessions"][-1]["lastConfirmedAt"] = _iso(now)


def add_task(state, name, estimate_min, notes, now):
    name = name.strip()
    if not name:
        raise TempoError("task name is empty")
    ids = {t["id"] for t in state["tasks"]}
    task_id = secrets.token_hex(3)
    while task_id in ids:
        task_id = secrets.token_hex(3)
    task = {
        "id": task_id,
        "name": name,
        "estimateMin": estimate_min,
        "notes": notes or "",
        "status": "todo",
        "overrunReason": None,
        "createdAt": _iso(now),
        "doneAt": None,
        "sessions": [],
        "friction": [],
    }
    state["tasks"].append(task)
    return task


def _resolve_end(session, at, now):
    if at is None:
        end, end_state = now, "confirmed"
    elif at in (UNKNOWN, INFERRED):
        # placeholder: the last moment we know the user was on the task. `inferred`
        # means the user accepted that time; `unknown` sends it to reconciliation.
        end, end_state = _ts(session["lastConfirmedAt"]), at
    else:
        end, end_state = at, "confirmed"
        if end < _ts(session["start"]):
            raise TempoError(
                "end time is before the session started (%s)" % session["start"]
            )
        if end > now:
            raise TempoError("end time is in the future")
    if session["breaks"] and session["breaks"][-1]["end"] is None:
        if end < _ts(session["breaks"][-1]["start"]):
            raise TempoError("end time is before the current break started")
    return end, end_state


def _close_session(task, session, end, end_state):
    if session["breaks"] and session["breaks"][-1]["end"] is None:
        session["breaks"][-1]["end"] = _iso(end)
    session["end"] = _iso(end)
    session["endState"] = end_state
    if task["status"] == "active":
        task["status"] = "todo"


def stop_active(state, now, at=None, task_id=None):
    """Stop the active task's session, or the named task's (which also repairs a task
    whose status and open session disagree)."""
    if task_id:
        task = find_task(state, task_id)
        has_session = bool(task["sessions"]) and task["sessions"][-1]["end"] is None
        if not has_session and task["status"] != "active":
            raise TempoError("task %s has nothing to stop" % task["id"])
        if not has_session:
            task["status"] = "todo"  # active without a session: just repair the status
            return task
    else:
        task = active_task(state)
        if task is None:
            raise TempoError("nothing is active")
    session = _open_session(task)
    end, end_state = _resolve_end(session, at, now)
    _close_session(task, session, end, end_state)
    return task


def start_task(state, task_id, now, prev_end=None):
    """Start a session. Returns (task, stopped) where stopped is the task that was
    auto-stopped to make room, or None."""
    task = find_task(state, task_id)
    if task["status"] == "done":
        raise TempoError("task %s is done" % task["id"])
    if task["status"] == "active":
        raise TempoError("task %s is already active" % task["id"])
    stopped = None
    if active_task(state) is not None:
        stopped = stop_active(state, now, prev_end)
    elif prev_end is not None:
        raise TempoError("--prev-end given but no task was active")
    task["sessions"].append(
        {
            "id": "s%d" % (len(task["sessions"]) + 1),
            "start": _iso(now),
            "end": None,
            "endState": "open",
            "lastConfirmedAt": _iso(now),
            "breaks": [],
            "nudgeIds": [],
        }
    )
    task["status"] = "active"
    return task, stopped


def start_break(state, now):
    task = active_task(state)
    if task is None:
        raise TempoError("nothing is active")
    session = _open_session(task)
    if session["breaks"] and session["breaks"][-1]["end"] is None:
        raise TempoError("already on a break")
    session["breaks"].append({"start": _iso(now), "end": None})
    _touch(task, now)
    return task


def end_break(state, now):
    task = active_task(state)
    if task is None:
        raise TempoError("nothing is active")
    session = _open_session(task)
    if not session["breaks"] or session["breaks"][-1]["end"] is not None:
        raise TempoError("not on a break")
    session["breaks"][-1]["end"] = _iso(now)
    _touch(task, now)
    return task


def add_friction(state, text, now, task_id=None):
    text = text.strip()
    if not text:
        raise TempoError("friction text is empty")
    task = find_task(state, task_id) if task_id else active_task(state)
    if task is None:
        raise TempoError("no active task; pass --task ID")
    task["friction"].append({"at": _iso(now), "text": text})
    return task


def confirm(state, now):
    """The user says they are still on the active task."""
    task = active_task(state)
    if task is None:
        raise TempoError("nothing is active")
    _open_session(task)
    _touch(task, now)
    return task


def complete_task(state, task_id, now, reason=None):
    """Mark a task done, stopping its session first. Calling it again on a done task
    with a reason records the overrun reason after the fact."""
    task = find_task(state, task_id) if task_id else active_task(state)
    if task is None:
        raise TempoError("no active task; pass a task id")
    if task["status"] == "done":
        if not reason:
            raise TempoError("task %s is already done" % task["id"])
        task["overrunReason"] = reason
        return task
    if task["status"] == "active":
        session = _open_session(task)
        _close_session(task, session, now, "confirmed")
    task["status"] = "done"
    task["doneAt"] = _iso(now)
    if reason:
        task["overrunReason"] = reason
    return task


def session_seconds(session, now):
    end = _ts(session["end"]) if session["end"] else now
    secs = (end - _ts(session["start"])).total_seconds()
    for b in session["breaks"]:
        b_end = _ts(b["end"]) if b["end"] else end
        secs -= (b_end - _ts(b["start"])).total_seconds()
    return max(0, int(secs))


def actual_seconds(task, now):
    return sum(session_seconds(s, now) for s in task["sessions"])


def nudge_interval_min(estimate_min, actual_min):
    """Minutes between "still on it?" nudges (F4), or None when the task gets none.

    A 30m estimate's single midpoint nudge at 15m is a scheduling detail of step 3.
    """
    if estimate_min < 30:
        return None
    if actual_min >= estimate_min:
        return 15
    return 30


def stale_after_min(task, now):
    """How long an open session may go unconfirmed before it counts as a zombie."""
    session = task["sessions"][-1]
    if session["breaks"] and session["breaks"][-1]["end"] is None:
        return BREAK_GRACE_MIN
    interval = nudge_interval_min(task["estimateMin"], actual_seconds(task, now) / 60)
    return 2 * interval if interval else NO_NUDGE_GRACE_MIN
