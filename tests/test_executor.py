import importlib
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import harness.executor as executor_module
from harness.executor import (
    BufferWriter,
    ExecutionTimeout,
    Executor,
    instrument_source,
    warm_viper_compiler,
)
from harness.workspace import Workspace
from tests.support import DummyBoard


class FakeRecovery:
    def __init__(self):
        self.started = 0
        self.ended = 0

    def begin(self, source):
        self.started += 1

    def end(self):
        self.ended += 1


class ExecutorTests(unittest.TestCase):
    def test_buffer_writer_normalizes_terminal_newlines(self):
        writer = BufferWriter(100)
        writer.write("one\r\ntwo\rthree\n")
        self.assertEqual("one\ntwo\nthree\n", writer.getvalue())

    def test_success_captures_output_and_device(self):
        recovery = FakeRecovery()
        result = Executor(DummyBoard(), recovery=recovery).execute("print(device.info())")
        self.assertTrue(result.ok)
        self.assertIn("True", result.output)
        self.assertEqual(1, recovery.started)
        self.assertEqual(1, recovery.ended)

    def test_generated_code_can_import_machine(self):
        machine = types.ModuleType("machine")
        machine.freq = lambda: 240000000
        with mock.patch.dict(sys.modules, {"machine": machine}):
            result = Executor(DummyBoard()).execute(
                "import machine\nprint(machine.freq())")
        self.assertTrue(result.ok)
        self.assertIn("240000000", result.output)

    def test_api_global_is_not_injected(self):
        result = Executor(DummyBoard()).execute("print(api)")
        self.assertFalse(result.ok)
        self.assertIn("api", result.error)

    def test_cancel_check_stops_at_cooperative_checkpoint(self):
        result = Executor(
            DummyBoard(), cancel_check=lambda: True
        ).execute("while True:\n    pass")
        self.assertFalse(result.ok)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.timed_out)
        observation = result.observation()
        self.assertIn("status=cancelled", observation)
        self.assertIn("stop was requested", observation)
        self.assertIn("cooperative", observation)

    def test_cancel_check_can_fire_after_some_work(self):
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) >= 3

        result = Executor(
            DummyBoard(), timeout_s=5, cancel_check=cancel
        ).execute("import time\nfor i in range(100000):\n    time.sleep_ms(1)")
        self.assertTrue(result.cancelled)
        self.assertGreaterEqual(len(calls), 3)

    def test_nested_exec_gets_harness_check(self):
        import builtins
        result = Executor(DummyBoard()).execute(
            "g = {'__name__': '__main__'}\n"
            "exec('__harness_check(); print(\"nested-ok\")', g)")
        self.assertTrue(result.ok)
        self.assertIn("nested-ok", result.output)
        self.assertFalse(hasattr(builtins, "__harness_check"))

    def test_nested_exec_loop_is_stopped_by_harness_check(self):
        result = Executor(DummyBoard(), timeout_s=1).execute(
            "g = {'__name__': '__main__'}\n"
            "exec('def loop():\\n"
            "    while True:\\n"
            "        __harness_check()\\n"
            "loop()', g)")
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertIn("ExecutionTimeout", result.error)

    def test_syntax_error_does_not_start_recovery_marker(self):
        recovery = FakeRecovery()
        result = Executor(DummyBoard(), recovery=recovery).execute("if True print('bad')")
        self.assertFalse(result.ok)
        self.assertIn("SyntaxError", result.output)
        self.assertEqual(0, recovery.started)

    def test_compile_memory_error_reports_stage_and_heap(self):
        with mock.patch("harness.executor.compile",
                        side_effect=MemoryError("allocating 260 bytes"),
                        create=True):
            result = Executor(DummyBoard(), timeout_s=5).execute(
                "from micropython import viper\n"
                "@viper\n"
                "def f(n: int):\n"
                "    for i in range(n):\n"
                "        pass\n"
                "print('never-ran')\n")
        self.assertFalse(result.ok)
        self.assertIn("MemoryError", result.error)
        self.assertIn("compile stage", result.error)
        self.assertIn("script did not start", result.error)
        self.assertIn("compile_retries=2", result.error)
        self.assertIn("viper/native=yes", result.error)
        self.assertIn("not a low-memory condition", result.error)
        self.assertIn("imported .mpy module", result.error)
        self.assertNotIn("internal_free=", result.error)
        self.assertNotIn("never-ran", result.output)

    def test_compile_memory_error_reports_internal_heap_split(self):
        fake = types.ModuleType("esp32")
        fake.HEAP_DATA = 1
        fake.idf_heap_info = lambda caps: [
            (32767, 32031, 31744, 32031),
            (8388608, 8320372, 8257536, 7239024),
            (249784, 200356, 196608, 198720),
        ]
        with mock.patch.dict(sys.modules, {"esp32": fake}):
            with mock.patch("harness.executor.compile",
                            side_effect=MemoryError("allocating 144 bytes"),
                            create=True):
                result = Executor(DummyBoard()).execute("print('never-ran')\n")
        self.assertIn("internal_free=232387", result.error)
        self.assertIn("internal_largest_block=196608", result.error)
        self.assertIn("psram_free=8320372", result.error)
        self.assertNotIn("idf_free", result.error)

    def test_compile_retries_memory_error_once(self):
        real_compile = compile
        calls = []

        def flaky(source, filename, mode):
            calls.append(1)
            if len(calls) == 1:
                raise MemoryError("allocating 116 bytes")
            return real_compile(source, filename, mode)

        with mock.patch("harness.executor.compile",
                        side_effect=flaky, create=True):
            result = Executor(DummyBoard(), timeout_s=5).execute(
                "print('retry-ok')\n")
        self.assertTrue(result.ok)
        self.assertIn("retry-ok", result.output)
        self.assertEqual(2, len(calls))

    def test_warm_viper_compiler_swallows_failure(self):
        with mock.patch("harness.executor.compile",
                        side_effect=MemoryError("allocating 116 bytes"),
                        create=True):
            self.assertFalse(warm_viper_compiler())
        self.assertTrue(warm_viper_compiler())

    def test_compile_memory_error_without_viper_has_no_viper_hint(self):
        with mock.patch("harness.executor.compile",
                        side_effect=MemoryError("allocating 144 bytes"),
                        create=True):
            result = Executor(DummyBoard()).execute("print('never-ran')\n")
        self.assertIn("compile stage", result.error)
        self.assertNotIn("viper/native", result.error)

    def test_compile_memory_note_ignores_similar_names(self):
        with mock.patch("harness.executor.compile",
                        side_effect=MemoryError("allocating 8 bytes"),
                        create=True):
            result = Executor(DummyBoard()).execute(
                "@viper_deco\ndef f():\n    pass\n")
        self.assertIn("compile stage", result.error)
        self.assertNotIn("viper/native", result.error)

    def test_runtime_memory_error_is_not_reported_as_compile(self):
        result = Executor(DummyBoard()).execute(
            "raise MemoryError('allocating 1 byte')")
        self.assertFalse(result.ok)
        self.assertIn("MemoryError", result.error)
        self.assertNotIn("compile stage", result.error)

    def test_runtime_error_is_captured(self):
        result = Executor(DummyBoard()).execute("print('before')\n1 / 0")
        self.assertFalse(result.ok)
        self.assertIn("before", result.output)
        self.assertIn("ZeroDivisionError", result.output)

    def test_write_returns_input_bytes_for_utf8(self):
        writer = BufferWriter(100)
        self.assertEqual(6, writer.write("测试".encode()))
        self.assertEqual("测试", writer.getvalue())

    def test_output_is_bounded(self):
        result = Executor(DummyBoard(), output_limit=5).execute("print('123456789')")
        self.assertTrue(result.ok)
        self.assertIn("truncated", result.output)

    def test_large_exception_message_is_bounded(self):
        result = Executor(DummyBoard(), output_limit=128).execute("raise ValueError('x' * 10000)")
        self.assertFalse(result.ok)
        self.assertLessEqual(len(result.error), 128)
        observation = result.observation(limit=96)
        self.assertLessEqual(len(observation), 96)
        self.assertIn("truncated", observation)

    def test_range_loop_times_out_and_reports_timeout(self):
        feeds = []
        result = Executor(
            DummyBoard(), timeout_s=1, feed=lambda: feeds.append(1)
        ).execute("for i in range(10 ** 9):\n    pass\nprint('finished')")
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertIn("ExecutionTimeout", result.error)
        self.assertNotIn("finished", result.output)
        observation = result.observation()
        self.assertIn("status=timeout", observation)
        self.assertIn("did not reboot", observation)
        self.assertIn("opening fence line", observation)
        self.assertIn("after the closing fence", observation)
        self.assertTrue(feeds)

    def test_watchdog_feed_is_time_gated(self):
        feeds = []

        def feed():
            feeds.append(time.monotonic())

        result = Executor(
            DummyBoard(), timeout_s=30, feed=feed
        ).execute("i = 0\nwhile i < 50000:\n    i += 1")
        self.assertTrue(result.ok)
        self.assertTrue(feeds)
        self.assertLess(len(feeds), 10)

    def test_cancel_poll_is_amortised(self):
        calls = []

        def cancel():
            calls.append(1)
            return False

        result = Executor(
            DummyBoard(), timeout_s=30, cancel_check=cancel
        ).execute("i = 0\nwhile i < 100000:\n    i += 1")
        self.assertTrue(result.ok)
        self.assertTrue(calls)
        self.assertLess(len(calls), 100)

    def test_sleep_times_out_without_waiting_for_full_sleep(self):
        started = time.monotonic()
        result = Executor(DummyBoard(), timeout_s=1).execute("import time\ntime.sleep(30)")
        elapsed = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 5)

    def test_harness_check_times_out(self):
        result = Executor(DummyBoard(), timeout_s=1).execute(
            "while True:\n    __harness_check()")
        self.assertTrue(result.timed_out)

    def test_timeout_is_not_swallowed_by_except_exception(self):
        result = Executor(DummyBoard(), timeout_s=1).execute(
            "try:\n"
            "    while True:\n"
            "        __harness_check()\n"
            "except Exception:\n"
            "    print('swallowed')")
        self.assertTrue(result.timed_out)
        self.assertNotIn("swallowed", result.output)

    def test_time_module_is_restored_after_timeout(self):
        before = time.sleep
        result = Executor(DummyBoard(), timeout_s=1).execute(
            "import time\ntime.sleep(30)")
        self.assertTrue(result.timed_out)
        self.assertIs(before, time.sleep)

    def test_explicit_timeout_overrides_default(self):
        result = Executor(DummyBoard(), timeout_s=30).execute(
            "for i in range(10 ** 9):\n    pass", timeout_s=1)
        self.assertTrue(result.timed_out)

    def test_positive_fractional_timeout_rounds_up(self):
        executor = Executor(DummyBoard(), timeout_s=20, max_timeout_s=300)
        self.assertEqual(1, executor._resolve_timeout(0.2))
        self.assertEqual(1, executor._resolve_timeout(0.9))
        self.assertEqual(2, executor._resolve_timeout(1.2))
        self.assertEqual(0, executor._resolve_timeout(0))
        self.assertEqual(20, executor._resolve_timeout(None))
        self.assertEqual(
            0, Executor(DummyBoard(), timeout_s=0)._resolve_timeout(None))
        result = Executor(DummyBoard()).execute(
            "for i in range(10 ** 9):\n    pass", timeout_s=0.2)
        self.assertTrue(result.timed_out)

    def test_deadline_range_keeps_range_behavior(self):
        result = Executor(DummyBoard(), timeout_s=5).execute(
            "print(len(range(5)))\nprint(list(range(3)))\nprint(3 in range(5))\n"
            "print(range(10)[2])")
        self.assertTrue(result.ok)
        self.assertIn("5", result.output)
        self.assertIn("[0, 1, 2]", result.output)
        self.assertIn("True", result.output)
        self.assertIn("2", result.output)

    def test_timeout_class_is_base_exception(self):
        self.assertTrue(issubclass(ExecutionTimeout, BaseException))
        self.assertFalse(issubclass(ExecutionTimeout, Exception))

    def test_instrument_source_skips_strings_comments_and_names(self):
        source = (
            "for i in range(3):\n"
            "    print('range( not code')\n"
            "# range(4)\n"
            "obj.range(1)\n"
            "my_range(2)\n"
            "arrange(3)\n"
            "x = range (5)\n"
        )
        result = instrument_source(source)
        self.assertIn("for i in __harness_range(3):", result)
        self.assertIn("'range( not code'", result)
        self.assertIn("# range(4)", result)
        self.assertIn("obj.range(1)", result)
        self.assertIn("my_range(2)", result)
        self.assertIn("arrange(3)", result)
        self.assertIn("x = __harness_range (5)", result)

    def test_string_containing_range_is_not_rewritten_at_runtime(self):
        result = Executor(DummyBoard(), timeout_s=5).execute(
            "print('range( literal')\nfor i in range(2):\n    print(i)")
        self.assertTrue(result.ok)
        self.assertIn("range( literal", result.output)
        self.assertIn("1", result.output)

    def test_instrument_source_skips_viper_and_native_functions(self):
        source = (
            "import micropython\n"
            "@micropython.viper\n"
            "def swap16(buf: ptr8, n: int):\n"
            "    for i in range(n):\n"
            "        buf[i] = 1\n"
            "\n"
            "@micropython.native\n"
            "def count(n: int) -> int:\n"
            "    total = 0\n"
            "    while total < n:\n"
            "        total += 1\n"
            "    return total\n"
            "\n"
            "for j in range(3):\n"
            "    pass\n"
            "while ready:\n"
            "    pass\n"
        )
        result = instrument_source(source)
        viper_body = result.split("@micropython.native")[0]
        self.assertIn("def swap16", viper_body)
        self.assertIn("for i in range(n):", viper_body)
        self.assertNotIn("__harness_range", viper_body)
        native_body = result.split("@micropython.native")[1].split("for j")[0]
        self.assertIn("while total < n:", native_body)
        self.assertNotIn("__harness_check", native_body)
        self.assertIn("for j in __harness_range(3):", result)
        self.assertIn("while __h_tick() or (ready):", result)

    def test_instrument_source_keeps_viper_block_with_strings(self):
        source = (
            "@micropython.viper\n"
            "def f(n: int):\n"
            "    '''doc\n"
            "zero\n"
            "    '''\n"
            "    # comment at column zero is still inside the block\n"
            "    for i in range(n):\n"
            "        pass\n"
            "\n"
            "for k in range(2):\n"
            "    pass\n"
        )
        result = instrument_source(source)
        self.assertNotIn("__harness_range(n)", result)
        self.assertIn("for k in __harness_range(2):", result)

    def test_instrument_source_handles_indented_viper_function(self):
        source = (
            "class Holder:\n"
            "    @micropython.viper\n"
            "    def f(self, n: int):\n"
            "        for i in range(n):\n"
            "            pass\n"
            "    def g(self):\n"
            "        for i in range(2):\n"
            "            pass\n"
        )
        result = instrument_source(source)
        first, second = result.split("def g")
        self.assertNotIn("__harness_range", first)
        self.assertIn("__harness_range(2)", second)

    def test_instrument_source_skips_bare_viper_imports(self):
        source = (
            "from micropython import viper, native as fast\n"
            "@viper\n"
            "def one(n: int):\n"
            "    for i in range(n):\n"
            "        pass\n"
            "@fast\n"
            "def two(n: int):\n"
            "    while n:\n"
            "        n -= 1\n"
            "for ok in range(2):\n"
            "    pass\n"
        )
        result = instrument_source(source)
        skipped = result.split("for ok")[0]
        self.assertNotIn("__harness_range", skipped)
        self.assertNotIn("__harness_check", skipped)
        self.assertIn("for ok in __harness_range(2):", result)

    def test_instrument_source_skips_parenthesised_viper_import(self):
        source = (
            "from micropython import (\n"
            "    viper,\n"
            ")\n"
            "@viper\n"
            "def f(n: int):\n"
            "    for i in range(n):\n"
            "        pass\n"
        )
        self.assertEqual(source, instrument_source(source))

    def test_instrument_source_bare_viper_needs_the_import(self):
        source = (
            "@viper\n"
            "def f(n: int):\n"
            "    for i in range(n):\n"
            "        pass\n"
        )
        self.assertIn("__harness_range(n)", instrument_source(source))

    def test_instrument_source_ignores_import_inside_string(self):
        source = (
            "doc = '''\n"
            "from micropython import viper\n"
            "'''\n"
            "@viper\n"
            "def f(n: int):\n"
            "    for i in range(n):\n"
            "        pass\n"
        )
        self.assertIn("__harness_range(n)", instrument_source(source))

    def test_instrument_source_leaves_other_decorators_alone(self):
        source = (
            "@staticmethod\n"
            "def f():\n"
            "    for i in range(2):\n"
            "        pass\n"
        )
        self.assertIn("__harness_range(2)", instrument_source(source))

    def test_instrument_source_makes_true_while_cooperative(self):
        self.assertIn(
            "while __h_tick() or True:",
            instrument_source("while True:\n    pass"),
        )
        self.assertIn(
            "while __h_tick() or (1):",
            instrument_source("while (1):\n    pass"),
        )
        self.assertIn(
            "while __h_tick() or (True and ready):",
            instrument_source("while True and ready:\n    pass"),
        )
        self.assertNotIn(
            "__h_tick",
            instrument_source("print('while True:')"),
        )

    def test_instrument_source_wraps_general_while_conditions(self):
        result = instrument_source("while flag:\n    pass")
        self.assertIn("while __h_tick() or (flag):", result)
        result = instrument_source(
            "while time.ticks_diff(time.ticks_ms(), started) < 100:\n"
            "    pass")
        self.assertIn(
            "while __h_tick() or "
            "(time.ticks_diff(time.ticks_ms(), started) < 100):", result)
        # Nested brackets, slices, dict literals and lambdas keep their colon.
        for source, expected in (
            ("while buf[i:j]:\n    pass",
             "while __h_tick() or (buf[i:j]):"),
            ("while {'x': 1}:\n    pass",
             "while __h_tick() or ({'x': 1}):"),
            ("while (lambda: 1)():\n    pass",
             "while __h_tick() or ((lambda: 1)()):"),
            ("while (a and\n       b):\n    pass",
             "while __h_tick() or ((a and\n       b)):"),
            ("while chunk := read():\n    pass",
             "while __h_tick() or (chunk := read()):"),
        ):
            self.assertIn(expected, instrument_source(source))
        # A while already using either checkpoint is not double-wrapped.
        for source in (
            "while __harness_check() or done:\n    pass",
            "while __h_tick() or done:\n    pass",
        ):
            self.assertEqual(source, instrument_source(source))
        # Strings and comments are untouched.
        self.assertNotIn(
            "__h_tick",
            instrument_source("print('while x:')\n# while x:\n"))
        self.assertNotIn(
            "__h_tick", instrument_source("mywhile = 1\n"))

    def test_general_while_loop_is_cooperative_without_explicit_check(self):
        result = Executor(DummyBoard(), timeout_s=1).execute(
            "import time\n"
            "started = time.monotonic()\n"
            "while time.monotonic() - started < 30:\n"
            "    pass\n")
        self.assertTrue(result.timed_out)
        self.assertIn("ExecutionTimeout", result.error)

    def test_general_while_loop_still_exits_normally(self):
        result = Executor(DummyBoard(), timeout_s=5).execute(
            "i = 0\n"
            "while i < 3:\n"
            "    print(i)\n"
            "    i += 1\n")
        self.assertTrue(result.ok, result.error)
        self.assertIn("0", result.output)
        self.assertIn("2", result.output)

    def test_true_while_times_out(self):
        result = Executor(DummyBoard(), timeout_s=1).execute(
            "while True:\n    pass")
        self.assertTrue(result.timed_out)
        self.assertIn("ExecutionTimeout", result.error)


