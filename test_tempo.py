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


class _HomeCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.home = os.path.join(self._dir.name, "tempo")
        patcher = mock.patch.dict(os.environ, {"TEMPO_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._dir.cleanup)

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
