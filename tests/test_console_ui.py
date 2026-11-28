import io
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import _thread

from harness.background import BackgroundPool
from harness.console import (
    Application,
    LiveTurn,
    SerialUI,
    UIRouter,
    factory_reset_files,
)
from harness.executor import ExecutionResult, Executor
from harness.llm import LLMStreamCancelled
from harness.schedule import Scheduler
from tests.support import DummyConfig


class FakeSerial:
    def __init__(self):
        self.calls = []

    def write(self, text):
        self.calls.append(("write", str(text)))

    def line(self, text=""):
        self.calls.append(("line", str(text)))

    def readline(self, prompt="", secret=False):
        self.calls.append(("readline", prompt, secret))
        return "serial"

    def ask_yes_no(self, prompt, default=False):
        self.calls.append(("ask", prompt, default))
        return default


class MutableConfig(DummyConfig):
    def set(self, name, value, save=True):
        self.values[name] = value

    def set_secret(self, name, value, save=True):
        self.secrets[name] = value

    def save(self):
        pass


class FakeSink:
    def __init__(self):
        self.calls = []

    def write(self, text):
        self.calls.append(("write", str(text)))

    def line(self, text=""):
        self.calls.append(("line", str(text)))

    def readline(self, prompt="", secret=False):
        self.calls.append(("readline", prompt, secret))
        return "sink"

    def ask_yes_no(self, prompt, default=False):
        self.calls.append(("ask", prompt, default))
        return not default


class UIRouterTests(unittest.TestCase):
    def test_bind_routes_calls_and_unbind_restores_serial(self):
        serial = FakeSerial()
        sink = FakeSink()
        router = UIRouter(serial)
        router.bind(sink)
        router.write("a")
        router.line("b")
        self.assertEqual("sink", router.readline("p", True))
        self.assertTrue(router.ask_yes_no("q", False))
        self.assertIs(sink, router.current())
        router.unbind()
        router.line("c")
        self.assertEqual("serial", router.readline("p", False))
        self.assertEqual(("line", "c"), serial.calls[-2])
        self.assertEqual(("readline", "p", False), serial.calls[-1])

    def test_thread_local_sinks_do_not_leak_to_serial(self):
        serial = FakeSerial()
        router = UIRouter(serial)
        sink = FakeSink()
        ready = threading.Event()
        release = threading.Event()

        def worker():
            router.bind(sink)
            ready.set()
            release.wait(2)
            router.line("from web")
            router.unbind()
            return "ok"

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(ready.wait(2))
        router.line("from serial")
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual([("line", "from serial")], serial.calls)
        self.assertEqual([("line", "from web")], sink.calls)


class SerialLineEndingTests(unittest.TestCase):
    def read_two_lines(self, data, raw=False):
        if raw:
            stream = io.BytesIO(data)

            class Stdin:
                buffer = stream

            stdin = Stdin()
        else:
            stream = io.StringIO(data)
            stdin = stream

        class Poll:
            def poll(self, _timeout):
                if stream.tell() >= len(stream.getvalue()):
                    raise AssertionError("readline waited for unexpected extra input")
                return [(0, 1)]

        ui = SerialUI.__new__(SerialUI)
        ui.feed = lambda: None
        ui.poll = Poll()
        ui.write = lambda _text: None
        ui.skip_terminator = False
        ui.utf8_pending = bytearray()
        ui.utf8_needed = 0
        ui.pending_input = []
        with mock.patch("harness.console.sys.stdin", stdin):
            return ui.readline(), ui.readline()

    def test_lf_only_empty_line_is_not_swallowed(self):
        self.assertEqual(("/status", ""), self.read_two_lines("/status\n\n"))

    def test_crlf_and_lfcr_pairs_still_produce_one_line_break(self):
        self.assertEqual(("/status", ""), self.read_two_lines("/status\r\n\n"))
        self.assertEqual(("/status", ""), self.read_two_lines("/status\n\r\n"))

    def test_cr_only_empty_line_is_not_swallowed(self):
        self.assertEqual(("/status", ""), self.read_two_lines("/status\r\r"))

    def test_raw_buffer_pairs_are_swallowed(self):
        self.assertEqual(
            ("/status", ""), self.read_two_lines(b"/status\r\n\n", raw=True))
        self.assertEqual(
            ("/status", ""), self.read_two_lines(b"/status\n\r\n", raw=True))

    def test_raw_buffer_empty_lines_are_not_swallowed(self):
        self.assertEqual(
            ("/status", ""), self.read_two_lines(b"/status\n\n", raw=True))
        self.assertEqual(
            ("/status", ""), self.read_two_lines(b"/status\r\r", raw=True))

    def test_raw_buffer_decodes_utf8(self):
        self.assertEqual(
            ("测试", ""),
            self.read_two_lines("测试".encode("utf-8") + b"\n\n", raw=True))

    def test_buffered_input_codes_are_consumed_from_a_list(self):
        ui = SerialUI.__new__(SerialUI)
        ui.feed = lambda: None
        ui.poll = None
        ui.write = lambda _text: None
        ui.skip_terminator = False
        ui.utf8_pending = bytearray()
        ui.utf8_needed = 0
        ui.pending_input = list(b"/status\n")
        self.assertEqual("/status", ui.readline())
        self.assertEqual([], ui.pending_input)


