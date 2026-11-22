import tempfile
import threading
import time
import unittest
from unittest import mock

from harness.background import BackgroundPool
from harness.conversation import BackgroundStore
from harness.executor import ExecutionResult
from tests.support import DummyConfig


class FakeStore:
    def __init__(self):
        self.saved = []
        self.loaded = []
        self.cleared = 0
        self.last_error = ""

    def save_jobs(self, jobs):
        self.saved.append([dict(job) for job in jobs])
        return True

    def load_jobs(self):
        return [dict(job) for job in self.loaded]

    def clear(self):
        self.cleared += 1


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class BackgroundPoolTests(unittest.TestCase):
    def setUp(self):
        self.release = {}
        self.started = []
        self.thread_ids = []
        self.notices = []
        self.cleared = []

    def config(self, **overrides):
        values = {
            "persist_conversation": True,
            "background_max_jobs": 2,
            "background_max_records": 8,
            "background_thread_stack_kb": 24,
        }
        values.update(overrides)
        return DummyConfig(**values)

    def execute(self, job):
        """Controlled job body used by most tests."""
        self.started.append(job)
        self.thread_ids.append(threading.get_ident())
        source = job.get("source") or ""
        if source.startswith("wait"):
            event = self.release.get(source)
            if event is not None:
                event.wait(5)
        if source.startswith("stop"):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if job.get("stop_requested"):
                    return ExecutionResult(
                        False, "", "ExecutionCancelled: stop", 5,
                        False, False, True)
                time.sleep(0.01)
        return ExecutionResult(
            True, "ok:" + source, "", 5)

    def make_pool(self, **overrides):
        self.store = FakeStore()
        pool = BackgroundPool(
            self.config(**overrides), self.store, self.execute,
            notify=self.notices.append,
        )
        self.pool = pool
        return pool

    def wait_phase(self, job, phase, timeout=5.0):
        self.assertTrue(wait_for(lambda: job.get("phase") == phase, timeout),
                        (job, self.pool.views()))

    def test_two_jobs_run_concurrently_on_worker_threads(self):
        pool = self.make_pool()
        self.release["wait-a"] = threading.Event()
        self.release["wait-b"] = threading.Event()
        main = threading.get_ident()
        first = pool.create("wait-a", 0, note="alpha")
        second = pool.create("wait-b", 0, note="beta")
        self.assertEqual("running", first["phase"])
        self.assertEqual("running", second["phase"])
        self.assertTrue(wait_for(lambda: len(self.started) >= 2))
        self.assertEqual(2, len(self.thread_ids))
        self.assertNotIn(main, self.thread_ids)
        views = {view["id"]: view for view in pool.views()}
        self.assertEqual("alpha", views[first["id"]]["note"])
        self.assertEqual("beta", views[second["id"]]["note"])
        self.release["wait-a"].set()
        self.release["wait-b"].set()
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")
        self.assertEqual("ok:wait-a", first["result"].output)
        self.assertEqual("ok:wait-b", second["result"].output)
        self.assertFalse(pool.has_active())

    def test_concurrent_create_never_exceeds_slots(self):
        pool = self.make_pool(
            background_max_jobs=1, background_max_records=32)
        lock = threading.Lock()
        state = {"running": 0, "max": 0}

        def execute(job):
            with lock:
                state["running"] += 1
                if state["running"] > state["max"]:
                    state["max"] = state["running"]
            time.sleep(0.002)
            with lock:
                state["running"] -= 1
            return ExecutionResult(True, "ok", "", 2)

        pool.execute = execute
        errors = []

        def make():
            try:
                for _ in range(4):
                    pool.create("quick", 0)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=make) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(errors)
        self.assertTrue(wait_for(
            lambda: not pool.has_active() and not any(
                job.get("phase") == "queued" for job in pool.jobs), 10))
        self.assertLessEqual(state["max"], 1)
        self.assertEqual(12, len(pool.snapshot()))

    def test_queue_starts_when_a_slot_frees(self):
        pool = self.make_pool(background_max_jobs=1)
        self.release["wait-one"] = threading.Event()
        first = pool.create("wait-one", 0)
        second = pool.create("quick", 0)
        self.assertEqual("running", first["phase"])
        self.assertEqual("queued", second["phase"])
        self.assertFalse(wait_for(lambda: second["phase"] == "running", 0.1))
        self.release["wait-one"].set()
        self.wait_phase(first, "ready")
        self.assertTrue(wait_for(
            lambda: second["phase"] in ("running", "ready"), 5))
        self.wait_phase(second, "ready")
        self.assertEqual("ok:quick", second["result"].output)

    def test_lowering_and_raising_max_jobs(self):
        pool = self.make_pool(background_max_jobs=1)
        self.release["wait-a"] = threading.Event()
        first = pool.create("wait-a", 0)
        second = pool.create("wait-b", 0)
        self.assertEqual("queued", second["phase"])
        pool.config.values["background_max_jobs"] = 2
        pool.refresh_limits()
        self.wait_phase(second, "running")
        self.release["wait-a"].set()
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")

    def test_stop_running_and_queued_jobs(self):
        pool = self.make_pool(background_max_jobs=1)
        running = pool.create("stop-me", 0)
        queued = pool.create("never", 0)
        self.assertEqual("running", running["phase"])
        self.assertEqual("queued", queued["phase"])
        self.assertEqual(1, pool.stop(job_id=queued["id"], reason="test"))
        self.assertEqual("ready", queued["phase"])
        self.assertTrue(queued["result"].cancelled)
        self.assertEqual(1, pool.stop(job_id=running["id"], reason="test"))
        self.assertTrue(running["stop_requested"])
        self.wait_phase(running, "ready")
        self.assertTrue(running["result"].cancelled)
        self.assertFalse(pool.has_active())
        self.assertEqual(0, pool.stop())

    def test_stop_all_without_id(self):
        pool = self.make_pool()
        a = pool.create("stop-a", 0)
        b = pool.create("stop-b", 0)
        self.assertEqual(2, pool.stop(reason="all"))
        self.wait_phase(a, "ready")
        self.wait_phase(b, "ready")
        self.assertTrue(a["result"].cancelled)
        self.assertTrue(b["result"].cancelled)

    def test_take_commits_once_and_only_by_id(self):
        pool = self.make_pool()
        first = pool.create("one", 0)
        second = pool.create("two", 0)
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")
        committed = []
        taken = pool.take(first["id"], commit=lambda job: committed.append(job["id"]))
        self.assertEqual(first["id"], taken["id"])
        self.assertEqual([first["id"]], committed)
        # The commit callback ran while the job was still retained.
        with self.assertRaises(RuntimeError):
            pool.take(first["id"])
        taken2 = pool.take(second["id"])
        self.assertEqual(second["id"], taken2["id"])
        with self.assertRaises(RuntimeError):
            pool.take()

    def test_discard_requires_id_when_multiple_finished(self):
        pool = self.make_pool()
        first = pool.create("one", 0)
        second = pool.create("two", 0)
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")
        with self.assertRaises(RuntimeError):
            pool.discard()
        removed = pool.discard(job_id=first["id"])
        self.assertEqual(1, len(removed))
        self.assertEqual(second["id"], pool.views()[0]["id"])

    def test_thread_start_retries_with_smaller_stacks(self):
        pool = self.make_pool(background_thread_stack_kb=32)

        class FakeThread:
            def __init__(self):
                self.sizes = []
                self.current = 0
                self.started = False

            def stack_size(self, size):
                self.current = size

            def start_new_thread(self, func, args):
                self.sizes.append(self.current)
                if self.current > 16 * 1024:
                    raise OSError("can't create thread")
                self.started = True

        fake = FakeThread()
        with mock.patch("harness.background._thread", fake), \
                mock.patch("harness.background.time.sleep_ms",
                           lambda value: None, create=True):
            pool._start_thread(lambda: None)
        self.assertTrue(fake.started)
        self.assertEqual([32 * 1024, 24 * 1024, 16 * 1024], fake.sizes)

    def test_thread_start_reports_actionable_error_after_retries(self):
        pool = self.make_pool(background_thread_stack_kb=8)

        class AlwaysFail:
            def stack_size(self, size):
                pass

            def start_new_thread(self, func, args):
                raise OSError("can't create thread")

        with mock.patch("harness.background._thread", AlwaysFail()), \
                mock.patch("harness.background.time.sleep_ms",
                           lambda value: None, create=True):
            with self.assertRaises(RuntimeError) as raised:
                pool._start_thread(lambda: None)
        self.assertIn("background_thread_stack_kb", str(raised.exception))

    def test_missing_timeout_uses_configured_default_not_infinite(self):
        pool = self.make_pool(code_timeout_s=17)
        job = pool.create("quick", None)
        self.assertEqual(17, job["timeout"])
        self.wait_phase(job, "ready")
        explicit = pool.create("quick", 0)
        self.assertEqual(0, explicit["timeout"])
        self.wait_phase(explicit, "ready")

    def test_invalid_ids_do_not_select_the_first_job(self):
        pool = self.make_pool()
        job = pool.create("quick", 0)
        self.wait_phase(job, "ready")
        self.assertIsNone(pool.get("not-an-id"))
        self.assertEqual("none", pool.view("not-an-id")["phase"])
        self.assertEqual(0, pool.stop(job_id="not-an-id"))
        with self.assertRaises(RuntimeError):
            pool.take("not-an-id")
        with self.assertRaises(RuntimeError):
            pool.discard(job_id="not-an-id")
        self.assertEqual("ready", pool.get(job["id"])["phase"])

    def test_note_is_updateable(self):
        pool = self.make_pool()
        job = pool.create("quick", 0, note="old")
        self.wait_phase(job, "ready")
        updated = pool.note(job["id"], "dashboard")
        self.assertEqual("dashboard", updated["note"])
        self.assertEqual("dashboard", pool.view(job["id"])["note"])

    def test_summary_lists_id_note_and_elapsed(self):
        pool = self.make_pool()
        self.release["wait-summary"] = threading.Event()
        job = pool.create("wait-summary", 0, note="dashboard")
        lines = "\n".join(pool.summary_lines())
        self.assertIn("#%s" % job["id"], lines)
        self.assertIn("dashboard", lines)
        self.assertIn("elapsed=", lines)
        self.assertIn("timeout=infinite", lines)
        self.release["wait-summary"].set()
        self.wait_phase(job, "ready")

    def test_max_records_prunes_errors_but_refuses_to_drop_ready(self):
        pool = self.make_pool(background_max_jobs=4, background_max_records=2)
        first = pool.create("one", 0)
        second = pool.create("two", 0)
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")
        with self.assertRaises(RuntimeError):
            pool.create("third", 0)

    def test_restore_marks_interrupted_jobs_as_error(self):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        store = BackgroundStore(root, self.config())
        store.save_jobs([
            {"id": 1, "token": "1@1", "phase": "running", "timeout": 0,
             "source_chars": 3, "task": "old", "note": "old", "turns": 1},
            {"id": 2, "token": "2@2", "phase": "ready", "timeout": 5,
             "source_chars": 3, "task": "done", "note": "done", "turns": 1,
             "result": {"ok": True, "output": "ok", "elapsed_ms": 4}},
        ])
        self.store = FakeStore()
        pool = BackgroundPool(
            self.config(), store, self.execute, notify=self.notices.append)
        restored = pool.restore()
        by_id = {job["id"]: job for job in restored}
        self.assertEqual("error", by_id[1]["phase"])
        self.assertIn("restart", by_id[1]["error"])
        self.assertEqual("ready", by_id[2]["phase"])
        self.assertEqual("ok", by_id[2]["result"].output)
        self.assertEqual(2, pool.seq)

    def test_restore_keeps_ready_results_when_max_records_is_lowered(self):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        store = BackgroundStore(root, self.config(background_max_records=2))
        store.save_jobs([
            {"id": 1, "token": "1@1", "phase": "ready", "timeout": 5,
             "source_chars": 3, "task": "one", "note": "one", "turns": 1,
             "result": {"ok": True, "output": "one", "elapsed_ms": 4}},
            {"id": 2, "token": "2@2", "phase": "ready", "timeout": 5,
             "source_chars": 3, "task": "two", "note": "two", "turns": 1,
             "result": {"ok": True, "output": "two", "elapsed_ms": 4}},
            {"id": 3, "token": "3@3", "phase": "ready", "timeout": 5,
             "source_chars": 3, "task": "three", "note": "three", "turns": 1,
             "result": {"ok": True, "output": "three", "elapsed_ms": 4}},
            {"id": 4, "token": "4@4", "phase": "error", "timeout": 5,
             "source_chars": 3, "task": "bad", "note": "bad", "turns": 1,
             "error": "failed"},
        ])
        pool = BackgroundPool(
            self.config(background_max_records=2), store, self.execute,
            notify=self.notices.append)
        restored = pool.restore()
        self.assertEqual([1, 2, 3], [job["id"] for job in restored])
        self.assertTrue(all(job["phase"] == "ready" for job in restored))
        with self.assertRaises(RuntimeError):
            pool.create("later", 5)

    def test_restore_drops_committed_token(self):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        store = BackgroundStore(root, self.config())
        store.save_jobs([
            {"id": 5, "token": "5@5", "phase": "ready", "timeout": 5,
             "source_chars": 3, "task": "done", "note": "done", "turns": 1,
             "result": {"ok": True, "output": "ok", "elapsed_ms": 4}},
        ])
        self.store = FakeStore()
        pool = BackgroundPool(
            self.config(), store, self.execute, notify=self.notices.append)
        self.assertEqual([], pool.restore(committed=lambda token: token == "5@5"))
        self.assertEqual([], pool.views())
        self.assertFalse(store.exists())


if __name__ == "__main__":
    unittest.main()
