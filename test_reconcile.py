"""Tests for reconciling unknown and inferred sessions."""

import contextlib
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import nudges
import report
import store
import tasks
import tempo

TZ = timezone(timedelta(hours=2))
DAY0 = datetime(2026, 10, 7, 0, 0, tzinfo=TZ)


def at(hours, minutes=0, day=0):
    return DAY0 + timedelta(days=day, hours=hours, minutes=minutes)


def unresolved(state, name, start, end, end_state, estimate=60):
    """A task with one closed session of the given (unconfirmed) kind."""
    task = tasks.add_task(state, name, estimate, "", start)
    tasks.start_task(state, task["id"], start)
    tasks.stop_active(state, end)
    task["sessions"][-1]["endState"] = end_state
    return task


class TestRules(unittest.TestCase):
    def test_list_finds_unknown_and_inferred_only(self):
        state = store.new_state()
        unresolved(state, "u", at(9), at(10), "unknown")
        unresolved(state, "i", at(11), at(12), "inferred")
        unresolved(state, "ok", at(13), at(14), "confirmed")
        names = [t["name"] for t, _ in tasks.unresolved_sessions(state)]
        self.assertEqual(names, ["u", "i"])

    def test_set_the_real_end(self):
        state = store.new_state()
        t = unresolved(state, "u", at(9), at(9, 30), "unknown")
        _, action = tasks.reconcile_session(state, t["id"], "s1", at(20), end=at(10, 15))
        s = t["sessions"][0]
        self.assertEqual((action, s["endState"], s["end"]), ("set", "confirmed", at(10, 15).isoformat()))
        self.assertEqual(tasks.actual_seconds(t, at(20)), 75 * 60)

    def test_accept_keeps_the_recorded_end(self):
        state = store.new_state()
        t = unresolved(state, "i", at(9), at(9, 30), "inferred")
        _, action = tasks.reconcile_session(state, t["id"], "s1", at(20), accept=True)
        s = t["sessions"][0]
        self.assertEqual((action, s["endState"], s["end"]), ("accepted", "confirmed", at(9, 30).isoformat()))

    def test_discard_removes_the_time(self):
        state = store.new_state()
        t = unresolved(state, "u", at(9), at(10), "unknown")
        _, action = tasks.reconcile_session(state, t["id"], "s1", at(20), discard=True)
        self.assertEqual((action, t["sessions"]), ("discarded", []))
        self.assertEqual(tasks.actual_seconds(t, at(20)), 0)

    def test_exactly_one_action(self):
        state = store.new_state()
        t = unresolved(state, "u", at(9), at(10), "unknown")
        for kwargs in ({}, {"accept": True, "discard": True}, {"end": at(10), "accept": True}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(tasks.TempoError):
                    tasks.reconcile_session(state, t["id"], "s1", at(20), **kwargs)

    def test_only_unresolved_sessions_can_be_reconciled(self):
        state = store.new_state()
        t = unresolved(state, "ok", at(9), at(10), "confirmed")
        with self.assertRaises(tasks.TempoError):
            tasks.reconcile_session(state, t["id"], "s1", at(20), accept=True)
        with self.assertRaises(tasks.TempoError):
            tasks.reconcile_session(state, t["id"], "s9", at(20), accept=True)

    def test_the_new_end_must_make_sense(self):
        state = store.new_state()
        a = unresolved(state, "a", at(9), at(9, 30), "unknown")
        unresolved(state, "b", at(10), at(11), "confirmed")
        cases = {
            "before the start": at(8),
            "in the future": at(23),
            "overlapping the next session": at(10, 30),
        }
        for label, bad in cases.items():
            with self.subTest(label):
                with self.assertRaises(tasks.TempoError):
                    tasks.reconcile_session(state, a["id"], "s1", at(12), end=bad)
        tasks.reconcile_session(state, a["id"], "s1", at(12), end=at(10))  # up to the next start is fine

    def test_end_cannot_cut_into_a_break(self):
        state = store.new_state()
        t = tasks.add_task(state, "t", 60, "", at(9))
        tasks.start_task(state, t["id"], at(9))
        tasks.start_break(state, at(9, 10))
        tasks.end_break(state, at(9, 20))
        tasks.stop_active(state, at(9, 40), at=tasks.UNKNOWN)
        t["sessions"][0]["end"] = at(9, 40).isoformat()
        with self.assertRaises(tasks.TempoError):
            tasks.reconcile_session(state, t["id"], "s1", at(12), end=at(9, 15))

    def test_hhmm_means_the_sessions_own_day(self):
        session = {"start": at(9, day=-3).isoformat()}
        self.assertEqual(tasks.parse_end_near("10:30", session, at(12)), at(10, 30, day=-3))

    def test_hhmm_before_the_start_means_after_midnight(self):
        session = {"start": at(23).isoformat()}
        self.assertEqual(tasks.parse_end_near("00:20", session, at(12, day=2)), at(0, 20, day=1))

    def test_iso_and_bad_input(self):
        session = {"start": at(9).isoformat()}
        self.assertEqual(
            tasks.parse_end_near("2026-10-07T10:00:00+02:00", session, at(12)), at(10))
        for bad in ("unknown", "inferred", "soon"):
            with self.assertRaises(tasks.TempoError):
                tasks.parse_end_near(bad, session, at(12))

    def test_session_ids_stay_unique_after_a_discard(self):
        state = store.new_state()
        t = tasks.add_task(state, "t", 60, "", at(8))
        for h in (9, 10):
            tasks.start_task(state, t["id"], at(h))
            tasks.stop_active(state, at(h, 30), at=tasks.UNKNOWN)
        tasks.reconcile_session(state, t["id"], "s1", at(20), discard=True)
        tasks.start_task(state, t["id"], at(12))
        ids = [s["id"] for s in t["sessions"]]
        self.assertEqual(ids, ["s2", "s3"])

    def test_resolved_sessions_leave_the_report(self):
        state = store.new_state()
        t = unresolved(state, "u", at(9), at(10), "unknown")
        window = (at(0), at(0) + timedelta(days=1))
        before = report.build_window(state, "daily", "d", *window, at(12))
        self.assertEqual(len(before["unresolved"]), 1)
        tasks.reconcile_session(state, t["id"], "s1", at(12), accept=True)
        after = report.build_window(state, "daily", "d", *window, at(12))
        self.assertEqual(after["unresolved"], [])
        self.assertTrue(after["tasks"][0]["reliable"])


class TestCli(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        patcher = mock.patch.dict(os.environ, {"TEMPO_HOME": os.path.join(self._dir.name, "t")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.nudge = mock.patch.object(nudges, "_run", return_value=(0, "", "")).start()
        self.addCleanup(mock.patch.stopall)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tempo.main(list(argv))
        return out.getvalue()

    def make_unknown(self):
        self.run_cli("add", "one", "-e", "20")
        task_id = store.read()["tasks"][0]["id"]
        self.run_cli("start", task_id)
        self.run_cli("stop", "--at", "unknown")
        with store.transaction() as state:  # an old session, three days back
            s = state["tasks"][0]["sessions"][0]
            s["start"] = (tasks.now_local() - timedelta(days=3)).isoformat()
            s["end"] = (tasks.now_local() - timedelta(days=3) + timedelta(minutes=10)).isoformat()
            s["lastConfirmedAt"] = s["end"]
        return task_id

    def test_list_then_resolve(self):
        self.assertIn("nothing to reconcile", self.run_cli("reconcile"))
        task_id = self.make_unknown()
        listing = self.run_cli("reconcile")
        self.assertIn(task_id, listing)
        self.assertIn("unknown", listing)
        out = self.run_cli("reconcile", task_id, "s1", "--accept")
        self.assertIn("accepted session s1", out)
        self.assertIn("nothing to reconcile", self.run_cli("reconcile"))

    def test_end_flag_uses_the_sessions_own_day(self):
        task_id = self.make_unknown()
        start = store.read()["tasks"][0]["sessions"][0]["start"]
        self.run_cli("reconcile", task_id, "s1", "--end", "23:30")
        s = store.read()["tasks"][0]["sessions"][0]
        self.assertEqual(s["endState"], "confirmed")
        self.assertEqual(s["end"][:10], start[:10])

    def test_discard(self):
        task_id = self.make_unknown()
        self.assertIn("discarded", self.run_cli("reconcile", task_id, "s1", "--discard"))
        self.assertEqual(store.read()["tasks"][0]["sessions"], [])

    def test_errors(self):
        task_id = self.make_unknown()
        for argv, text in (
            (["reconcile", task_id], "name the session"),
            (["reconcile", task_id, "s9", "--accept"], "no session s9"),
            (["reconcile", task_id, "s1"], "exactly one"),
            (["reconcile", "--accept"], "name a task"),
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as cm:
                    self.run_cli(*argv)
                self.assertIn(text, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