class FactoryResetFileTests(unittest.TestCase):
    def test_removes_user_data_and_keeps_firmware_and_ca(self):
        with tempfile.TemporaryDirectory() as root:
            def write(relative, text="x"):
                path = os.path.join(root, relative)
                parent = os.path.dirname(path)
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)

            for name in (
                "config.json", "secrets.json", "MEMORY.md",
                "recovery.json", "recovery.py", "config.json.tmp",
                "maintenance.flag",
            ):
                write(name)
            for name in (
                "skills/demo/SKILL.md", "tools/demo.py",
                "tools/demo/data.txt", "workspace/notes.txt",
                "workspace/tmp/cache.bin",
            ):
                write(name)
            for name in ("boot.py", "main.py", "ca.pem"):
                write(name)

            removed = factory_reset_files(root)
            for name in (
                "config.json", "secrets.json", "MEMORY.md",
                "recovery.json", "recovery.py", "config.json.tmp",
                "maintenance.flag",
            ):
                self.assertFalse(os.path.exists(os.path.join(root, name)), name)
            for name in ("skills", "tools", "workspace"):
                self.assertFalse(os.path.exists(os.path.join(root, name)), name)
            for name in ("boot.py", "main.py", "ca.pem"):
                self.assertTrue(os.path.exists(os.path.join(root, name)), name)
            for name in (
                "/config.json", "/secrets.json", "/MEMORY.md",
                "/skills", "/tools", "/workspace",
            ):
                self.assertIn(name, removed)
            self.assertEqual([], factory_reset_files(root))


class MainRunner:
    def __init__(self, main_thread):
        self.main_thread = main_thread
        self.calls = 0

    def on_main_thread(self):
        return self.main_thread

    def run_on_main(self, func):
        self.calls += 1
        return func()


class RecordingExecutor(Executor):
    def __init__(self):
        Executor.__init__(self)
        self.local_calls = 0

    def _execute_local(self, source, filename="<agent>", timeout_s=None):
        self.local_calls += 1
        return "local:" + source


class ExecutorHandoffTests(unittest.TestCase):
    def test_non_main_calls_use_runner(self):
        executor = RecordingExecutor()
        runner = MainRunner(False)
        executor.main_runner = runner
        self.assertEqual("local:print(1)", executor.execute("print(1)"))
        self.assertEqual(1, runner.calls)
        self.assertEqual(1, executor.local_calls)

    def test_main_thread_executes_directly(self):
        executor = RecordingExecutor()
        runner = MainRunner(True)
        executor.main_runner = runner
        self.assertEqual("local:print(1)", executor.execute("print(1)"))
        self.assertEqual(0, runner.calls)
        self.assertEqual(1, executor.local_calls)

    def test_no_runner_executes_directly(self):
        executor = RecordingExecutor()
        self.assertEqual("local:x", executor.execute("x"))


class ApplicationTickTests(unittest.TestCase):
    def make_app(self):
        app = Application.__new__(Application)
        app._network_ready = True
        app.pending_wifi = ("home", "pw")
        app.turn_lock = _thread.allocate_lock()
        app.busy_source = ""
        app.config = DummyConfig()
        app.web_password_set = lambda: True
        app._ap_settings = lambda: ("ESP32-Harness-CCDD", "esp32setup")
        app.queue_notice = lambda text: None
        calls = []

        class Network:
            def connect(self, ssid, password, **kwargs):
                calls.append(("connect", ssid, kwargs.get("grace_ms")))
                return {"sta_ip": "10.0.0.50"}

            def maintain(self, *args, **kwargs):
                calls.append(("maintain", kwargs.get("password_set")))
                return {}

        app.network = Network()
        app.time_sync = mock.Mock()
        return app, calls

    def test_tick_connects_pending_wifi_then_runs_maintenance(self):
        app, calls = self.make_app()
        app.tick()
        self.assertIsNone(app.pending_wifi)
        self.assertEqual(
            [("connect", "home", 300000), ("maintain", True)], calls)
        self.assertEqual("", app.busy_source)

    def test_tick_skips_network_work_while_busy(self):
        app, calls = self.make_app()
        app.busy_source = "web-chat"
        app.tick()
        self.assertEqual(("home", "pw"), app.pending_wifi)
        self.assertEqual([], calls)
        self.assertEqual("web-chat", app.busy_source)


class ApplicationStopTests(unittest.TestCase):
    def make_app(self, source="auto-continue"):
        app = Application.__new__(Application)
        app.main_thread_id = threading.get_ident()
        app.turn_lock = _thread.allocate_lock()
        app.busy_source = source
        app.stop_requested = False
        app.backgrounds = None

        class CancelUI:
            def __init__(self):
                self.calls = 0

            def check_cancel(self):
                self.calls += 1

        app._serial_ui = CancelUI()
        return app

    def test_request_stop_is_consumed_by_check_cancel(self):
        app = self.make_app()
        self.assertTrue(app.request_turn_stop())
        self.assertTrue(app.stop_requested)
        with self.assertRaises(LLMStreamCancelled):
            app.check_cancel()
        self.assertFalse(app.stop_requested)
        self.assertEqual(0, app._serial_ui.calls)
        app.check_cancel()
        self.assertEqual(1, app._serial_ui.calls)

    def test_web_owned_or_idle_turn_is_not_cancelled(self):
        app = self.make_app("web-chat")
        self.assertFalse(app.request_turn_stop())
        self.assertFalse(app.stop_requested)
        app.busy_source = ""
        self.assertFalse(app.request_turn_stop())

    def test_begin_operation_discards_a_stale_stop_flag(self):
        app = self.make_app("")
        app.background_active = lambda: False
        app.stop_requested = True
        self.assertTrue(app.begin_operation("serial"))
        self.assertFalse(app.stop_requested)
        self.assertEqual("serial", app.busy_source)


