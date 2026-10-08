"""Task and session rules.

Pure functions over the state dict from `store`, so the rules can be tested without
touching disk. Every function that needs the time takes `now`.
"""

import re
import secrets
from datetime import datetime, timedelta

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


def _next_session_id(task):
    # max+1, not len+1: reconciling can discard a session and ids must stay unique
    nums = [int(s["id"][1:]) for s in task["sessions"] if s["id"][1:].isdigit()]
    return "s%d" % (max(nums, default=0) + 1)


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
            "id": _next_session_id(task),
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


def drop_task(state, task_id):
    """Delete a task outright, sessions and friction included: dropped time counts
    nowhere. An active task must be stopped first."""
    task = find_task(state, task_id)
    if task["status"] == "active":
        raise TempoError("task %s is active; tempo stop first" % task["id"])
    state["tasks"].remove(task)
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


MAX_AHEAD = 6  # nudges are one-shot, so schedule this many ahead and top up on confirm


def _nudge_marks(estimate_min):
    """Cumulative-actual minutes at which a nudge is due (F4), ascending."""
    if estimate_min == 30:
        yield 15
    elif estimate_min > 30:
        m = 30
        while m < estimate_min:
            yield m
            m += 30
    m = estimate_min
    while True:
        yield m
        m += 15


def nudge_marks(estimate_min, actual_min, count=MAX_AHEAD):
    """The next `count` nudge marks strictly after `actual_min`. Tasks under 30m get none."""
    if estimate_min < 30:
        return []
    out = []
    for m in _nudge_marks(estimate_min):
        if m > actual_min:
            out.append(m)
            if len(out) == count:
                break
    return out


def record_nudges(state, task_id, session_id, removed=(), added=()):
    """Update the nudge ids kept on a session. Quietly does nothing if it has gone."""
    for task in state["tasks"]:
        if task["id"] != task_id:
            continue
        for session in task["sessions"]:
            if session["id"] == session_id:
                kept = [i for i in session["nudgeIds"] if i not in removed]
                session["nudgeIds"] = kept + list(added)


def unresolved_sessions(state):
    """(task, session) pairs whose end the user never confirmed: unknown and inferred."""
    return [
        (t, s)
        for t in state["tasks"]
        for s in t["sessions"]
        if s["endState"] in (UNKNOWN, INFERRED)
    ]


def parse_end_near(text, session, now):
    """An end time for an old session. HH:MM means on the session's own day, or the
    next day when that would land before the start (an overnight session)."""
    text = text.strip()
    if text.lower() in (UNKNOWN, INFERRED):
        raise TempoError("use --accept to keep the recorded end time")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not m:
        return parse_when(text, now)
    start = _ts(session["start"])
    try:
        end = start.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    except ValueError:
        raise TempoError("bad time: %r" % text)
    return end + timedelta(days=1) if end < start else end


def _check_new_end(state, session, end, now):
    start = _ts(session["start"])
    if end < start:
        raise TempoError("end time is before the session started (%s)" % session["start"])
    if end > now:
        raise TempoError("end time is in the future")
    for b in session["breaks"]:
        if b["end"] and _ts(b["end"]) > end:
            raise TempoError("end time is before a break that ended at %s" % b["end"])
    later = [
        _ts(other["start"])
        for t in state["tasks"]
        for other in t["sessions"]
        if other is not session and _ts(other["start"]) > start
    ]
    if later and end > min(later):
        raise TempoError(
            "end time overlaps the next session, which starts at %s" % min(later).isoformat()
        )


def reconcile_session(state, task_id, session_id, now, end=None, accept=False, discard=False):
    """Resolve one unknown or inferred session. Returns (task, action)."""
    if sum([end is not None, accept, discard]) != 1:
        raise TempoError("choose exactly one of --end, --accept, --discard")
    task = find_task(state, task_id)
    session = next((s for s in task["sessions"] if s["id"] == session_id), None)
    if session is None:
        raise TempoError("task %s has no session %s" % (task["id"], session_id))
    if session["endState"] not in (UNKNOWN, INFERRED):
        raise TempoError(
            "session %s is %s; only unknown and inferred sessions are reconciled"
            % (session_id, session["endState"])
        )
    if discard:
        task["sessions"].remove(session)
        return task, "discarded"
    if end is not None:
        _check_new_end(state, session, end, now)
        session["end"] = _iso(end)
    session["endState"] = "confirmed"
    return task, "set" if end is not None else "accepted"
