"""Tests for the tempo core. Fixed clocks, temporary TEMPO_HOME, no real time passing."""

import contextlib
import fcntl
import io
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import doctor
import nudges
import store
import tasks
import tempo
import timespec

TZ = timezone(timedelta(hours=2))
T0 = datetime(2026, 10, 7, 9, 0, 0, tzinfo=TZ)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


class TestTimespec(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(timespec.parse_minutes("45"), 45)
        self.assertEqual(timespec.parse_minutes("90m"), 90)
        self.assertEqual(timespec.parse_minutes("1h30m"), 90)

    def test_seconds_round_up(self):
        self.assertEqual(timespec.parse_minutes("30s"), 1)
        self.assertEqual(timespec.parse_minutes("61s"), 2)

    def test_rejects_garbage_and_zero(self):
        for bad in ["1m30", "10x", "", "0", "abc"]:
            with self.subTest(bad=bad):
                with self.assertRaises(timespec.BadDuration):
                    timespec.parse_minutes(bad)

    def test_format(self):
        self.assertEqual(timespec.format_minutes(0), "0m")
        self.assertEqual(timespec.format_minutes(45), "45m")
        self.assertEqual(timespec.format_minutes(60), "1h")
        self.assertEqual(timespec.format_minutes(90), "1h30m")


class TestParseWhen(unittest.TestCase):
    def test_forms(self):
        now = at(120)  # 11:00
        self.assertEqual(tasks.parse_when("unknown", now), tasks.UNKNOWN)
        self.assertEqual(tasks.parse_when("10:15", now), at(75))
        self.assertEqual(tasks.parse_when("2026-10-07T09:30:00+02:00", now), at(30))

    def test_naive_iso_is_local(self):
        self.assertEqual(tasks.parse_when("2026-10-07T09:30:00", at(120)), at(30))

    def test_bad(self):
        with self.assertRaises(tasks.TempoError):
            tasks.parse_when("half past", at(0))


def _state_with_task(estimate=60):
    state = store.new_state()
    task = tasks.add_task(state, "write doc", estimate, "", T0)
    return state, task


class TestSessions(unittest.TestCase):
    def test_start_stop_actual(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        self.assertEqual(task["status"], "active")
        tasks.stop_active(state, at(25))
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["sessions"][0]["endState"], "confirmed")
        self.assertEqual(tasks.actual_seconds(task, at(99)), 25 * 60)

    def test_breaks_are_subtracted(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        tasks.start_break(state, at(10))
        tasks.end_break(state, at(15))
        tasks.stop_active(state, at(30))
        self.assertEqual(tasks.actual_seconds(task, at(99)), 25 * 60)

    def test_stop_during_break_closes_the_break(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        tasks.start_break(state, at(10))
        tasks.stop_active(state, at(20))
        b = task["sessions"][0]["breaks"][0]
        self.assertEqual(b["end"], at(20).isoformat())
        self.assertEqual(tasks.actual_seconds(task, at(99)), 10 * 60)

    def test_open_session_counts_up_to_now(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        self.assertEqual(tasks.actual_seconds(task, at(7)), 7 * 60)

    def test_actual_spans_sessions(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        tasks.stop_active(state, at(10))
        tasks.start_task(state, task["id"], at(60))
        tasks.stop_active(state, at(75))
        self.assertEqual(tasks.actual_seconds(task, at(99)), 25 * 60)
        self.assertEqual([s["id"] for s in task["sessions"]], ["s1", "s2"])

    def test_starting_another_task_auto_stops_the_first(self):
        state, a = _state_with_task()
        b = tasks.add_task(state, "other", 30, "", T0)
        tasks.start_task(state, a["id"], at(0))
        task, stopped = tasks.start_task(state, b["id"], at(20))
        self.assertIs(stopped, a)
        self.assertEqual(a["status"], "todo")
        self.assertEqual(b["status"], "active")
        self.assertEqual(a["sessions"][0]["end"], at(20).isoformat())

    def test_auto_stop_with_given_end_time(self):
        state, a = _state_with_task()
        b = tasks.add_task(state, "other", 30, "", T0)
        tasks.start_task(state, a["id"], at(0))
        tasks.start_task(state, b["id"], at(20), prev_end=at(15))
        self.assertEqual(a["sessions"][0]["end"], at(15).isoformat())
        self.assertEqual(a["sessions"][0]["endState"], "confirmed")

    def test_auto_stop_unknown_uses_last_confirmed(self):
        state, a = _state_with_task()
        b = tasks.add_task(state, "other", 30, "", T0)
        tasks.start_task(state, a["id"], at(0))
        a["sessions"][0]["lastConfirmedAt"] = at(12).isoformat()
        tasks.start_task(state, b["id"], at(40), prev_end=tasks.UNKNOWN)
        self.assertEqual(a["sessions"][0]["end"], at(12).isoformat())
        self.assertEqual(a["sessions"][0]["endState"], "unknown")

    def test_end_time_must_fall_inside_the_session(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(10))
        with self.assertRaises(tasks.TempoError):
            tasks.stop_active(state, at(30), at=at(5))  # before the start
        with self.assertRaises(tasks.TempoError):
            tasks.stop_active(state, at(30), at=at(45))  # in the future
        self.assertEqual(task["status"], "active")  # failed stops change nothing

    def test_guards(self):
        state, task = _state_with_task()
        with self.assertRaises(tasks.TempoError):
            tasks.stop_active(state, at(0))
        with self.assertRaises(tasks.TempoError):
            tasks.start_break(state, at(0))
        tasks.start_task(state, task["id"], at(0))
        with self.assertRaises(tasks.TempoError):
            tasks.start_task(state, task["id"], at(1))  # already active
        with self.assertRaises(tasks.TempoError):
            tasks.end_break(state, at(1))  # not on a break
        tasks.start_break(state, at(2))
        with self.assertRaises(tasks.TempoError):
            tasks.start_break(state, at(3))  # already on one
        with self.assertRaises(tasks.TempoError):
            tasks.start_task(state, "nope", at(4))
        with self.assertRaises(tasks.TempoError):
            tasks.start_task(state, task["id"], at(4), prev_end=at(3))

    def test_prefix_ids(self):
        state, task = _state_with_task()
        self.assertIs(tasks.find_task(state, task["id"][:3]), task)
        state["tasks"].append(dict(task, id=task["id"][:2] + "zzzz"))
        with self.assertRaises(tasks.TempoError):
            tasks.find_task(state, task["id"][:2])


class TestFrictionAndDone(unittest.TestCase):
    def test_friction_on_active_or_named(self):
        state, task = _state_with_task()
        with self.assertRaises(tasks.TempoError):
            tasks.add_friction(state, "slow build", at(1))  # nothing active
        tasks.add_friction(state, "after the fact", at(1), task["id"])
        tasks.start_task(state, task["id"], at(2))
        tasks.add_friction(state, "slow build", at(3))
        self.assertEqual([f["text"] for f in task["friction"]], ["after the fact", "slow build"])

    def test_friction_after_the_task_is_done(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        tasks.complete_task(state, None, at(30))
        tasks.add_friction(state, "review took a day", at(600), task["id"])
        self.assertEqual(task["friction"][0]["text"], "review took a day")
        self.assertEqual(task["status"], "done")

    def test_done_stops_the_session(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        tasks.complete_task(state, None, at(30))
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["doneAt"], at(30).isoformat())
        self.assertEqual(task["sessions"][0]["end"], at(30).isoformat())

    def test_done_twice_needs_a_reason_to_do_anything(self):
        state, task = _state_with_task()
        tasks.complete_task(state, task["id"], at(0))
        with self.assertRaises(tasks.TempoError):
            tasks.complete_task(state, task["id"], at(1))
        tasks.complete_task(state, task["id"], at(1), reason="scope grew")
        self.assertEqual(task["overrunReason"], "scope grew")
        self.assertEqual(task["doneAt"], at(0).isoformat())  # unchanged

    def test_done_task_cannot_restart(self):
        state, task = _state_with_task()
        tasks.complete_task(state, task["id"], at(0))
        with self.assertRaises(tasks.TempoError):
            tasks.start_task(state, task["id"], at(1))

    def test_several_active_tasks_is_an_error(self):
        state, a = _state_with_task()
        b = tasks.add_task(state, "other", 30, "", T0)
        a["status"] = b["status"] = "active"
        with self.assertRaises(tasks.TempoError):
            tasks.active_task(state)


class FakeNudge:
    """Stands in for the nudge CLI: same commands, same output shape, in memory."""

    def __init__(self, lock_path):
        self.lock_path = lock_path
        self.jobs = {}  # id -> (seconds, message)
        self.calls = []
        self.count = 0
        self.down = False
        self.daemon_ok = True
        self.lock_violations = 0

    def _check_lock(self):
        """Nudge must never be called while the tasks.json lock is held."""
        if not os.path.exists(self.lock_path):
            return
        fd = os.open(self.lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_violations += 1
        finally:
            os.close(fd)

    def __call__(self, args, timeout=10):
        if self.down:
            raise nudges.NudgeError("could not run nudge: boom")
        self._check_lock()
        self.calls.append(args)
        cmd = args[0]
        if cmd == "in":
            self.count += 1
            job_id = "j%d" % self.count
            self.jobs[job_id] = (int(args[1].rstrip("s")), args[2])
            return 0, "%s  in 1m  %r\n" % (job_id, args[2]), ""
        if cmd == "cancel":
            if args[1] in self.jobs:
                del self.jobs[args[1]]
                return 0, "cancelled %s\n" % args[1], ""
            return 1, "", "nudge: no such job: %s\n" % args[1]
        if cmd == "list":
            if not self.jobs:
                return 0, "(empty)\n", ""
            return 0, "".join("%s  in 5m  'x'\n" % j for j in self.jobs), ""
        if cmd == "status":
            return (0, "daemon  loaded\n", "") if self.daemon_ok else (1, "daemon  not loaded\n", "")
        raise AssertionError(args)


class _HomeCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.home = os.path.join(self._dir.name, "tempo")
        patcher = mock.patch.dict(os.environ, {"TEMPO_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)
        # never touch the real nudge queue
        self.nudge = FakeNudge(self.path("tasks.lock"))
        patcher = mock.patch.object(nudges, "_run", self.nudge)
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, name):
        return os.path.join(self.home, name)


class TestStore(_HomeCase):
    def test_missing_file_is_empty(self):
        self.assertEqual(store.read(), store.new_state())

    def test_transaction_saves_and_keeps_a_backup(self):
        with store.transaction() as state:
            tasks.add_task(state, "one", 10, "", T0)
        with store.transaction() as state:
            tasks.add_task(state, "two", 10, "", T0)
        self.assertEqual(len(store.read()["tasks"]), 2)
        with open(self.path("tasks.json.bak")) as f:
            self.assertEqual(len(json.load(f)["tasks"]), 1)

    def test_failed_transaction_saves_nothing(self):
        with store.transaction() as state:
            tasks.add_task(state, "one", 10, "", T0)
        with self.assertRaises(RuntimeError):
            with store.transaction() as state:
                tasks.add_task(state, "two", 10, "", T0)
                raise RuntimeError
        self.assertEqual(len(store.read()["tasks"]), 1)

    def test_unreadable_file_is_set_aside_and_tracking_continues(self):
        os.makedirs(self.home)
        with open(self.path("tasks.json"), "w") as f:
            f.write('{"schemaVersion": 1, "tasks": [')  # truncated
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with store.transaction() as state:
                tasks.add_task(state, "fresh", 10, "", T0)
        self.assertIn("set it aside", err.getvalue())
        self.assertEqual([t["name"] for t in store.read()["tasks"]], ["fresh"])
        aside = [n for n in os.listdir(self.home) if ".corrupt-" in n]
        self.assertEqual(len(aside), 1)
        with open(self.path(aside[0])) as f:
            self.assertIn("[", f.read())  # the damaged bytes are preserved

    def test_wrong_shape_counts_as_unreadable(self):
        os.makedirs(self.home)
        with open(self.path("tasks.json"), "w") as f:
            json.dump({"hello": "world"}, f)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(store.read(), store.new_state())

    def test_newer_schema_is_refused_and_untouched(self):
        os.makedirs(self.home)
        with open(self.path("tasks.json"), "w") as f:
            json.dump({"schemaVersion": 99, "tasks": []}, f)
        with self.assertRaises(store.StoreError):
            store.read()
        self.assertEqual(os.listdir(self.home).count("tasks.json"), 1)
        self.assertFalse([n for n in os.listdir(self.home) if ".corrupt-" in n])


class TestRecoveryPrimitives(unittest.TestCase):
    def test_inferred_closes_at_last_confirmed(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        task["sessions"][0]["lastConfirmedAt"] = at(20).isoformat()
        tasks.stop_active(state, at(200), at=tasks.INFERRED)
        s = task["sessions"][0]
        self.assertEqual((s["end"], s["endState"]), (at(20).isoformat(), "inferred"))
        self.assertEqual(task["status"], "todo")

    def test_interactions_refresh_last_confirmed(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        last = lambda: task["sessions"][0]["lastConfirmedAt"]
        tasks.start_break(state, at(5))
        self.assertEqual(last(), at(5).isoformat())
        tasks.end_break(state, at(9))
        self.assertEqual(last(), at(9).isoformat())
        tasks.add_friction(state, "slow", at(12))  # friction is not presence
        self.assertEqual(last(), at(9).isoformat())
        tasks.confirm(state, at(30))
        self.assertEqual(last(), at(30).isoformat())

    def test_inferred_while_on_a_break_never_ends_before_the_break_started(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        tasks.start_break(state, at(10))  # also confirms at 10
        tasks.stop_active(state, at(300), at=tasks.INFERRED)
        self.assertEqual(tasks.actual_seconds(task, at(400)), 10 * 60)

    def test_stop_named_task_even_with_several_active(self):
        state, a = _state_with_task()
        b = tasks.add_task(state, "other", 30, "", T0)
        for t in (a, b):
            tasks.start_task(state, t["id"], at(0)) if t is a else None
        # fabricate the broken state: b active too, with its own open session
        b["status"] = "active"
        b["sessions"].append(dict(a["sessions"][0], id="s1"))
        with self.assertRaises(tasks.TempoError):
            tasks.stop_active(state, at(5))  # ambiguous without --task
        tasks.stop_active(state, at(5), at=tasks.INFERRED, task_id=b["id"])
        self.assertEqual(b["status"], "todo")
        self.assertEqual(a["status"], "active")

    def test_stop_repairs_active_without_session(self):
        state, task = _state_with_task()
        task["status"] = "active"
        tasks.stop_active(state, at(0), task_id=task["id"])
        self.assertEqual(task["status"], "todo")

    def test_stop_closing_a_done_task_session_keeps_it_done(self):
        state, task = _state_with_task()
        tasks.start_task(state, task["id"], at(0))
        task["status"] = "done"  # open session on a done task
        tasks.stop_active(state, at(5), task_id=task["id"])
        self.assertEqual(task["status"], "done")

    def test_nothing_to_stop(self):
        state, task = _state_with_task()
        with self.assertRaises(tasks.TempoError):
            tasks.stop_active(state, at(0), task_id=task["id"])

    def test_nudge_interval(self):
        self.assertIsNone(tasks.nudge_interval_min(20, 5))
        self.assertIsNone(tasks.nudge_interval_min(20, 90))
        self.assertEqual(tasks.nudge_interval_min(30, 5), 30)
        self.assertEqual(tasks.nudge_interval_min(90, 10), 30)
        self.assertEqual(tasks.nudge_interval_min(90, 90), 15)


class TestDoctor(unittest.TestCase):
    def codes(self, state, now, aside=()):
        return [f["code"] for f in doctor.check(state, now, aside)]

    def active_state(self, estimate=90):
        state, task = _state_with_task(estimate)
        tasks.start_task(state, task["id"], at(0))
        return state, task

    def test_clean(self):
        state, _ = self.active_state()
        self.assertEqual(doctor.check(state, at(10)), [])
        self.assertEqual(doctor.check(store.new_state(), at(10)), [])

    def test_zombie_after_twice_the_nudge_interval(self):
        state, task = self.active_state(90)  # nudged every 30m -> limit 60m
        self.assertEqual(self.codes(state, at(59)), [])
        self.assertEqual(self.codes(state, at(61)), ["zombie"])
        finding = doctor.check(state, at(61))[0]
        self.assertIn("tempo stop --task %s --at inferred" % task["id"], finding["fixes"][0])

    def test_confirming_clears_the_zombie(self):
        state, _ = self.active_state(90)
        tasks.confirm(state, at(55))
        self.assertEqual(self.codes(state, at(80)), [])

    def test_overrun_tightens_the_limit(self):
        state, _ = self.active_state(60)
        tasks.confirm(state, at(50))
        # actual (65m) is past the estimate, nudges are now every 15m -> limit 30m
        self.assertEqual(self.codes(state, at(65)), [])
        self.assertEqual(self.codes(state, at(85)), ["zombie"])

    def test_short_tasks_use_the_flat_grace(self):
        state, _ = self.active_state(20)
        self.assertEqual(self.codes(state, at(59)), [])
        self.assertEqual(self.codes(state, at(61)), ["zombie"])

    def test_forgotten_break_is_a_zombie_after_the_break_grace(self):
        state, _ = self.active_state(90)
        tasks.start_break(state, at(10))
        self.assertEqual(self.codes(state, at(99)), [])
        self.assertEqual(self.codes(state, at(101)), ["zombie"])

    def test_multiple_active_is_an_error_keeping_the_newest(self):
        state, a = self.active_state()
        b = tasks.add_task(state, "other", 30, "", T0)
        b["status"] = "active"
        b["sessions"].append(dict(a["sessions"][0], id="s1", start=at(30).isoformat()))
        found = doctor.check(state, at(31))
        self.assertEqual(found[0]["code"], "multiple_active")
        self.assertEqual(found[0]["severity"], doctor.ERROR)
        self.assertEqual(found[0]["fixes"], ["tempo stop --task %s --at inferred" % a["id"]])

    def test_status_mismatch(self):
        state, task = self.active_state()
        task["status"] = "todo"  # open session, not active
        self.assertIn("status_mismatch", self.codes(state, at(1)))
        task["status"] = "active"
        task["sessions"][0]["end"] = at(5).isoformat()  # active, no open session
        self.assertIn("status_mismatch", self.codes(state, at(6)))

    def test_bad_times(self):
        state, task = self.active_state()
        s = task["sessions"][0]
        s["end"] = at(-5).isoformat()
        s["endState"] = "confirmed"
        task["status"] = "todo"
        self.assertEqual(self.codes(state, at(1)), ["bad_times"])
        s["end"] = at(10).isoformat()
        s["breaks"] = [{"start": at(20).isoformat(), "end": at(25).isoformat()}]
        self.assertEqual(self.codes(state, at(30)), ["bad_times"])
        s["breaks"] = [{"start": "garbage", "end": None}]
        self.assertEqual(self.codes(state, at(30)), ["bad_times"])

    def test_set_aside_files_are_listed(self):
        found = doctor.check(store.new_state(), at(0), ["tasks.json.corrupt-20261007T090000"])
        self.assertEqual([f["code"] for f in found], ["set_aside_file"])

    def test_errors_sort_first(self):
        state, a = self.active_state()
        b = tasks.add_task(state, "other", 30, "", T0)
        b["status"] = "active"
        b["sessions"].append(dict(a["sessions"][0], id="s1"))
        found = doctor.check(state, at(500), ["tasks.json.corrupt-x"])
        self.assertEqual(found[0]["severity"], doctor.ERROR)
        self.assertEqual(found[-1]["severity"], doctor.WARN)


class TestNudgeMarks(unittest.TestCase):
    def test_short_tasks_get_none(self):
        self.assertEqual(tasks.nudge_marks(20, 0), [])
        self.assertEqual(tasks.nudge_marks(29, 10), [])

    def test_thirty_minute_task_has_a_midpoint(self):
        self.assertEqual(tasks.nudge_marks(30, 0), [15, 30, 45, 60, 75, 90])

    def test_longer_tasks_every_thirty_then_every_fifteen_after_the_estimate(self):
        self.assertEqual(tasks.nudge_marks(90, 0), [30, 60, 90, 105, 120, 135])
        self.assertEqual(tasks.nudge_marks(45, 0), [30, 45, 60, 75, 90, 105])

    def test_marks_are_cumulative_and_strictly_ahead(self):
        self.assertEqual(tasks.nudge_marks(90, 40)[:2], [60, 90])
        self.assertEqual(tasks.nudge_marks(90, 30)[0], 60)  # not the mark we are on
        self.assertEqual(tasks.nudge_marks(90, 95)[:2], [105, 120])


class TestNudgeAdapter(unittest.TestCase):
    def test_find_prefers_the_path(self):
        with mock.patch("nudges.shutil.which", return_value="/opt/nudge"):
            self.assertEqual(nudges.find(), "/opt/nudge")

    def test_find_falls_back_to_the_shim_off_the_path(self):
        with tempfile.TemporaryDirectory() as d:
            shim = os.path.join(d, "nudge")
            with mock.patch("nudges.shutil.which", return_value=None), \
                    mock.patch.object(nudges, "FALLBACK", shim):
                self.assertIsNone(nudges.find())
                with open(shim, "w") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(shim, 0o755)
                self.assertEqual(nudges.find(), shim)

    def test_schedule_returns_the_job_id(self):
        with mock.patch.object(nudges, "_run", return_value=(0, "3f1a9c  in 15m  'x'\n", "")) as run:
            self.assertEqual(nudges.schedule(900, "x"), "3f1a9c")
        run.assert_called_once_with(["in", "900s", "x", "--title", "tempo"])

    def test_schedule_failure(self):
        with mock.patch.object(nudges, "_run", return_value=(1, "", "nudge: bad duration\n")):
            with self.assertRaises(nudges.NudgeError):
                nudges.schedule(900, "x")

    def test_cancel_treats_a_missing_job_as_done(self):
        with mock.patch.object(nudges, "_run", return_value=(1, "", "nudge: no such job: x\n")):
            nudges.cancel("x")
        with mock.patch.object(nudges, "_run", return_value=(1, "", "disk on fire\n")):
            with self.assertRaises(nudges.NudgeError):
                nudges.cancel("x")

    def test_pending_ids(self):
        out = "3f1a9c  in 5m  'a b'\n77aa11  in 1h  'c'\n"
        with mock.patch.object(nudges, "_run", return_value=(0, out, "")):
            self.assertEqual(nudges.pending_ids(), {"3f1a9c", "77aa11"})
        with mock.patch.object(nudges, "_run", return_value=(0, "(empty)\n", "")):
            self.assertEqual(nudges.pending_ids(), set())

    def test_missing_binary(self):
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("nudges.shutil.which", return_value=None), \
                mock.patch.object(nudges, "FALLBACK", "/nonexistent/nudge"):
            os.environ.pop("TEMPO_NUDGE", None)
            with self.assertRaises(nudges.NudgeError):
                nudges._run(["list"])


class TestDoctorNudges(unittest.TestCase):
    def state_with_nudges(self, status_active=True):
        state, task = _state_with_task(90)
        tasks.start_task(state, task["id"], at(0))
        task["sessions"][0]["nudgeIds"] = ["j1", "j2"]
        if not status_active:
            tasks.stop_active(state, at(5))
        return state

    def health(self, **kw):
        base = {"available": True, "pending": set(), "daemon_ok": True, "detail": "ok"}
        base.update(kw)
        return base

    def codes(self, state, nudge):
        return [f["code"] for f in doctor.check(state, at(10), nudge=nudge)]

    def test_live_nudges_are_fine(self):
        state = self.state_with_nudges()
        self.assertEqual(self.codes(state, self.health(pending={"j1", "j2"})), [])

    def test_pending_nudge_on_a_stopped_session_is_an_orphan(self):
        state = self.state_with_nudges(status_active=False)
        found = doctor.check(state, at(10), nudge=self.health(pending={"j1"}))
        self.assertEqual([f["code"] for f in found], ["orphaned_nudge"])
        self.assertEqual(found[0]["fixes"], ["nudge cancel j1"])

    def test_recorded_but_already_gone_is_not_an_orphan(self):
        state = self.state_with_nudges(status_active=False)
        self.assertEqual(self.codes(state, self.health(pending=set())), [])

    def test_daemon_down(self):
        state = self.state_with_nudges()
        found = self.codes(state, self.health(pending={"j1", "j2"}, daemon_ok=False))
        self.assertEqual(found, ["nudge_daemon"])

    def test_nudge_unavailable(self):
        found = doctor.check(store.new_state(), at(0), nudge={"available": False, "error": "nudge not found"})
        self.assertEqual([f["code"] for f in found], ["nudge_unavailable"])

    def test_skipped_without_health(self):
        self.assertEqual(doctor.check(self.state_with_nudges(False), at(10)), [])


class TestLock(_HomeCase):
    def _hold(self, pid_text):
        """Take the lock on a separate file description, as another process would."""
        os.makedirs(self.home)
        fd = os.open(self.path("tasks.lock"), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, pid_text.encode())
        self.addCleanup(os.close, fd)
        return fd

    def test_held_lock_times_out_naming_the_holder(self):
        self._hold("4242\n")
        with mock.patch.dict(os.environ, {"TEMPO_LOCK_TIMEOUT": "0.2"}):
            with self.assertRaises(store.StoreError) as cm:
                store.read()
        self.assertIn("pid 4242", str(cm.exception))

    def test_cli_reports_the_timeout(self):
        self._hold("4242\n")
        with mock.patch.dict(os.environ, {"TEMPO_LOCK_TIMEOUT": "0.2"}):
            with self.assertRaises(SystemExit) as cm:
                tempo.main(["list"])
        self.assertIn("pid 4242", str(cm.exception))

    def test_waits_for_a_lock_that_is_released_in_time(self):
        fd = self._hold("4242\n")
        threading.Timer(0.2, lambda: fcntl.flock(fd, fcntl.LOCK_UN)).start()
        with mock.patch.dict(os.environ, {"TEMPO_LOCK_TIMEOUT": "3"}):
            self.assertEqual(store.read(), store.new_state())

    def test_holder_records_its_pid(self):
        with store.transaction():
            with open(self.path("tasks.lock")) as f:
                self.assertEqual(f.read().strip(), str(os.getpid()))


class TestCli(_HomeCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tempo.main(list(argv))
        return out.getvalue()

    def first_id(self):
        return store.read()["tasks"][0]["id"]

    def run_status(self, *argv):
        """Run a command that may exit; return (exit code, stdout)."""
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                tempo.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_doctor_exit_codes(self):
        self.assertEqual(self.run_status("doctor"), (0, "ok\n"))
        self.run_cli("add", "one", "-e", "90")
        task_id = self.first_id()
        self.run_cli("start", task_id)
        with store.transaction() as state:  # last confirmed two hours ago
            old = (tasks.now_local() - timedelta(hours=2)).isoformat()
            state["tasks"][0]["sessions"][0]["lastConfirmedAt"] = old
        code, out = self.run_status("doctor")
        self.assertEqual(code, 1)
        self.assertIn("zombie", out)
        self.assertIn("fix: tempo stop --task %s --at inferred" % task_id, out)
        self.run_cli("confirm")
        self.assertEqual(self.run_status("doctor")[0], 0)
        with store.transaction() as state:
            state["tasks"][0]["status"] = "todo"
        self.assertEqual(self.run_status("doctor")[0], 2)

    def test_the_printed_fix_actually_works(self):
        self.run_cli("add", "one", "-e", "90")
        task_id = self.first_id()
        self.run_cli("start", task_id)
        with store.transaction() as state:
            session = state["tasks"][0]["sessions"][0]
            session["start"] = (tasks.now_local() - timedelta(hours=3)).isoformat()
            session["lastConfirmedAt"] = (tasks.now_local() - timedelta(hours=2)).isoformat()
        self.run_cli("stop", "--task", task_id, "--at", "inferred")
        task = store.read()["tasks"][0]
        self.assertEqual(task["sessions"][0]["endState"], "inferred")
        self.assertEqual(self.run_status("doctor")[0], 0)

    def test_doctor_mentions_set_aside_files(self):
        os.makedirs(self.home)
        with open(self.path("tasks.json"), "w") as f:
            f.write("not json")
        with contextlib.redirect_stderr(io.StringIO()):
            code, out = self.run_status("doctor")
        self.assertEqual(code, 1)
        self.assertIn("set_aside_file", out)

    def test_stop_for_a_repaired_task_says_so(self):
        self.run_cli("add", "one", "-e", "10")
        with store.transaction() as state:
            state["tasks"][0]["status"] = "active"
        self.assertIn("reset", self.run_cli("stop", "--task", self.first_id()))

    def session_ids(self, index=0):
        return store.read()["tasks"][index]["sessions"][-1]["nudgeIds"]

    def test_start_schedules_the_next_marks(self):
        self.run_cli("add", "write", "doc", "-e", "90")
        out = self.run_cli("start", self.first_id())
        self.assertIn("nudges: 6 scheduled, next in 30m", out)
        delays = [sec for sec, _ in self.nudge.jobs.values()]
        self.assertEqual(delays, [1800, 3600, 5400, 6300, 7200, 8100])
        self.assertEqual(sorted(self.session_ids()), sorted(self.nudge.jobs))
        message = next(iter(self.nudge.jobs.values()))[1]
        self.assertIn("write doc", message)
        self.assertIn("tempo confirm", message)

    def test_short_tasks_schedule_nothing(self):
        self.run_cli("add", "quick", "-e", "20")
        self.run_cli("start", self.first_id())
        self.assertEqual(self.nudge.jobs, {})
        self.assertEqual(self.nudge.calls, [])

    def test_thirty_minute_task_gets_its_midpoint(self):
        self.run_cli("add", "half", "-e", "30")
        self.run_cli("start", self.first_id())
        self.assertEqual(min(sec for sec, _ in self.nudge.jobs.values()), 900)

    def test_stop_cancels_and_clears(self):
        self.run_cli("add", "one", "-e", "90")
        self.run_cli("start", self.first_id())
        self.run_cli("stop")
        self.assertEqual(self.nudge.jobs, {})
        self.assertEqual(self.session_ids(), [])

    def test_break_cancels_and_resume_reschedules(self):
        self.run_cli("add", "one", "-e", "90")
        self.run_cli("start", self.first_id())
        self.run_cli("break")
        self.assertEqual(self.nudge.jobs, {})
        out = self.run_cli("resume")
        self.assertIn("6 scheduled", out)
        self.assertEqual(len(self.session_ids()), 6)

    def test_confirm_replaces_the_schedule(self):
        self.run_cli("add", "one", "-e", "90")
        self.run_cli("start", self.first_id())
        before = set(self.session_ids())
        self.run_cli("confirm")
        after = set(self.session_ids())
        self.assertEqual(len(after), 6)
        self.assertFalse(before & after)
        self.assertEqual(set(self.nudge.jobs), after)  # the old ones are really gone

    def test_confirm_replans_from_cumulative_actual_time(self):
        self.run_cli("add", "one", "-e", "90")
        self.run_cli("start", self.first_id())
        with store.transaction() as state:  # pretend 40 minutes have been worked
            session = state["tasks"][0]["sessions"][0]
            session["start"] = (tasks.now_local() - timedelta(minutes=40)).isoformat()
        self.run_cli("confirm")
        first = min(sec for sec, _ in self.nudge.jobs.values())
        self.assertAlmostEqual(first, 20 * 60, delta=5)  # the 60-minute mark

    def test_switching_tasks_moves_the_nudges(self):
        self.run_cli("add", "a", "-e", "90")
        self.run_cli("add", "b", "-e", "60")
        a, b = [t["id"] for t in store.read()["tasks"]]
        self.run_cli("start", a)
        self.run_cli("start", b)
        state = store.read()
        self.assertEqual(state["tasks"][0]["sessions"][-1]["nudgeIds"], [])
        self.assertEqual(len(state["tasks"][1]["sessions"][-1]["nudgeIds"]), 6)
        self.assertEqual(len(self.nudge.jobs), 6)

    def test_done_cancels(self):
        self.run_cli("add", "one", "-e", "90")
        self.run_cli("start", self.first_id())
        self.run_cli("done")
        self.assertEqual(self.nudge.jobs, {})

    def test_nudge_is_never_called_while_the_lock_is_held(self):
        self.run_cli("add", "a", "-e", "90")
        self.run_cli("add", "b", "-e", "90")
        a, b = [t["id"] for t in store.read()["tasks"]]
        for argv in (["start", a], ["break"], ["resume"], ["confirm"], ["start", b],
                     ["stop"], ["start", a], ["done"], ["doctor"]):
            try:
                self.run_cli(*argv)
            except SystemExit:
                pass
        self.assertGreater(len(self.nudge.calls), 10)
        self.assertEqual(self.nudge.lock_violations, 0)

    def test_nudge_down_never_blocks_tracking(self):
        self.nudge.down = True
        self.run_cli("add", "one", "-e", "90")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.run_cli("start", self.first_id())
        self.assertIn("nudge", err.getvalue())
        self.assertEqual(store.read()["tasks"][0]["status"], "active")
        self.assertEqual(self.session_ids(), [])

    def test_failed_cancel_leaves_the_ids_for_doctor(self):
        self.run_cli("add", "one", "-e", "90")
        self.run_cli("start", self.first_id())
        ids = list(self.session_ids())
        self.nudge.down = True
        with contextlib.redirect_stderr(io.StringIO()):
            self.run_cli("stop")
        self.assertEqual(self.session_ids(), ids)  # still recorded
        self.nudge.down = False
        code, out = self.run_status("doctor")
        self.assertEqual(code, 1)
        self.assertIn("orphaned_nudge", out)
        self.assertIn("fix: nudge cancel %s" % ids[0], out)

    def test_doctor_reports_a_dead_daemon(self):
        self.nudge.daemon_ok = False
        code, out = self.run_status("doctor")
        self.assertEqual(code, 1)
        self.assertIn("nudge_daemon", out)

    def test_happy_path(self):
        self.assertIn("est 1h30m", self.run_cli("add", "write", "doc", "-e", "1h30m"))
        task_id = self.first_id()
        self.assertIn("started", self.run_cli("start", task_id))
        self.run_cli("break")
        self.run_cli("resume")
        self.run_cli("friction", "slow", "review")
        self.assertIn("stopped", self.run_cli("stop"))
        self.assertIn("done", self.run_cli("done", task_id))
        task = store.read()["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["friction"][0]["text"], "slow review")

    def test_list_hides_done_unless_asked(self):
        self.run_cli("add", "one", "-e", "10")
        self.run_cli("done", self.first_id())
        self.assertIn("(empty)", self.run_cli("list"))
        self.assertIn("one", self.run_cli("list", "--all"))

    def test_errors_exit_with_a_message(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("stop")
        self.assertIn("nothing is active", str(cm.exception))
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("add", "x", "-e", "1m30")
        self.assertIn("bad duration", str(cm.exception))

    def test_overrun_prompts_for_a_reason(self):
        self.run_cli("add", "quick", "-e", "1")
        task_id = self.first_id()
        self.run_cli("start", task_id)
        start = tasks.now_local() - timedelta(minutes=5)
        with store.transaction() as state:
            state["tasks"][0]["sessions"][0]["start"] = start.isoformat()
        out = self.run_cli("done", task_id)
        self.assertIn("over by", out)
        self.assertIn("--reason", out)
        self.run_cli("done", task_id, "--reason", "scope grew")
        self.assertEqual(store.read()["tasks"][0]["overrunReason"], "scope grew")


if __name__ == "__main__":
    unittest.main()