class ApplicationHandoffTests(unittest.TestCase):
    def make_app(self):
        app = Application.__new__(Application)
        app.main_thread_id = threading.get_ident()
        app.main_lock = _thread.allocate_lock()
        app.main_request = None
        app.execution_active = False
        return app

    def test_worker_request_is_pumped_by_main_thread(self):
        app = self.make_app()
        results = []

        def worker():
            results.append(app.run_on_main(lambda: 42))

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.time() + 3
        while thread.is_alive() and time.time() < deadline:
            app.pump_main_request()
            time.sleep(0.001)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual([42], results)
        self.assertFalse(app.execution_active)
        self.assertIsNone(app.main_request)

    def test_feed_is_main_thread_and_not_during_execution(self):
        class Supervisor:
            def __init__(self):
                self.count = 0

            def feed(self):
                self.count += 1

        app = self.make_app()
        app.supervisor = Supervisor()
        app.feed()
        self.assertEqual(1, app.supervisor.count)
        app.execution_active = True
        app.feed()
        self.assertEqual(1, app.supervisor.count)
        app.execution_active = False
        app.main_thread_id = threading.get_ident() + 1
        app.feed()
        self.assertEqual(1, app.supervisor.count)

    def test_pump_publishes_errors(self):
        app = self.make_app()
        request = {
            "func": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            "started": False, "done": False, "result": None, "error": None,
        }
        app.main_request = request
        app.pump_main_request()
        self.assertTrue(request["done"])
        self.assertTrue(request["started"])
        self.assertIsInstance(request["error"], RuntimeError)
        self.assertFalse(app.execution_active)

    def test_waiting_timeout_cancels_request(self):
        app = self.make_app()
        app.main_thread_id = threading.get_ident() + 1
        times = [0, 20000, 20000, 20000]
        with mock.patch("harness.console.time.monotonic",
                        side_effect=lambda: times.pop(0) if times else 20000):
            with mock.patch("harness.console._sleep_ms", lambda _ms: None):
                with self.assertRaises(RuntimeError):
                    app.run_on_main(lambda: 1)
        self.assertIsNone(app.main_request)


class QuietSupervisor:
    def feed(self):
        pass


class BootTimeNetwork:
    def __init__(self):
        self.feed = None
        self.connected = False
        self.sync_calls = []

    def connect(self, ssid, password, timeout=25, force=False, grace_ms=None):
        self.connected = True
        return {"sta_ip": "192.168.1.50", "sta_connected": True}

    def status(self):
        return {"sta_connected": self.connected, "connected": self.connected}

    def time_sync_age_ms(self):
        return None

    def sync_time(self, server="pool.ntp.org", force=False):
        self.sync_calls.append((server, force))
        return True


