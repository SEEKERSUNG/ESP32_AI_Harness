import sys
import unittest
from unittest import mock

from harness.agent import (
    Agent,
    builtin_tool_prompt,
    native_tool_definitions,
    parse_tool_call,
)
from harness.clock import TimeSync, format_snapshot, snapshot
from tests.support import DummyBoard, DummyConfig


class MutableConfig(DummyConfig):
    def set(self, name, value, save=True):
        self.values[name] = value


class FakeRTC:
    def __init__(self, value):
        self.value = value

    def datetime(self, value=None):
        if value is not None:
            self.value = value
            return None
        return self.value


class FakeMachine:
    def __init__(self, value):
        self.rtc = FakeRTC(value)

    def RTC(self):
        return self.rtc


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def advance(self, milliseconds):
        self.now += milliseconds


class FakeNetwork:
    def __init__(self, connected=True, fail=False):
        self.connected = connected
        self.fail = fail
        self.age = None
        self.calls = []

    def status(self):
        return {
            "connected": self.connected,
            "sta_connected": self.connected,
        }

    def time_sync_age_ms(self):
        return self.age

    def sync_time(self, server="pool.ntp.org", force=False):
        self.calls.append((server, force))
        if self.fail:
            raise RuntimeError("network unavailable")
        self.age = 0
        return True


class StubLLM:
    supports_native_tools = False

    def complete(self, messages, **kwargs):
        return "ok"

    complete_stream = complete


class SnapshotTests(unittest.TestCase):
    def config(self, offset=480, **overrides):
        return DummyConfig(timezone_offset_minutes=offset, **overrides)

    def test_rtc_utc_is_converted_to_positive_offset(self):
        value = snapshot(self.config(480), (2026, 11, 26, 3, 2, 30, 0, 0))
        self.assertEqual("2026-11-26T02:30:00Z", value["utc"])
        self.assertEqual("2026-11-26T10:30:00+08:00", value["local"])
        self.assertEqual("Thursday", value["weekday"])
        self.assertEqual("UTC+08:00", value["timezone"])
        self.assertTrue(value["valid"])

    def test_negative_offset_crosses_to_previous_day(self):
        value = snapshot(self.config(-300), (2027, 1, 1, 3, 2, 0, 0, 0))
        self.assertEqual("2027-01-01T02:00:00Z", value["utc"])
        self.assertEqual("2026-12-31T21:00:00-05:00", value["local"])
        self.assertEqual("Thursday", value["weekday"])

    def test_positive_offset_crosses_to_next_year(self):
        value = snapshot(self.config(480), (2026, 12, 31, 3, 20, 0, 0, 0))
        self.assertEqual("2027-01-01T04:00:00+08:00", value["local"])
        self.assertEqual("Friday", value["weekday"])

    def test_leap_day_and_minute_rollover(self):
        value = snapshot(self.config(60), (2028, 2, 29, 3, 23, 30, 0, 0))
        self.assertEqual("2028-03-01T00:30:00+01:00", value["local"])
        self.assertEqual("Wednesday", value["weekday"])

    def test_invalid_rtc_is_reported_as_unsynchronized(self):
        value = snapshot(self.config(), (2021, 1, 1, 0, 0, 0, 0, 0))
        self.assertFalse(value["valid"])
        text = format_snapshot(value)
        self.assertIn("valid: false", text)
        self.assertIn("NTP may not have synchronized", text)

    def test_offset_is_bounded(self):
        value = snapshot(self.config(9999), (2026, 1, 1, 0, 0, 0, 0, 0))
        self.assertEqual(840, value["offset_minutes"])
        self.assertEqual("UTC+14:00", value["timezone"])

    def test_bad_rtc_value_raises(self):
        with self.assertRaises(Exception):
            snapshot(self.config(), (2026, 13, 1, 0, 0, 0, 0, 0))


class TimeSyncTests(unittest.TestCase):
    def test_initial_sync_then_interval_and_forced_refresh(self):
        network = FakeNetwork()
        sync = TimeSync(
            DummyConfig(ntp_sync_interval_s=21600), network,
            clock=FakeClock())
        self.assertTrue(sync.maybe_sync())
        self.assertEqual([("pool.ntp.org", False)], network.calls)

        network.age = 1000
        self.assertFalse(sync.maybe_sync())
        self.assertEqual(1, len(network.calls))

        network.age = 21600 * 1000
        self.assertTrue(sync.maybe_sync())
        self.assertEqual([("pool.ntp.org", False),
                          ("pool.ntp.org", True)], network.calls)

        network.age = 1000
        self.assertTrue(sync.maybe_sync(force=True))
        self.assertEqual(3, len(network.calls))

    def test_zero_interval_syncs_once_per_boot(self):
        network = FakeNetwork()
        sync = TimeSync(
            DummyConfig(ntp_sync_interval_s=0), network,
            clock=FakeClock())
        self.assertTrue(sync.maybe_sync())
        network.age = 10 ** 9
        self.assertFalse(sync.maybe_sync())
        self.assertEqual(1, len(network.calls))

    def test_failure_uses_delayed_retry(self):
        network = FakeNetwork(fail=True)
        clock = FakeClock()
        sync = TimeSync(DummyConfig(), network, clock=clock)
        self.assertFalse(sync.maybe_sync())
        self.assertIn("network unavailable", sync.last_error)
        self.assertEqual(1, len(network.calls))

        clock.advance(30000)
        self.assertFalse(sync.maybe_sync())
        self.assertEqual(1, len(network.calls))

        network.fail = False
        clock.advance(30000)
        self.assertTrue(sync.maybe_sync())
        self.assertEqual(2, len(network.calls))
        self.assertEqual("", sync.last_error)

    def test_disconnected_network_does_not_sync(self):
        network = FakeNetwork(connected=False)
        sync = TimeSync(DummyConfig(), network, clock=FakeClock())
        self.assertFalse(sync.maybe_sync())
        self.assertEqual([], network.calls)

    def test_custom_server_is_used(self):
        network = FakeNetwork()
        sync = TimeSync(
            DummyConfig(ntp_server="time.example.test"), network,
            clock=FakeClock())
        self.assertTrue(sync.maybe_sync())
        self.assertEqual("time.example.test", network.calls[0][0])


