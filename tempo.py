#!/usr/bin/env python3
"""tempo -- track tasks, estimates and actual time.

    tempo add "write the doc" -e 1h30m
    tempo start <id>
    tempo break / resume
    tempo friction "waited on review"
    tempo stop
    tempo done --reason "scope grew"
    tempo list
"""

import argparse
import sys

import doctor
import store
import tasks
from timespec import BadDuration, format_minutes, parse_minutes


def _mins(seconds):
    return format_minutes(seconds / 60)


def _when(text, now):
    return tasks.parse_when(text, now) if text is not None else None


def _label(task):
    return "%s  %r" % (task["id"], task["name"])


def cmd_add(args):
    estimate = parse_minutes(args.estimate)
    now = tasks.now_local()
    with store.transaction() as state:
        task = tasks.add_task(state, " ".join(args.name), estimate, args.notes, now)
    print("%s  est %s" % (_label(task), format_minutes(estimate)))


def cmd_start(args):
    now = tasks.now_local()
    with store.transaction() as state:
        task, stopped = tasks.start_task(state, args.id, now, _when(args.prev_end, now))
    if stopped:
        session = stopped["sessions"][-1]
        print(
            "auto-stopped %s at %s (%s); back to todo"
            % (_label(stopped), session["end"], session["endState"])
        )
    print("started %s  est %s" % (_label(task), format_minutes(task["estimateMin"])))


def cmd_stop(args):
    now = tasks.now_local()
    with store.transaction() as state:
        task = tasks.stop_active(state, now, _when(args.at, now), args.task)
        session = task["sessions"][-1] if task["sessions"] else None
        if session is None or session["end"] is None:
            print("reset %s to todo" % _label(task))
            return
        this = tasks.session_seconds(session, now)
        total = tasks.actual_seconds(task, now)
    print(
        "stopped %s (%s): session %s, total %s of %s"
        % (_label(task), session["endState"], _mins(this), _mins(total),
           format_minutes(task["estimateMin"]))
    )


def cmd_confirm(args):
    now = tasks.now_local()
    with store.transaction() as state:
        task = tasks.confirm(state, now)
    print("confirmed %s at %s" % (_label(task), now.strftime("%H:%M")))


def cmd_doctor(args):
    findings = doctor.check(store.read(), tasks.now_local(), store.aside_files())
    if not findings:
        print("ok")
        return
    for f in findings:
        print("%-5s  %s  %s" % (f["severity"], f["code"], f["message"]))
        for fix in f["fixes"]:
            print("       fix: %s" % fix)
    sys.exit(2 if any(f["severity"] == doctor.ERROR for f in findings) else 1)


def cmd_break(args):
    now = tasks.now_local()
    with store.transaction() as state:
        task = tasks.start_break(state, now)
    print("break started on %s" % _label(task))


def cmd_resume(args):
    now = tasks.now_local()
    with store.transaction() as state:
        task = tasks.end_break(state, now)
    print("back on %s" % _label(task))


def cmd_friction(args):
    now = tasks.now_local()
    with store.transaction() as state:
        task = tasks.add_friction(state, " ".join(args.text), now, args.task)
    print("friction noted on %s" % _label(task))


def cmd_done(args):
    now = tasks.now_local()
    with store.transaction() as state:
        task = tasks.complete_task(state, args.id, now, args.reason)
        actual = tasks.actual_seconds(task, now)
    est = task["estimateMin"]
    line = "done %s: actual %s vs est %s" % (_label(task), _mins(actual), format_minutes(est))
    if actual > est * 60:
        line += " (over by %s)" % _mins(actual - est * 60)
        if not task["overrunReason"]:
            line += "; add a reason: tempo done %s --reason '...'" % task["id"]
    print(line)


def cmd_list(args):
    now = tasks.now_local()
    state = store.read()
    shown = [t for t in state["tasks"] if args.all or t["status"] != "done"]
    if not shown:
        print("(empty)")
        return
    for t in shown:
        print(
            "%s  %-6s  %-30s  est %-6s  actual %s"
            % (t["id"], t["status"], t["name"][:30], format_minutes(t["estimateMin"]),
               _mins(tasks.actual_seconds(t, now)))
        )


def build_parser():
    p = argparse.ArgumentParser(prog="tempo", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="create a task")
    s.add_argument("name", nargs="+")
    s.add_argument("-e", "--estimate", required=True, help="45, 90m, 1h30m")
    s.add_argument("-n", "--notes", default="")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("start", help="start a session; auto-stops the active task")
    s.add_argument("id")
    s.add_argument("--prev-end", help="when the previous task really ended: 14:30, ISO, 'inferred' or 'unknown'")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("stop", help="stop the active session; the task returns to todo")
    s.add_argument("--at", help="when it really ended: 14:30, ISO, 'inferred' or 'unknown'")
    s.add_argument("--task", help="stop this task's session instead of the active one")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("confirm", help="say you are still on the active task")
    s.set_defaults(func=cmd_confirm)

    s = sub.add_parser("doctor", help="check the data; exit 0 ok, 1 warnings, 2 errors")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("break", help="pause the clock")
    s.set_defaults(func=cmd_break)

    s = sub.add_parser("resume", help="end the break")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("friction", help="log a friction point")
    s.add_argument("text", nargs="+")
    s.add_argument("--task", help="task id (default: the active task)")
    s.set_defaults(func=cmd_friction)

    s = sub.add_parser("done", help="complete a task (default: the active one)")
    s.add_argument("id", nargs="?")
    s.add_argument("--reason", help="why it ran over the estimate")
    s.set_defaults(func=cmd_done)

    s = sub.add_parser("list", help="show tasks")
    s.add_argument("--all", action="store_true", help="include done tasks")
    s.set_defaults(func=cmd_list)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (tasks.TempoError, store.StoreError, BadDuration) as e:
        sys.exit("tempo: %s" % e)


if __name__ == "__main__":
    main()