class ApplicationConstructionTests(unittest.TestCase):
    def test_boot_network_synchronizes_time_after_connect(self):
        network = BootTimeNetwork()
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                config = MutableConfig(
                    web_enabled=True,
                    ntp_server="time.example.test",
                    timezone_offset_minutes=480,
                )
                config.secrets["web_password_hash"] = "hash"
                config.secrets["web_password_salt"] = "salt"
                app = Application(
                    root=root,
                    config=config,
                    network=network,
                    supervisor=QuietSupervisor(),
                )
            app.boot_network()
        self.assertEqual([("time.example.test", False)], network.sync_calls)
        self.assertTrue(app._network_ready)

    def test_constructor_initializes_background_store(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                app = Application(
                    root=root,
                    config=DummyConfig(
                        persist_conversation=True, web_enabled=False),
                    supervisor=QuietSupervisor(),
                )
            self.assertIsNone(app.background)
            self.assertIsNotNone(app.background_store)
            self.assertEqual(0, app.background_seq)
            self.assertFalse(app.background_active())

    def test_main_executor_observes_web_stop_request(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                app = Application(
                    root=root,
                    config=DummyConfig(web_enabled=False),
                    supervisor=QuietSupervisor(),
                )
            app.stop_requested = True
            result = app.executor.execute(
                "for i in range(1000):\n    pass\n", timeout_s=5)
            self.assertTrue(result.cancelled)
            self.assertFalse(result.timed_out)
            app.stop_requested = False
            result = app.executor.execute("print('still-runs')", timeout_s=5)
            self.assertTrue(result.ok)
            self.assertIn("still-runs", result.output)

    def test_workspace_run_accepts_file_above_code_limit(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                app = Application(
                    root=root,
                    config=MutableConfig(
                        web_enabled=False, auto_run=True, code_limit=32,
                        workspace_file_limit=4096),
                    supervisor=QuietSupervisor(),
                )
            source = "value = 1\n" * 40 + "print('console-big-ok')\n"
            app.workspace.write("big.py", source)
            app.ui.ask_yes_no = lambda prompt, default=False: True
            app.workspace_command(["run", "big.py"])
            self.assertTrue(any(
                "console-big-ok" in str(call) for call in app._serial_ui.calls))

    def test_peripherals_command_is_removed(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                app = Application(
                    root=root,
                    config=MutableConfig(web_enabled=False),
                    supervisor=QuietSupervisor(),
                )
            app.command("/peripherals")
            self.assertTrue(any(
                "Unknown command" in str(call) for call in app._serial_ui.calls))
            self.assertIsNone(app.config.get("spi_buses"))

    def test_memory_replace_accepts_inline_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                app = Application(
                    root=root,
                    config=MutableConfig(web_enabled=False),
                    supervisor=QuietSupervisor(),
                )
            app.memory.append("Board uses 115200 baud.")
            app.command("/memory replace 115200 => 921600")
            self.assertIn("921600", app.memory.bundle()["content"])

    def test_search_provider_command_only_accepts_auto_or_bing(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                app = Application(
                    root=root,
                    config=MutableConfig(web_enabled=False),
                    supervisor=QuietSupervisor(),
                )
            app.command("/search provider bing")
            self.assertEqual("bing", app.config.get("search_provider"))
            app.command("/search provider auto")
            self.assertEqual("auto", app.config.get("search_provider"))
            for value in ("deepseek", "anthropic", "openai", "unknown"):
                with self.assertRaises(ValueError):
                    app.command("/search provider " + value)
            with self.assertRaises(ValueError):
                app.command("/search key")

    def test_model_command_preserves_typed_custom_base(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                app = Application(
                    root=root,
                    config=MutableConfig(
                        web_enabled=False,
                        api_provider="openai",
                        base_url="https://api.deepseek.com/",
                        model="deepseek-flash",
                    ),
                    supervisor=QuietSupervisor(),
                )
            answers = iter([
                "anthropic",
                "https://api.deepseek.com/anthropic",
                "deepseek-flash",
                "",
                "",
            ])
            app.ui.readline = lambda prompt="", secret=False: next(
                answers, "")
            app.ui.ask_yes_no = lambda prompt, default=False: default
            app.command("/model")
            self.assertEqual("anthropic", app.config.get("api_provider"))
            self.assertEqual(
                "https://api.deepseek.com/anthropic",
                app.config.get("base_url"),
            )

    def test_real_workers_and_foreground_execution_are_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch("harness.console.SerialUI",
                            lambda _feed: FakeSerial()):
                app = Application(
                    root=root,
                    config=DummyConfig(
                        persist_conversation=False, web_enabled=False,
                        background_execution=True,
                        background_max_jobs=2,
                        background_thread_stack_kb=32,
                        code_timeout_s=5,
                    ),
                    supervisor=QuietSupervisor(),
                )
            jobs = []
            for name in ("bg-one", "bg-two"):
                app.agent.pending_source = (
                    "import time\n"
                    "for i in range(5):\n"
                    "    print('%s', i)\n"
                    "    time.sleep_ms(20)\n" % name)
                jobs.append(app.start_background(auto=True, driver="web"))
            self.assertEqual(2, len(app.background_views()))
            foreground = app.executor.execute("print('fg-only')")
            self.assertTrue(foreground.ok)
            self.assertIn("fg-only", foreground.output)
            deadline = time.monotonic() + 5
            while (any(job["phase"] in ("queued", "running") for job in jobs)
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            for job in jobs:
                self.assertEqual("ready", job["phase"], job.get("error"))
            self.assertIn("bg-one", jobs[0]["result"].output)
            self.assertIn("bg-two", jobs[1]["result"].output)
            self.assertNotIn("fg-only", jobs[0]["result"].output)
            self.assertNotIn("fg-only", jobs[1]["result"].output)
            self.assertNotIn("bg-one", foreground.output)
            taken = app.take_background_result(jobs[0]["id"])
            self.assertIn("bg-one", taken["result"].output)
            self.assertEqual(1, len(app.background_views()))


class FakeBackgroundStore:
    def __init__(self):
        self.saved = []
        self.cleared = 0
        self.last_error = ""
        self.loaded = []

    def save_jobs(self, jobs):
        self.saved.append([dict(job) for job in jobs])
        return True

    def load_jobs(self):
        return [dict(job) for job in self.loaded]

    def clear(self):
        self.cleared += 1


class FakeBackgroundAgent:
    def __init__(self):
        self.recorded = []
        self.history = [{"role": "user", "content": "task", "kind": "user"}]
        self.pending_source = "print(1)"
        self.pending_timeout = 5
        self.pending_question = None
        self.pending_skill = None
        self.recovered_pending = False
        self.recovered_execution = False
        self.last_result = None
        self.last_user_index = 0
        self.last_streamed = False
        self.turns = 1
        self.pending = False
        self.feedback_calls = 0

    def has_pending(self):
        return self.pending

    def can_continue(self):
        return True

    def continue_feedback(self):
        self.feedback_calls += 1
        return "continued", False

    def begin_execution(self):
        source = self.pending_source
        timeout = self.pending_timeout
        self.pending_source = None
        self.pending_timeout = None
        return source, timeout, False

    def restore_pending_source(self, source, timeout=None, recovered=False):
        self.pending_source = source
        self.pending_timeout = timeout

    def record_execution_result(self, result, recovered=False,
                                background=False, job_id=0, task=""):
        self.recorded.append((background, job_id, recovered))
        self.history.append({
            "role": "user", "content": "result", "kind": "observation"})
        self.last_result = result

    def record_local_observation(self, text):
        self.history.append({
            "role": "user", "content": text, "kind": "observation"})

    def has_background_result(self, job_id):
        return any("(job=%s)" % job_id in message.get("content", "")
                   for message in self.history)

    def discard_pending_skill(self):
        self.pending_skill = None


class FakeBackgroundExecutor:
    def execute(self, source, timeout_s=None):
        return ExecutionResult(True, "ok", "", 12)


class FakeSerialUI:
    def __init__(self):
        self.lines = []

    def sink(self):
        return None

    def line(self, text=""):
        self.lines.append(str(text))

    def write(self, text):
        self.lines.append(str(text))


class PromptingSerialUI(FakeSerialUI):
    def __init__(self):
        FakeSerialUI.__init__(self)
        self.asked = []

    def ask_yes_no(self, prompt, default=False):
        self.asked.append(prompt)
        return False


class ApplicationBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.release = {}

    def execute(self, job):
        source = job.get("source") or ""
        event = self.release.get(source)
        if event is not None:
            event.wait(5)
        return ExecutionResult(True, "ok:" + source, "", 7)

    def make_app(self, **overrides):
        values = {
            "background_execution": True,
            "background_max_jobs": 2,
            "background_max_records": 8,
            "background_thread_stack_kb": 24,
            "output_limit": 4096,
            "code_timeout_s": 5,
            "code_timeout_max_s": 300,
        }
        values.update(overrides)
        app = Application.__new__(Application)
        app.main_thread_id = threading.get_ident()
        app.main_lock = _thread.allocate_lock()
        app.main_request = None
        app.execution_active = False
        app.config = DummyConfig(**values)
        app.background_store = FakeBackgroundStore()
        app.agent = FakeBackgroundAgent()
        app.executor = FakeBackgroundExecutor()
        app.notices = []
        app.queue_notice = app.notices.append
        app.turn_lock = _thread.allocate_lock()
        app.busy_source = ""
        app.live = LiveTurn()
        app.backgrounds = BackgroundPool(
            app.config, app.background_store, self.execute,
            notify=app.queue_notice)
        self.app = app
        return app

    def make_auto_app(self, **overrides):
        values = {"auto_run": True, "auto_feedback": True}
        values.update(overrides)
        app = self.make_app(**values)
        app.ui = FakeSerialUI()
        app.prepare_cloud = lambda: None
        app.auto_continue_seq = 0
        return app

    def wait_phase(self, job, phase, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if job.get("phase") == phase:
                return
            time.sleep(0.01)
        self.fail((job, self.app.background_views()))

    def test_two_jobs_run_without_blocking_the_conversation_slot(self):
        app = self.make_app()
        self.release["wait-a"] = threading.Event()
        self.release["wait-b"] = threading.Event()
        app.agent.pending_source = "wait-a"
        first = app.start_background(auto=True, driver="web", note="alpha")
        app.agent.pending_source = "wait-b"
        second = app.start_background(auto=True, driver="web", note="beta")
        self.assertEqual("running", first["phase"])
        self.assertEqual("running", second["phase"])
        self.assertTrue(app.background_active())
        self.assertTrue(app.begin_operation("web-config"))
        app.end_operation()
        self.assertFalse(app.begin_operation("web-ca"))
        self.assertTrue(app.begin_operation("web-chat"))
        app.end_operation()
        self.assertTrue(app.begin_operation("serial"))
        app.end_operation()
        views = {view["id"]: view for view in app.background_views()}
        self.assertEqual("alpha", views[first["id"]]["note"])
        self.assertEqual("beta", views[second["id"]]["note"])
        self.release["wait-a"].set()
        self.release["wait-b"].set()
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")
        self.assertFalse(app.background_active())
        self.assertTrue(app.background_store.saved)
        taken = app.take_background_result(first["id"])
        self.assertEqual(1, len(app.agent.recorded))
        self.assertEqual(taken["token"], app.agent.recorded[0][1])
        self.assertIs(app.agent.last_result, taken["result"])

    def test_start_background_source_does_not_consume_pending(self):
        app = self.make_app(auto_run=True)
        app.agent.pending_source = "print('user-pending')"
        app.agent.pending_timeout = 7
        job = app.start_background_source(
            "print('agent-source')", 12, auto=True, driver="agent",
            note="agent note")
        self.assertEqual("agent note", job["note"])
        self.assertEqual(12, job["timeout"])
        self.assertEqual("print('user-pending')", app.agent.pending_source)
        self.assertEqual(7, app.agent.pending_timeout)
        self.wait_phase(job, "ready")
        self.assertIn("agent-source", job["result"].output)
        self.assertEqual(1, len(app.background_views()))

    def test_start_background_source_requires_auto_mode(self):
        app = self.make_app()
        with self.assertRaises(RuntimeError) as raised:
            app.start_background_source(
                "print(1)", 5, auto=True, driver="agent")
        self.assertIn("auto_run", str(raised.exception))
        self.assertEqual([], app.background_views())

    def test_thread_start_failure_restores_pending_code(self):
        app = self.make_app()

        def fail(func):
            raise RuntimeError("can't create thread")

        app.backgrounds.thread_starter = fail
        app.agent.pending_source = "print('retry-later')"
        app.agent.pending_timeout = 9
        with self.assertRaises(RuntimeError) as raised:
            app.start_background(auto=False, driver="web")
        self.assertIn("can't create thread", str(raised.exception))
        self.assertEqual("print('retry-later')", app.agent.pending_source)
        self.assertEqual(9, app.agent.pending_timeout)
        self.assertEqual([], app.background_views())
        self.assertFalse(app.background_active())

    def test_background_without_timeout_uses_configured_default(self):
        app = self.make_app(code_timeout_s=17)
        app.agent.pending_timeout = None
        job = app.start_background(auto=False, driver="web")
        self.assertEqual(17, job["timeout"])
        self.wait_phase(job, "ready")

    def test_take_records_even_with_an_old_same_id_marker(self):
        app = self.make_app()
        app.agent.history = [{
            "role": "user",
            "content": "old result (job=1)",
            "kind": "observation",
        }]
        job = app.start_background(auto=False, driver="web")
        self.wait_phase(job, "ready")
        taken = app.take_background_result()
        self.assertEqual(1, len(app.agent.recorded))
        self.assertEqual(taken["token"], app.agent.recorded[0][1])
        self.assertIs(app.agent.last_result, taken["result"])

    def test_running_job_cannot_be_discarded(self):
        app = self.make_app()
        self.release["wait"] = threading.Event()
        app.agent.pending_source = "wait"
        job = app.start_background(auto=False, driver="web")
        with self.assertRaises(RuntimeError):
            app.discard_background(job_id=job["id"])
        self.release["wait"].set()
        self.wait_phase(job, "ready")
        self.assertTrue(app.discard_background(job_id=job["id"]))
        self.assertIsNone(app.background)

    def test_discard_all_finished_from_serial_command(self):
        app = self.make_app()
        app.ui = FakeSerialUI()
        app.agent.pending_source = "one"
        first = app.start_background(auto=False, driver="web")
        app.agent.pending_source = "two"
        second = app.start_background(auto=False, driver="web")
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")
        app.background_command(["discard", "all"])
        self.assertEqual([], app.background_views())
        self.assertTrue(
            any("Discarded 2" in line for line in app.ui.lines))

    def test_prepare_for_new_turn_commits_all_ready_results(self):
        app = self.make_app()
        first = app.start_background(auto=False, driver="web")
        app.agent.pending_source = "second"
        second = app.start_background(auto=False, driver="web")
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")
        last = app.prepare_for_new_turn()
        self.assertEqual(second["id"], last["id"])
        self.assertEqual(2, len(app.agent.recorded))
        self.assertIsNone(app.background)
        self.assertEqual([], app.background_views())

    def test_queued_job_starts_after_a_slot_frees(self):
        app = self.make_app(background_max_jobs=1)
        self.release["wait-first"] = threading.Event()
        app.agent.pending_source = "wait-first"
        first = app.start_background(auto=False, driver="web")
        app.agent.pending_source = "quick"
        second = app.start_background(auto=False, driver="web")
        self.assertEqual("running", first["phase"])
        self.assertEqual("queued", second["phase"])
        self.release["wait-first"].set()
        self.wait_phase(first, "ready")
        self.assertTrue(
            app.backgrounds.view(second["id"])["phase"] in ("running", "ready"))
        self.wait_phase(second, "ready")
        self.assertEqual("ok:quick", second["result"].output)

    def test_stop_targets_one_job_and_then_all(self):
        app = self.make_app()
        self.release["one"] = threading.Event()
        self.release["two"] = threading.Event()
        app.agent.pending_source = "one"
        first = app.start_background(auto=False, driver="web")
        app.agent.pending_source = "two"
        second = app.start_background(auto=False, driver="web")
        self.assertEqual(1, app.stop_background("test", job_id=second["id"]))
        self.assertFalse(first["stop_requested"])
        self.assertTrue(second["stop_requested"])
        self.assertEqual(2, app.stop_background("test"))
        self.assertTrue(first["stop_requested"])
        self.release["one"].set()
        self.release["two"].set()
        self.wait_phase(first, "ready")
        self.wait_phase(second, "ready")

    def test_restore_uses_persisted_ready_jobs(self):
        app = self.make_app()
        app.background_store.loaded = [{
            "id": 9, "token": "9@9", "phase": "ready", "timeout": 5,
            "source_chars": 5, "task": "old", "note": "old", "turns": 1,
            "result": {"ok": True, "output": "ok", "elapsed_ms": 4},
        }]
        restored = app.restore_background()
        self.assertEqual(9, restored["id"])
        self.assertEqual("ready", app.background["phase"])
        self.assertEqual("ok", app.background["result"].output)

    def test_auto_mode_continues_ready_result_without_a_prompt(self):
        app = self.make_auto_app()
        job = app.start_background(auto=False, driver="web", note="soak")
        self.wait_phase(job, "ready")
        continued = app.auto_continue_background()
        self.assertIsNotNone(continued)
        self.assertEqual(1, app.agent.feedback_calls)
        self.assertEqual([], app.background_views())
        self.assertEqual(1, app.auto_continue_seq)
        self.assertEqual(1, len(app.agent.recorded))
        self.assertTrue(any(
            "automatically" in line for line in app.ui.lines))
        self.assertEqual("continued", app.live.content)
        self.assertFalse(app.live.active)
        self.assertGreaterEqual(app.live.id, 1)
        self.assertTrue(app.live.separate_turn)
        self.assertTrue(any(
            event.get("t") == "notice" for event in app.live.events))
        self.assertTrue(app.live.summary()["separate_turn"])
        self.assertFalse(any(
            event.get("t") == "execution" for event in app.live.events))

    def test_live_turn_buffers_and_caps_stream_text(self):
        live = LiveTurn(limit=1024)
        live.begin(separate_turn=True)
        self.assertTrue(live.summary()["separate_turn"])
        live.next_turn()
        live.append_content("a" * 600)
        live.next_turn()
        live.append_content("b" * 600)
        self.assertTrue(live.truncated)
        self.assertEqual(1024, len(live.content))
        self.assertTrue(live.content.startswith("a" * 600))
        self.assertEqual(2, sum(1 for event in live.events
                                if event.get("t") == "model_start"))
        live.append_reasoning("r" * 10)
        self.assertEqual(10, live.summary()["reasoning_chars"])
        for index in range(70):
            live.add_event({"i": index})
        self.assertEqual(24, len(live.events))
        self.assertEqual(48, live.events_dropped)
        self.assertEqual(48, live.view()["event_base"])
        self.assertTrue(live.view()["active"])
        live.finish()
        self.assertFalse(live.view()["active"])
        self.assertIsInstance(live.view()["finished_ms"], int)

    def test_auto_continue_waits_for_pending_or_manual_mode(self):
        app = self.make_auto_app(auto_feedback=False)
        job = app.start_background(auto=False, driver="web")
        self.wait_phase(job, "ready")
        self.assertIsNone(app.auto_continue_background())
        self.assertEqual(1, len(app.background_views()))
        self.assertEqual(0, app.agent.feedback_calls)

        app = self.make_auto_app()
        app.agent.pending = True
        job = app.start_background(auto=False, driver="web")
        self.wait_phase(job, "ready")
        self.assertIsNone(app.auto_continue_background())
        self.assertEqual(1, len(app.background_views()))

        app = self.make_auto_app(auto_run=False)
        job = app.start_background(auto=False, driver="web")
        self.wait_phase(job, "ready")
        self.assertIsNone(app.auto_continue_background())
        self.assertEqual(1, len(app.background_views()))

    def test_idle_auto_continues_only_at_the_top_level_prompt(self):
        app = self.make_auto_app()
        calls = []
        app.auto_continue_background = lambda: calls.append(True)
        app.pump_main_request = lambda: None
        app.drain_notices = lambda: None
        app.feed = lambda: None
        app._service_reboot = lambda: None
        app._at_prompt = False
        app.idle()
        self.assertEqual([], calls)
        app._at_prompt = True
        app.idle()
        self.assertEqual([True], calls)

        class PendingSerial:
            def has_pending_line(self):
                return True

        calls[:] = []
        app._serial_ui = PendingSerial()
        app.idle()
        self.assertEqual([], calls)

    def test_auto_continue_does_not_block_on_a_serial_skill_prompt(self):
        app = self.make_auto_app()
        app.ui = PromptingSerialUI()
        job = app.start_background(auto=False, driver="web")
        self.wait_phase(job, "ready")

        def propose_skill():
            app.agent.pending_skill = {
                "name": "proposal", "description": "d", "body": "b"}
            return "skill proposed", False

        app.agent.continue_feedback = propose_skill
        continued = app.auto_continue_background()
        self.assertIsNotNone(continued)
        self.assertEqual([], app.ui.asked)
        self.assertEqual("proposal", app.agent.pending_skill["name"])
        self.assertFalse(app.turn_lock.locked())
        self.assertTrue(any(
            "Skill proposal is pending" in line for line in app.ui.lines))

        # Outside the automatic continuation the serial confirmation remains.
        app.offer_pending_skill()
        self.assertEqual(1, len(app.ui.asked))

    def test_auto_continue_does_not_block_on_mcp_confirmation(self):
        app = self.make_auto_app()
        app.ui = PromptingSerialUI()
        app._auto_continue_running = True
        try:
            self.assertFalse(app.confirm_mcp_call("server", "write_tool", {}))
        finally:
            app._auto_continue_running = False
        self.assertEqual([], app.ui.asked)
        self.assertTrue(any(
            "requires confirmation" in line for line in app.ui.lines))

        self.assertFalse(app.confirm_mcp_call("server", "write_tool", {}))
        self.assertEqual(1, len(app.ui.asked))


class FakeScheduleAgent:
    def __init__(self):
        self.asked = []
        self.history = []
        self.pending_source = None
        self.pending_skill = None
        self.pending_question = None

    def has_pending(self):
        return False

    def ask(self, text):
        self.asked.append(text)
        self.history.append({"role": "user", "content": text,
                             "kind": "user"})
        self.history.append({"role": "assistant", "content": "done",
                             "kind": "assistant"})
        return "done", False


class ApplicationScheduleTests(unittest.TestCase):
    def make_app(self):
        app = Application.__new__(Application)
        app.main_thread_id = threading.get_ident()
        app.main_lock = _thread.allocate_lock()
        app.main_request = None
        app.execution_active = False
        app.busy_source = ""
        app.turn_lock = _thread.allocate_lock()
        app._schedule_running = False
        app._auto_continue_running = False
        app._at_prompt = True
        app.config = DummyConfig(
            background_execution=True,
            timezone_offset_minutes=480,
        )
        app.schedules = Scheduler(app.config, None)
        app.schedule_seq = 0
        app.agent = FakeScheduleAgent()
        app.live = LiveTurn()
        app.ui = FakeSerialUI()
        app.notices = []
        app.queue_notice = app.notices.append
        app.drain_notices = lambda: None
        app.prepare_for_new_turn = lambda: None
        app.prepare_cloud = lambda: None
        app.show_response = lambda explanation, has_code, auto=False: None
        app.run_pending = lambda auto=False: None
        app._require_background_execution = lambda: None
        app._create_background_job = lambda source, timeout, auto=False, driver="", note="": {"id": 7}
        return app

    def make_due_prompt(self, app, now=1000):
        app.schedules.create({
            "type": "prompt", "name": "ping",
            "prompt": "Check the board", "when": "once",
            "delay_s": 10,
        }, now)
        return now

    def snapshot(self, epoch, valid=True):
        return {"valid": valid, "epoch": epoch}

    def test_prompt_task_runs_one_agent_turn_and_disables_once(self):
        app = self.make_app()
        self.make_due_prompt(app)
        with mock.patch(
                "harness.console.clock.snapshot",
                return_value=self.snapshot(1020)):
            result = app.service_schedules()
        self.assertIsNotNone(result)
        self.assertEqual(1, len(app.agent.asked))
        self.assertIn("定时任务 #1", app.agent.asked[0])
        self.assertIn("Check the board", app.agent.asked[0])
        task = app.schedules.get(1)
        self.assertFalse(task["enabled"])
        self.assertEqual("ok", task["last_status"])
        self.assertEqual(1, task["run_count"])
        self.assertEqual(1, app.schedule_seq)
        self.assertEqual("", app.busy_source)
        self.assertFalse(app.turn_lock.locked())
        self.assertTrue(any(
            "Scheduled task #1" in line for line in app.ui.lines))

    def test_code_task_starts_a_background_job(self):
        app = self.make_app()
        app.schedules.create({
            "type": "code", "name": "blink",
            "source": "print(1)", "when": "once", "delay_s": 10,
        }, 1000)
        with mock.patch(
                "harness.console.clock.snapshot",
                return_value=self.snapshot(1020)):
            result = app.service_schedules()
        self.assertIsNotNone(result)
        task = app.schedules.get(1)
        self.assertFalse(task["enabled"])
        self.assertEqual("ok", task["last_status"])
        self.assertTrue(any(
            "started background job #7" in notice
            for notice in app.notices))
        self.assertEqual(1, app.schedule_seq)
        self.assertEqual([], app.agent.asked)

    def test_invalid_rtc_skips_scheduled_tasks(self):
        app = self.make_app()
        self.make_due_prompt(app)
        with mock.patch(
                "harness.console.clock.snapshot",
                return_value=self.snapshot(1020, valid=False)):
            self.assertIsNone(app.service_schedules())
        self.assertEqual([], app.agent.asked)
        self.assertTrue(app.schedules.get(1)["enabled"])

    def test_cancelled_auto_code_marks_the_task_cancelled(self):
        app = self.make_app()
        self.make_due_prompt(app)
        app.config.values["auto_run"] = True
        app.agent.ask = lambda text: ("done", True)
        app.agent.pending_source = "print(1)"
        app.agent.last_result = ExecutionResult(
            False, "", "ExecutionCancelled: stop", 5,
            False, False, True)
        with mock.patch(
                "harness.console.clock.snapshot",
                return_value=self.snapshot(1020)):
            app.service_schedules()
        task = app.schedules.get(1)
        self.assertEqual("error", task["last_status"])
        self.assertIn("cancelled", task["last_error"])
        self.assertFalse(app.turn_lock.locked())

    def test_prepare_failure_keeps_one_time_task_for_retry(self):
        app = self.make_app()
        self.make_due_prompt(app)

        def fail():
            raise RuntimeError("network offline")

        app.prepare_cloud = fail
        with mock.patch(
                "harness.console.clock.snapshot",
                return_value=self.snapshot(1020)):
            app.service_schedules()
        task = app.schedules.get(1)
        self.assertTrue(task["enabled"])
        self.assertEqual("error", task["last_status"])
        self.assertIn("network offline", task["last_error"])
        self.assertEqual(1020 + 60, task["next_run"])
        self.assertFalse(app.turn_lock.locked())
        self.assertEqual(1, app.schedule_seq)


if __name__ == "__main__":
    unittest.main()
