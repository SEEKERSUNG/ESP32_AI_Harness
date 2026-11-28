import os
import tempfile
import unittest
from unittest import mock

from harness import clock
from harness.schedule import (
    MAX_ATTEMPTS,
    RETRY_DELAY_S,
    ScheduleStore,
    Scheduler,
)
from tests.support import DummyConfig


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = DummyConfig(
            timezone_offset_minutes=480,
            schedule_file="/state/schedules.json",
            schedule_max_tasks=4,
            schedule_min_interval_s=30,
        )
        self.store = ScheduleStore(self.temp.name, self.config)
        self.scheduler = Scheduler(self.config, self.store)
        # 2026-11-26 00:00:00 UTC == 08:00 local (UTC+8)
        self.now = clock.epoch_from_utc(2026, 11, 26, 0, 0, 0)

    def tearDown(self):
        self.temp.cleanup()

    def create_prompt(self, **fields):
        values = {
            "type": "prompt",
            "name": "reminder",
            "prompt": "Check the sensor",
            "when": "once",
            "delay_s": 600,
        }
        values.update(fields)
        return self.scheduler.create(values, self.now)

    def test_once_delay_and_custom_name(self):
        task = self.create_prompt()
        self.assertEqual(self.now + 600, task["next_run"])
        self.assertEqual("reminder", task["name"])
        self.assertEqual("once after 600s", self.scheduler.when_label(task))

    def test_once_absolute_local_time_uses_configured_offset(self):
        task = self.create_prompt(
            when="once", delay_s=None, at="2026-11-27 08:00")
        expected = clock.epoch_from_utc(2026, 11, 27, 0, 0, 0)
        self.assertEqual(expected, task["next_run"])
        view = self.scheduler.view(task["id"])
        self.assertIn("2026-11-27 08:00:00 UTC+08:00", view["next_local"])

    def test_daily_next_occurrence_crosses_midnight(self):
        task = self.create_prompt(when="daily", delay_s=None, at="08:00")
        # Local time is already 08:00, so the next run is tomorrow.
        self.assertEqual(
            clock.epoch_from_utc(2026, 11, 27, 0, 0, 0),
            task["next_run"])

    def test_interval_uses_seconds(self):
        task = self.create_prompt(when="interval", delay_s=None, every_s=3600)
        self.assertEqual(self.now + 3600, task["next_run"])
        self.assertEqual("every 1 hour(s)", self.scheduler.when_label(task))

    def test_update_fields_and_schedule(self):
        task = self.create_prompt()
        updated = self.scheduler.update(task["id"], {
            "name": "renamed",
            "prompt": "New instruction",
            "when": "interval",
            "every_s": 120,
        }, self.now + 10)
        self.assertEqual("renamed", updated["name"])
        self.assertEqual("New instruction", updated["prompt"])
        self.assertEqual(self.now + 10 + 120, updated["next_run"])
        self.assertEqual(120, updated["every_s"])

    def test_delete_enable_disable_and_run_now(self):
        task = self.create_prompt()
        disabled = self.scheduler.set_enabled(task["id"], False)
        self.assertFalse(disabled["enabled"])
        self.assertIsNone(
            self.scheduler.due(self.now + 10000))
        enabled = self.scheduler.set_enabled(task["id"], True)
        self.assertTrue(enabled["enabled"])
        queued = self.scheduler.request_run(task["id"], self.now)
        self.assertEqual(self.now, queued["next_run"])
        self.assertEqual(
            task["id"], self.scheduler.due(self.now)["id"])
        deleted = self.scheduler.delete(task["id"])
        self.assertEqual(task["id"], deleted["id"])
        self.assertEqual([], self.scheduler.snapshot())

    def test_due_picks_oldest_and_ignores_disabled(self):
        first = self.create_prompt(delay_s=60, name="first")
        self.create_prompt(delay_s=600, name="second")
        self.scheduler.set_enabled(first["id"], False)
        due = self.scheduler.due(self.now + 700)
        self.assertEqual("second", due["name"])
        self.scheduler.set_enabled(first["id"], True)
        due = self.scheduler.due(self.now + 700)
        self.assertEqual("first", due["name"])

    def test_begin_run_is_one_shot_until_finish(self):
        task = self.create_prompt()
        started = self.scheduler.begin_run(task["id"], self.now + 600)
        self.assertTrue(started["running_since"])
        self.assertIsNone(
            self.scheduler.begin_run(task["id"], self.now + 600))
        self.assertIsNone(self.scheduler.due(self.now + 600))
        finished = self.scheduler.finish_run(
            task["id"], True, "", self.now + 605)
        self.assertFalse(finished["enabled"])
        self.assertEqual("ok", finished["last_status"])

    def test_repeating_success_advances_from_scheduled_time(self):
        task = self.create_prompt(
            when="interval", delay_s=None, every_s=3600)
        due_at = task["next_run"]
        self.scheduler.begin_run(task["id"], due_at)
        finished = self.scheduler.finish_run(
            task["id"], True, "", due_at + 5)
        self.assertEqual(due_at + 3600, finished["next_run"])
        self.assertTrue(finished["enabled"])

    def test_failure_retries_then_disables_a_one_time_task(self):
        task = self.create_prompt()
        now = task["next_run"]
        for attempt in range(MAX_ATTEMPTS):
            self.scheduler.begin_run(task["id"], now)
            finished = self.scheduler.finish_run(
                task["id"], False, "network", now)
            now = finished["next_run"]
            self.assertEqual("error", finished["last_status"])
            self.assertEqual("network", finished["last_error"])
            if attempt < MAX_ATTEMPTS - 1:
                self.assertTrue(finished["enabled"])
                self.assertEqual(
                    self.now + 600 + (attempt + 1) * RETRY_DELAY_S,
                    finished["next_run"])
        self.assertFalse(finished["enabled"])
        self.assertEqual(0, finished["attempts"])

    def test_repeating_failure_falls_back_to_the_next_regular_run(self):
        task = self.create_prompt(
            when="interval", delay_s=None, every_s=600)
        now = task["next_run"]
        for _ in range(MAX_ATTEMPTS):
            self.scheduler.begin_run(task["id"], now)
            failure_at = now
            finished = self.scheduler.finish_run(
                task["id"], False, "boom", failure_at)
            now = finished["next_run"]
        self.assertTrue(finished["enabled"])
        self.assertGreater(finished["next_run"], failure_at)
        self.assertEqual(0, finished["attempts"])

    def test_restore_persists_and_clears_interrupted_runs(self):
        task = self.create_prompt()
        self.scheduler.begin_run(task["id"], task["next_run"])
        restored = Scheduler(self.config, self.store)
        tasks = restored.restore()
        self.assertEqual(1, len(tasks))
        self.assertEqual(0, tasks[0]["running_since"])
        self.assertEqual(task["next_run"], tasks[0]["next_run"])
        self.assertTrue(tasks[0]["enabled"])

    def test_task_limit_and_validation(self):
        for index in range(4):
            self.create_prompt(name="task-%s" % index)
        with self.assertRaisesRegex(ValueError, "Too many"):
            self.create_prompt(name="overflow")
        with self.assertRaisesRegex(ValueError, "prompt is required"):
            self.scheduler.create(
                {"type": "prompt", "when": "once", "delay_s": 10}, self.now)
        with self.assertRaisesRegex(ValueError, "every_s"):
            self.scheduler.create(
                {"type": "prompt", "prompt": "x", "when": "interval"},
                self.now)
        with self.assertRaisesRegex(ValueError, "at must"):
            self.scheduler.create(
                {"type": "prompt", "prompt": "x", "when": "daily",
                 "at": "25:99"}, self.now)

    def test_code_task_compiles_and_rejects_syntax_errors(self):
        task = self.scheduler.create({
            "type": "code", "name": "blink", "source": "print('ok')",
            "when": "once", "delay_s": 30, "timeout": 5,
        }, self.now)
        self.assertEqual("code", task["type"])
        self.assertEqual(5, task["timeout"])
        with self.assertRaisesRegex(ValueError, "syntax error"):
            self.scheduler.create({
                "type": "code", "name": "bad", "source": "if:",
                "when": "once", "delay_s": 30,
            }, self.now)

    def test_code_task_reports_compile_memory_error(self):
        with mock.patch("harness.executor.compile",
                        side_effect=MemoryError("allocating 196 bytes"),
                        create=True):
            with self.assertRaisesRegex(
                    ValueError, "compile-time MemoryError"):
                self.scheduler.create({
                    "type": "code", "name": "bad", "source": "print('x')",
                    "when": "once", "delay_s": 30,
                }, self.now)

    def test_rebase_timezone_moves_daily_next_run(self):
        task = self.create_prompt(when="daily", delay_s=None, at="08:00")
        before = task["next_run"]
        self.config.values["timezone_offset_minutes"] = 0
        self.now = clock.epoch_from_utc(2026, 11, 26, 0, 30, 0)
        self.assertTrue(self.scheduler.rebase_timezone(self.now))
        after = self.scheduler.get(task["id"])["next_run"]
        self.assertNotEqual(before, after)
        self.assertEqual(
            clock.epoch_from_utc(2026, 11, 26, 8, 0, 0), after)

    def test_store_file_is_created_under_state(self):
        self.create_prompt()
        path = os.path.join(self.temp.name, "state", "schedules.json")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as handle:
            data = handle.read()
        self.assertIn("\"tasks\"", data)
        self.assertIn("Check the sensor", data)


if __name__ == "__main__":
    unittest.main()