class TimeToolTests(unittest.TestCase):
    def test_time_tool_is_available_in_native_and_text_protocols(self):
        names = [tool["name"] for tool in native_tool_definitions(DummyConfig())]
        self.assertIn("__time", names)
        prompt = builtin_tool_prompt(DummyConfig())
        self.assertIn("__time", prompt)
        self.assertIn('"name":"__time"', prompt.replace(" ", ""))

        explanation, call = parse_tool_call(
            "Now:\n```tool\n"
            '{"name":"__time","args":{}}\n'
            "```"
        )
        self.assertEqual("__time", call["name"])
        self.assertEqual({}, call["args"])
        self.assertEqual("Now:", explanation)

    def test_time_tool_reads_rtc_and_applies_timezone(self):
        agent = Agent(
            DummyConfig(stream=False, timezone_offset_minutes=480),
            StubLLM(), DummyBoard())
        with mock.patch(
                "harness.clock.read_rtc",
                return_value=(2026, 11, 26, 2, 30, 0)):
            result = agent._execute_tool({"name": "__time", "args": {}})
        self.assertTrue(result.ok, result.error)
        self.assertIn("local: 2026-11-26 10:30:00 UTC+08:00 (Thursday)",
                      result.output)
        self.assertIn("utc: 2026-11-26T02:30:00Z", result.output)
        self.assertIn("valid: true", result.output)

    def test_time_tool_sets_rtc_from_local_time(self):
        machine = FakeMachine((2026, 11, 26, 4, 0, 0, 0, 0))
        agent = Agent(
            DummyConfig(stream=False, timezone_offset_minutes=480),
            StubLLM(), DummyBoard())
        with mock.patch.dict(sys.modules, {"machine": machine}):
            result = agent._execute_tool({
                "name": "__time",
                "args": {"action": "set", "at": "2026-11-27 08:00"},
            })
        self.assertTrue(result.ok, result.error)
        self.assertIn("Clock updated.", result.output)
        self.assertIn("utc: 2026-11-27T00:00:00Z", result.output)
        self.assertIn("local: 2026-11-27 08:00:00 UTC+08:00", result.output)
        self.assertEqual(2026, machine.rtc.value[0])
        self.assertEqual(11, machine.rtc.value[1])
        self.assertEqual(27, machine.rtc.value[2])
        self.assertEqual(0, machine.rtc.value[4])

    def test_time_tool_sets_rtc_from_epoch(self):
        machine = FakeMachine((2026, 11, 26, 4, 0, 0, 0, 0))
        agent = Agent(
            DummyConfig(stream=False, timezone_offset_minutes=480),
            StubLLM(), DummyBoard())
        epoch = 1795737600  # 2026-11-27 00:00:00 UTC
        with mock.patch.dict(sys.modules, {"machine": machine}):
            result = agent._execute_tool({
                "name": "__time",
                "args": {"action": "set", "epoch": epoch},
            })
        self.assertTrue(result.ok, result.error)
        self.assertIn("epoch: %s" % epoch, result.output)

    def test_time_tool_updates_timezone(self):
        machine = FakeMachine((2026, 11, 26, 3, 2, 30, 0, 0))
        config = MutableConfig(stream=False, timezone_offset_minutes=480)
        agent = Agent(config, StubLLM(), DummyBoard())
        with mock.patch.dict(sys.modules, {"machine": machine}):
            result = agent._execute_tool({
                "name": "__time",
                "args": {
                    "action": "timezone",
                    "timezone_offset_minutes": 0,
                },
            })
        self.assertTrue(result.ok, result.error)
        self.assertEqual(0, config.get("timezone_offset_minutes"))
        self.assertIn("Timezone updated to UTC+00:00", result.output)
        self.assertIn("local: 2026-11-26 02:30:00 UTC+00:00", result.output)

    def test_time_tool_rejects_invalid_time_and_offset(self):
        machine = FakeMachine((2026, 11, 26, 2, 30, 0, 0, 0))
        agent = Agent(
            MutableConfig(stream=False), StubLLM(), DummyBoard())
        with mock.patch.dict(sys.modules, {"machine": machine}):
            result = agent._execute_tool({
                "name": "__time",
                "args": {"action": "set", "at": "not-a-date"},
            })
            self.assertFalse(result.ok)
            self.assertIn("YYYY-MM-DD", result.error)
            result = agent._execute_tool({
                "name": "__time",
                "args": {
                    "action": "timezone",
                    "timezone_offset_minutes": 9999,
                },
            })
            self.assertFalse(result.ok)
            self.assertIn("between", result.error)
            result = agent._execute_tool({
                "name": "__time",
                "args": {"action": "set"},
            })
            self.assertFalse(result.ok)
            self.assertIn("requires at or epoch", result.error)


if __name__ == "__main__":
    unittest.main()
