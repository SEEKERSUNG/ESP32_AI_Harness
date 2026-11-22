import sys
import time
import types
import unittest
from unittest import mock

from harness.executor import (
    BufferWriter,
    ExecutionTimeout,
    Executor,
    instrument_source,
)
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

    def test_instrument_source_makes_true_while_cooperative(self):
        self.assertIn(
            "while __harness_check() or True:",
            instrument_source("while True:\n    pass"),
        )
        self.assertIn(
            "while __harness_check() or (1):",
            instrument_source("while (1):\n    pass"),
        )
        self.assertNotIn(
            "__harness_check",
            instrument_source("while True and ready:\n    pass"),
        )
        self.assertNotIn(
            "__harness_check",
            instrument_source("print('while True:')"),
        )

    def test_true_while_times_out(self):
        result = Executor(DummyBoard(), timeout_s=1).execute(
            "while True:\n    pass")
        self.assertTrue(result.timed_out)
        self.assertIn("ExecutionTimeout", result.error)


if __name__ == "__main__":
    unittest.main()