class WorkspaceModuleCacheTests(unittest.TestCase):
    module_name = "harness_cache_probe"

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="harness-cache-")
        self.root = os.path.join(self.temp, "workspace")
        os.mkdir(self.root)
        self.path = os.path.join(self.root, self.module_name + ".py")
        self.patch = mock.patch.object(
            executor_module, "_WORKSPACE_ROOT", self.root)
        self.patch.start()
        executor_module._WORKSPACE_STAMPS.clear()
        sys.path.insert(0, self.root)
        self.cleanup_modules()

    def tearDown(self):
        self.cleanup_modules()
        executor_module._WORKSPACE_STAMPS.clear()
        try:
            sys.path.remove(self.root)
        except ValueError:
            pass
        self.patch.stop()
        shutil.rmtree(self.temp, ignore_errors=True)

    def cleanup_modules(self):
        for name in list(sys.modules):
            if name == self.module_name or name.startswith(
                    self.module_name + "."):
                sys.modules.pop(name, None)

    def write_module(self, version, stamp=None):
        # Include the version in an extra line so CPython's .pyc check (which
        # compares source size and second-resolution mtime) always invalidates.
        with open(self.path, "w") as handle:
            handle.write(
                "VERSION = %d\nSTATE = []\n# %s\n"
                % (version, "v%d" % version))
        if stamp is not None:
            os.utime(self.path, (stamp, stamp))
        importlib.invalidate_caches()

    def run_import(self):
        source = (
            "import {name}\n"
            "print('version', {name}.VERSION)\n"
            "{name}.STATE.append(1)\n"
            "print('state', len({name}.STATE))\n".format(name=self.module_name)
        )
        result = Executor(DummyBoard(), timeout_s=5).execute(source)
        self.assertTrue(result.ok, result.observation())
        lines = {
            line.split()[0]: line.split()[1]
            for line in result.output.strip().splitlines()
        }
        return int(lines["version"]), int(lines["state"])

    def test_rewritten_workspace_module_is_reimported(self):
        self.write_module(1)
        version, state = self.run_import()
        self.assertEqual(1, version)
        self.assertEqual(1, state)

        # The next execution re-imports only after the file changes. Force a
        # distinct mtime so the test is independent of filesystem timestamp
        # granularity.
        self.write_module(2, stamp=time.time() + 10)
        version, state = self.run_import()
        self.assertEqual(2, version)
        # A fresh module has fresh state.
        self.assertEqual(1, state)

        # An unchanged file keeps the cached module (and its state).
        version, state = self.run_import()
        self.assertEqual(2, version)
        self.assertEqual(2, state)

    def test_workspace_write_forgets_the_loaded_module_immediately(self):
        self.write_module(1)
        version, _ = self.run_import()
        self.assertEqual(1, version)
        self.assertIn(self.module_name, sys.modules)

        Workspace(self.temp).write(
            self.module_name + ".py",
            "VERSION = 3\nSTATE = []\n# edited through Workspace\n",
            overwrite=True)
        self.assertNotIn(self.module_name, sys.modules)

        version, _ = self.run_import()
        self.assertEqual(3, version)

    def test_deleted_workspace_module_is_dropped(self):
        self.write_module(1)
        self.run_import()
        self.assertIn(self.module_name, sys.modules)
        os.remove(self.path)
        self.assertTrue(executor_module.invalidate_workspace_modules())
        self.assertNotIn(self.module_name, sys.modules)

    def test_workspace_file_matching(self):
        with mock.patch.object(
                executor_module, "_WORKSPACE_ROOT", "/workspace"):
            self.assertEqual(
                "/workspace/tmp/driver.py",
                executor_module._workspace_file("workspace/tmp/driver.py"))
            self.assertEqual(
                "/workspace/tmp/driver.py",
                executor_module._workspace_file(
                    "/workspace/tmp/driver.py"))
            self.assertIsNone(
                executor_module._workspace_file("/harness/executor.py"))
            self.assertIsNone(
                executor_module._workspace_file("/workspace-other/x.py"))


if __name__ == "__main__":
    unittest.main()
