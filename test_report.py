"""Tests for report generation. Fixed clocks and explicit windows, no real time."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import nudges
import report
import store
import tasks
import tempo

TZ = timezone(timedelta(hours=2))
DAY0 = datetime(2026, 10, 7, 0, 0, tzinfo=TZ)
DAY1 = DAY0 + timedelta(days=1)


def at(hours, minutes=0, day=0):
    return DAY0 + timedelta(days=day, hours=hours, minutes=minutes)


def build(state, now, day=0):
    start = DAY0 + timedelta(days=day)
    return report.build_window(state, "daily", start.date().isoformat(),
                               start, start + timedelta(days=1), now)


def worked(state, name, estimate, start, end, end_state="confirmed"):
    """Add a task with one closed session between two datetimes."""
    task = tasks.add_task(state, name, estimate, "", start)
    tasks.start_task(state, task["id"], start)
    tasks.stop_active(state, end)
    task["sessions"][0]["endState"] = end_state
    return task


class TestWindows(unittest.TestCase):
    def test_empty_state(self):
        r = build(store.new_state(), at(12))
        self.assertEqual(r["tasks"], [])
        self.assertEqual(r["unresolved"], [])
        self.assertEqual(r["totals"]["workedMin"], 0)
        self.assertEqual(r["schemaVersion"], 1)

    def test_session_crossing_midnight_is_clipped_to_each_day(self):
        state = store.new_state()
        worked(state, "late", 120, at(23), at(1, day=1))
        today = build(state, at(12, day=2), day=0)
        tomorrow = build(state, at(12, day=2), day=1)
        self.assertEqual(today["tasks"][0]["actualWindowMin"], 60)
        self.assertEqual(tomorrow["tasks"][0]["actualWindowMin"], 60)
        self.assertEqual(today["tasks"][0]["actualTotalMin"], 120)  # whole task, not clipped

    def test_breaks_are_not_worked_time(self):
        state = store.new_state()
        task = tasks.add_task(state, "t", 60, "", at(9))
        tasks.start_task(state, task["id"], at(9))
        tasks.start_break(state, at(9, 20))
        tasks.end_break(state, at(9, 30))
        tasks.stop_active(state, at(10))
        r = build(state, at(12))
        self.assertEqual(r["totals"]["workedMin"], 50)
        self.assertEqual(r["tasks"][0]["sessions"][0]["breaks"], 1)

    def test_break_crossing_the_window_edge(self):
        state = store.new_state()
        task = tasks.add_task(state, "t", 60, "", at(22))
        tasks.start_task(state, task["id"], at(22))
        tasks.start_break(state, at(23))
        tasks.end_break(state, at(1, day=1))
        tasks.stop_active(state, at(2, day=1))
        self.assertEqual(build(state, at(12, day=2), 0)["totals"]["workedMin"], 60)
        self.assertEqual(build(state, at(12, day=2), 1)["totals"]["workedMin"], 60)

    def test_open_session_counts_up_to_now(self):
        state = store.new_state()
        task = tasks.add_task(state, "t", 90, "", at(9))
        tasks.start_task(state, task["id"], at(9))
        r = build(state, at(9, 25))
        self.assertEqual(r["totals"]["workedMin"], 25)
        self.assertEqual(r["sessionsByEndState"]["open"], 1)

    def test_other_days_do_not_appear(self):
        state = store.new_state()
        worked(state, "yesterday", 30, at(9, day=-1), at(10, day=-1))
        self.assertEqual(build(state, at(12))["tasks"], [])

    def test_daily_window_is_local_midnight_to_midnight(self):
        start, end = report.day_window(date(2026, 10, 7))
        self.assertEqual((start.hour, start.minute), (0, 0))
        self.assertEqual((end - start).days, 1)
        self.assertEqual(end.date(), date(2026, 10, 8))


class TestCompleted(unittest.TestCase):
    def test_estimate_vs_actual_for_tasks_finished_in_the_window(self):
        state = store.new_state()
        a = worked(state, "over", 60, at(9), at(10, 15))
        b = worked(state, "under", 60, at(11), at(11, 40))
        for t, when in ((a, at(10, 15)), (b, at(11, 40))):
            tasks.complete_task(state, t["id"], when)
        r = build(state, at(13))
        self.assertEqual(r["totals"]["completed"],
                         {"count": 2, "estimateMin": 120, "actualMin": 115.0, "deltaMin": -5.0})
        deltas = {t["name"]: t["deltaMin"] for t in r["tasks"]}
        self.assertEqual(deltas, {"over": 15.0, "under": -20.0})

    def test_unfinished_work_is_not_counted_as_completed(self):
        state = store.new_state()
        worked(state, "half", 60, at(9), at(9, 30))
        r = build(state, at(13))
        self.assertEqual(r["totals"]["completed"]["count"], 0)
        self.assertEqual(r["tasks"][0]["status"], "todo")

    def test_a_task_spanning_days_compares_its_whole_actual(self):
        state = store.new_state()
        task = worked(state, "long", 120, at(15, day=-1), at(16, day=-1))
        tasks.start_task(state, task["id"], at(9))
        tasks.complete_task(state, task["id"], at(10))
        r = build(state, at(13))
        row = r["tasks"][0]
        self.assertEqual(row["actualWindowMin"], 60)
        self.assertEqual(row["actualTotalMin"], 120)
        self.assertEqual(r["totals"]["completed"]["actualMin"], 120.0)

    def test_overrun_reason_is_carried(self):
        state = store.new_state()
        t = worked(state, "slow", 30, at(9), at(10))
        tasks.complete_task(state, t["id"], at(10), reason="scope grew")
        self.assertEqual(build(state, at(13))["tasks"][0]["overrunReason"], "scope grew")


class TestFriction(unittest.TestCase):
    def test_only_the_windows_friction_and_a_friction_only_task_appears(self):
        state = store.new_state()
        t = worked(state, "t", 30, at(9, day=-1), at(10, day=-1))
        tasks.complete_task(state, t["id"], at(10, day=-1))
        tasks.add_friction(state, "old", at(10, day=-1), t["id"])
        tasks.add_friction(state, "found out later", at(14), t["id"])
        r = build(state, at(15))
        self.assertEqual([x["name"] for x in r["tasks"]], ["t"])
        self.assertEqual([f["text"] for f in r["tasks"][0]["friction"]], ["found out later"])
        self.assertEqual(r["tasks"][0]["actualWindowMin"], 0)


class TestUnresolved(unittest.TestCase):
    def test_unknown_inferred_and_zombie_are_listed(self):
        state = store.new_state()
        worked(state, "u", 30, at(9), at(9, 30), end_state="unknown")
        worked(state, "i", 30, at(10), at(10, 30), end_state="inferred")
        worked(state, "ok", 30, at(10, 40), at(11))
        z = tasks.add_task(state, "z", 90, "", at(11, 30))
        tasks.start_task(state, z["id"], at(11, 30))  # started last, so still open
        r = build(state, at(16))  # z has been silent for 4.5 hours
        kinds = {u["taskName"]: u["kind"] for u in r["unresolved"]}
        self.assertEqual(kinds, {"u": "unknown", "i": "inferred", "z": "zombie"})
        reliable = {t["name"]: t["reliable"] for t in r["tasks"]}
        self.assertEqual(reliable, {"u": False, "i": False, "z": False, "ok": True})

    def test_a_running_session_is_not_a_zombie(self):
        state = store.new_state()
        task = tasks.add_task(state, "live", 90, "", at(9))
        tasks.start_task(state, task["id"], at(9))
        self.assertEqual(build(state, at(9, 30))["unresolved"], [])

    def test_unresolved_from_other_days_are_listed_but_marked(self):
        state = store.new_state()
        worked(state, "old", 30, at(9, day=-3), at(10, day=-3), end_state="unknown")
        r = build(state, at(12))
        self.assertEqual(r["tasks"], [])
        self.assertEqual(len(r["unresolved"]), 1)
        self.assertFalse(r["unresolved"][0]["inWindow"])
        self.assertEqual(r["unresolved"][0]["onDate"], "2026-10-04")

    def test_identifies_the_session_for_reconciliation(self):
        state = store.new_state()
        t = worked(state, "u", 30, at(9), at(9, 30), end_state="unknown")
        item = build(state, at(12))["unresolved"][0]
        self.assertEqual((item["taskId"], item["sessionId"]), (t["id"], "s1"))
        self.assertTrue(item["inWindow"])

    def test_end_states_are_counted_for_the_window_only(self):
        state = store.new_state()
        worked(state, "a", 30, at(9), at(10))
        worked(state, "b", 30, at(11), at(12), end_state="inferred")
        worked(state, "c", 30, at(9, day=-2), at(10, day=-2), end_state="unknown")
        counts = build(state, at(13))["sessionsByEndState"]
        self.assertEqual(counts, {"open": 0, "confirmed": 1, "inferred": 1, "unknown": 0})


class TestWrite(unittest.TestCase):
    def test_writes_valid_json_and_replaces_on_rerun(self):
        with tempfile.TemporaryDirectory() as d:
            r = build(store.new_state(), at(12))
            path = report.write(r, os.path.join(d, "reports"))
            self.assertEqual(os.path.basename(path), "daily-2026-10-07.json")
            with open(path) as f:
                self.assertEqual(json.load(f)["label"], "2026-10-07")
            r["totals"]["workedMin"] = 5
            report.write(r, os.path.join(d, "reports"))
            with open(path) as f:
                self.assertEqual(json.load(f)["totals"]["workedMin"], 5)
            self.assertEqual(os.listdir(os.path.join(d, "reports")), ["daily-2026-10-07.json"])


class TestCli(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.home = os.path.join(self._dir.name, "tempo")
        patcher = mock.patch.dict(os.environ, {"TEMPO_HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(nudges, "_run", return_value=(0, "", ""))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tempo.main(list(argv))
        return out.getvalue()

    def test_stdout_prints_the_json(self):
        self.run_cli("add", "one", "-e", "20")
        task_id = store.read()["tasks"][0]["id"]
        self.run_cli("start", task_id)
        self.run_cli("done")
        data = json.loads(self.run_cli("report", "daily", "--stdout"))
        self.assertEqual(data["kind"], "daily")
        self.assertEqual(data["totals"]["completed"]["count"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.home, "reports")))

    def test_default_writes_a_file_and_summarises(self):
        out = self.run_cli("report", "daily")
        today = tasks.now_local().date().isoformat()
        path = os.path.join(self.home, "reports", "daily-%s.json" % today)
        self.assertTrue(os.path.exists(path))
        self.assertIn("0 tasks", out)
        self.assertIn(path, out)

    def test_date_options(self):
        self.run_cli("report", "daily", "--date", "2026-01-02")
        self.run_cli("report", "daily", "--date", "yesterday")
        names = sorted(os.listdir(os.path.join(self.home, "reports")))
        self.assertIn("daily-2026-01-02.json", names)
        self.assertEqual(len(names), 2)

    def test_bad_date(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("report", "daily", "--date", "last tuesday")
        self.assertIn("bad date", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
