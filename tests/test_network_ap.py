import types
import unittest
from unittest import mock

from harness.device import Network


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, delta):
        self.value += delta


class FakeWLAN:
    def __init__(self, interface):
        self.interface = interface
        self._active = False
        self._connected = False
        self._essid = ""
        self._pending_essid = ""
        self._status_polls_after_connect = 0
        self._ip = "192.168.4.1" if interface == "ap" else "10.0.0.50"
        self.connect_ok = True
        self.fail_status = 203
        self.scanning_status = 201
        self.connect_after_status_calls = 0
        self.status_value = 1000
        self.internal_state_errors = 0
        self.disconnect_keeps_connecting = False
        self.essid_error = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.config_calls = []

    def active(self, value=None):
        if value is None:
            return self._active
        self._active = value
        return None

    def isconnected(self):
        return self._connected

    def connect(self, ssid, password):
        self.connect_calls += 1
        self._essid = ssid
        self._pending_essid = ssid
        if self.internal_state_errors:
            self.internal_state_errors -= 1
            self.status_value = 1001
            raise OSError("Wifi Internal State Error")
        if self.connect_after_status_calls:
            self._status_polls_after_connect = self.connect_after_status_calls
            self.status_value = self.scanning_status
            return
        if self.connect_ok:
            self._connected = True
            self.status_value = 1010
        else:
            self.status_value = self.fail_status

    def status(self):
        if self._status_polls_after_connect:
            self._status_polls_after_connect -= 1
            if self._status_polls_after_connect == 0:
                self._connected = True
                self._essid = self._pending_essid
                self.status_value = 1010
        return self.status_value

    def disconnect(self):
        self.disconnect_calls += 1
        self._connected = False
        self._status_polls_after_connect = 0
        self.status_value = (
            1001 if self.disconnect_keeps_connecting else 1000
        )

    def config(self, key=None, **kwargs):
        if kwargs:
            self.config_calls.append(kwargs)
            if "essid" in kwargs:
                self._essid = kwargs["essid"]
            return None
        if key == "essid":
            if self.essid_error:
                raise OSError("essid unavailable")
            return self._essid
        return None

    def ifconfig(self, value=None):
        if value is not None:
            self._ip = value[0]
            return None
        return (self._ip, "255.255.255.0", self._ip, self._ip)


class FakeRadios:
    def __init__(self):
        self.radios = {}

    def __call__(self, interface):
        if interface not in self.radios:
            self.radios[interface] = FakeWLAN(interface)
        return self.radios[interface]


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.radios = FakeRadios()
        self.network = Network(
            feed=lambda: None,
            wlan_factory=self.radios,
            unique_id=lambda: b"\xaa\xbb\xcc\xdd",
            auth_mode=4,
            clock=self.clock,
        )
        self.sta = self.radios("sta")
        self.ap = self.radios("ap")
        self.sleeper = lambda ms: self.clock.advance(ms)

    def radio(self, interface):
        return self.radios(interface)

    def test_ap_ssid_uses_mac_suffix(self):
        self.assertEqual("ESP32-Harness-CCDD", self.network.ap_ssid())
        self.assertEqual("X-CCDD", self.network.ap_ssid("X"))
        self.assertLessEqual(len(self.network.ap_ssid("x" * 40)), 32)

    def test_start_and_stop_ap(self):
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            status = self.network.start_ap("ESP32-Harness-CCDD", "esp32setup", channel=6)
        self.assertTrue(status["ap_active"])
        self.assertEqual("ap", status["mode"])
        self.assertEqual("192.168.4.1", status["ap_ip"])
        self.assertEqual("ESP32-Harness-CCDD", status["ap_ssid"])
        self.assertEqual("", status["ip"])
        self.assertEqual("", status["sta_ip"])
        self.assertEqual(4, self.ap.config_calls[-1]["authmode"])
        self.assertEqual(6, self.ap.config_calls[-1]["channel"])
        with self.assertRaises(RuntimeError):
            self.network.start_ap("", "esp32setup")
        with self.assertRaises(RuntimeError):
            self.network.start_ap("AP", "short")
        status = self.network.stop_ap()
        self.assertFalse(status["ap_active"])
        self.assertEqual("off", status["mode"])

    def test_connect_reports_sta_and_grace(self):
        self.network.start_ap("AP", "esp32setup")
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            status = self.network.connect(
                "home", "secret", timeout=0, grace_ms=5000
            )
        self.assertTrue(status["connected"])
        self.assertTrue(status["sta_connected"])
        self.assertEqual("10.0.0.50", status["sta_ip"])
        self.assertEqual("home", status["sta_ssid"])
        self.assertEqual("apsta", status["mode"])
        self.assertEqual(5, status["ap_off_in_s"])
        self.assertEqual(1, self.sta.connect_calls)

    def test_connect_failure_records_error(self):
        self.sta.connect_ok = False
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            with self.assertRaises(RuntimeError):
                self.network.connect("home", "bad", timeout=-1)
        status = self.network.status()
        self.assertFalse(status["connected"])
        self.assertIn("Wi-Fi connection timed out", status["last_error"])

    @staticmethod
    def fake_network_module():
        return types.SimpleNamespace(
            STAT_WRONG_PASSWORD=202, STAT_NO_AP_FOUND=201,
            STAT_CONNECT_FAIL=203, STAT_IDLE=1000,
            STAT_CONNECTING=1001, STAT_GOT_IP=1010,
        )

    def test_connect_reports_wrong_password_after_settle(self):
        self.sta.connect_ok = False
        self.sta.fail_status = 202
        start = self.clock.value
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", self.sleeper):
                with self.assertRaises(RuntimeError) as raised:
                    self.network.connect("home", "bad", timeout=25)
        self.assertIn("wrong password", str(raised.exception))
        self.assertGreaterEqual(self.clock.value - start, 5000)
        self.assertLess(self.clock.value - start, 6000)

    def test_connect_reports_missing_ap_after_settle(self):
        self.sta.connect_ok = False
        self.sta.fail_status = 201
        start = self.clock.value
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", self.sleeper):
                with self.assertRaises(RuntimeError) as raised:
                    self.network.connect("5g-only", "pw", timeout=25)
        self.assertIn("AP not found", str(raised.exception))
        self.assertIn("2.4 GHz", str(raised.exception))
        self.assertGreaterEqual(self.clock.value - start, 5000)
        self.assertLess(self.clock.value - start, 6000)

    def test_connect_ignores_transient_no_ap_found_while_scanning(self):
        # ESP-IDF reports the previous disconnect reason (201) while the new
        # attempt is still scanning. It must not be treated as final until the
        # settle window has elapsed.
        self.sta.connect_after_status_calls = 15
        self.sta.scanning_status = 201
        start = self.clock.value
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", self.sleeper):
                status = self.network.connect("home", "pw", timeout=25)
        self.assertTrue(status["connected"])
        self.assertEqual("home", self.sta.config("essid"))
        self.assertEqual(1, self.sta.connect_calls)
        self.assertLess(self.clock.value - start, 5000)

    def test_connect_clears_inflight_attempt_before_connecting(self):
        self.sta.status_value = 1001
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", lambda _ms: None):
                status = self.network.connect("home", "pw", timeout=0)
        self.assertTrue(status["connected"])
        self.assertGreaterEqual(self.sta.disconnect_calls, 1)
        self.assertEqual("home", self.sta.config("essid"))

    def test_connect_retries_after_internal_state_error(self):
        self.sta.internal_state_errors = 1
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", lambda _ms: None):
                status = self.network.connect("home", "pw", timeout=0)
        self.assertTrue(status["connected"])
        self.assertEqual(2, self.sta.connect_calls)
        self.assertGreaterEqual(self.sta.disconnect_calls, 1)

    def test_connect_reports_clear_error_when_previous_attempt_will_not_clear(self):
        self.sta.status_value = 1001
        self.sta.disconnect_keeps_connecting = True
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", self.sleeper):
                with self.assertRaises(RuntimeError) as raised:
                    self.network.connect("home", "pw", timeout=0)
        self.assertIn("previous Wi-Fi attempt", str(raised.exception))
        self.assertEqual(0, self.sta.connect_calls)

    def test_connect_reports_clear_error_when_retry_hits_internal_state(self):
        self.sta.internal_state_errors = 2
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", self.sleeper):
                with self.assertRaises(RuntimeError) as raised:
                    self.network.connect("home", "pw", timeout=0)
        self.assertIn("still busy", str(raised.exception))
        self.assertEqual(2, self.sta.connect_calls)

    def test_connect_reports_clear_error_when_retry_cleanup_will_not_clear(self):
        self.sta.internal_state_errors = 1
        self.sta.disconnect_keeps_connecting = True
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", self.sleeper):
                with self.assertRaises(RuntimeError) as raised:
                    self.network.connect("home", "pw", timeout=0)
        self.assertIn("previous Wi-Fi attempt", str(raised.exception))
        self.assertEqual(1, self.sta.connect_calls)

    def test_unrelated_error_does_not_disconnect_healthy_link(self):
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("home", "pw", timeout=0)
        self.assertTrue(self.sta.isconnected())
        before = self.sta.disconnect_calls

        def boom():
            raise RuntimeError("feed failed")

        self.network.feed = boom
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            with self.assertRaises(RuntimeError) as raised:
                self.network.connect("home", "pw", timeout=0)
        self.assertIn("feed failed", str(raised.exception))
        self.assertEqual(before, self.sta.disconnect_calls)
        self.assertTrue(self.sta.isconnected())

    def test_connect_timeout_stops_inflight_attempt(self):
        self.sta.connect_ok = False
        self.sta.fail_status = 203
        with mock.patch("harness.device._network", self.fake_network_module()):
            with mock.patch("harness.device._sleep_ms", lambda _ms: None):
                with self.assertRaises(RuntimeError):
                    self.network.connect("home", "bad", timeout=-1)
        self.assertGreaterEqual(self.sta.disconnect_calls, 1)
        self.assertIn("timed out", self.network.status()["last_error"])

    def test_connect_force_disconnects_existing_sta(self):
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("one", "pw", timeout=0)
            self.network.connect("two", "pw", timeout=0, force=True)
        self.assertEqual(1, self.sta.disconnect_calls)
        self.assertEqual("two", self.sta.config("essid"))

    def test_maintain_closes_ap_after_grace_when_password_set(self):
        self.network.start_ap("AP", "esp32setup")
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("home", "pw", timeout=0, grace_ms=5000)
        self.clock.advance(6000)
        status = self.network.maintain(
            "home", "pw", "AP", "esp32setup",
            grace_ms=5000, password_set=True, enabled=True,
        )
        self.assertFalse(status["ap_active"])
        self.assertTrue(status["connected"])

    def test_maintain_keeps_ap_without_admin_password(self):
        self.network.start_ap("AP", "esp32setup")
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("home", "pw", timeout=0, grace_ms=1000)
        self.clock.advance(5000)
        status = self.network.maintain(
            "home", "pw", "AP", "esp32setup",
            grace_ms=1000, password_set=False, enabled=True,
        )
        self.assertTrue(status["ap_active"])

    def test_maintain_starts_ap_after_lost_link(self):
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("home", "pw", timeout=0, grace_ms=1000)
        self.clock.advance(2000)
        self.sta._connected = False
        self.clock.advance(31000)
        status = self.network.maintain(
            "home", "pw", "AP", "esp32setup",
            grace_ms=1000, password_set=True, enabled=True,
            lost_grace_ms=30000, reconnect_interval=60000, timeout=-1,
        )
        self.assertTrue(status["ap_active"])
        self.assertFalse(status["connected"])

    def test_maintain_retries_sta(self):
        self.network.start_ap("AP", "esp32setup")
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("home", "pw", timeout=0, grace_ms=1000)
        self.sta._connected = False
        self.clock.advance(2000)
        self.network._last_attempt = None
        status = self.network.maintain(
            "home", "pw", "AP", "esp32setup",
            grace_ms=1000, password_set=True, enabled=True,
            lost_grace_ms=30000, reconnect_interval=60000, timeout=0,
        )
        self.assertTrue(status["sta_connected"])
        self.assertTrue(status["ap_active"])
        self.assertEqual(2, self.sta.connect_calls)

    def test_repeated_connect_does_not_extend_ap_grace(self):
        self.network.start_ap("AP", "esp32setup")
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("home", "pw", timeout=0, grace_ms=300000)
            first_deadline = self.network._ap_off_at
            self.clock.advance(1000)
            status = self.network.connect("home", "pw", timeout=0, grace_ms=300000)
        self.assertEqual(first_deadline, self.network._ap_off_at)
        self.assertEqual(299, status["ap_off_in_s"])
        self.clock.advance(299000)
        status = self.network.maintain(
            "home", "pw", "AP", "esp32setup",
            grace_ms=300000, password_set=True, enabled=True,
        )
        self.assertFalse(status["ap_active"])

    def test_real_sta_recovery_starts_a_new_ap_grace(self):
        self.network.start_ap("AP", "esp32setup")
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("home", "pw", timeout=0, grace_ms=5000)
            first_deadline = self.network._ap_off_at
            self.clock.advance(1000)
            self.sta._connected = False
            self.network.connect("home", "pw", timeout=0, grace_ms=5000)
        self.assertGreater(self.network._ap_off_at, first_deadline)
        self.assertEqual(self.clock.value + 5000, self.network._ap_off_at)

    def test_unknown_essid_reconnect_does_not_extend_ap_grace(self):
        self.network.start_ap("AP", "esp32setup")
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("home", "pw", timeout=0, grace_ms=300000)
            first_deadline = self.network._ap_off_at
            self.clock.advance(1000)
            self.sta.essid_error = True
            self.network.connect("home", "pw", timeout=0, grace_ms=300000)
        self.assertEqual(1, self.sta.disconnect_calls)
        self.assertEqual(2, self.sta.connect_calls)
        self.assertEqual(first_deadline, self.network._ap_off_at)

    def test_unknown_current_essid_forces_explicit_target_reconnect(self):
        self.sta._connected = True
        self.sta._essid = "old-network"
        self.sta.essid_error = True
        with mock.patch("harness.device._sleep_ms", lambda _ms: None):
            self.network.connect("new-network", "pw", timeout=0)
        self.assertEqual(1, self.sta.disconnect_calls)
        self.assertEqual(1, self.sta.connect_calls)
        self.assertEqual("new-network", self.sta._essid)

    def test_sync_time_once_per_boot_and_force_refresh(self):
        calls = []

        def settime():
            calls.append(1)

        ntptime = types.SimpleNamespace(settime=settime)
        with mock.patch.dict("sys.modules", {"ntptime": ntptime}):
            self.assertTrue(self.network.sync_time("time.example.test"))
            self.assertEqual("time.example.test", ntptime.host)
            self.assertEqual(1, len(calls))
            self.assertEqual(0, self.network.time_sync_age_ms())
            self.assertTrue(self.network.status()["time_synced"])

            self.clock.advance(5000)
            self.assertFalse(self.network.sync_time("time.example.test"))
            self.assertEqual(1, len(calls))
            self.assertEqual(5000, self.network.time_sync_age_ms())

            self.assertTrue(self.network.sync_time(
                "time.example.test", force=True))
            self.assertEqual(2, len(calls))
            self.assertEqual(0, self.network.time_sync_age_ms())

    def test_sync_time_reports_ntp_failure(self):
        def fail():
            raise OSError("no NTP reply")

        with mock.patch.dict(
                "sys.modules",
                {"ntptime": types.SimpleNamespace(settime=fail)}), \
                mock.patch("harness.device._sleep_ms", lambda _ms: None):
            with self.assertRaises(RuntimeError) as raised:
                self.network.sync_time()
        self.assertIn("no NTP reply", str(raised.exception))
        self.assertFalse(self.network.status()["time_synced"])
        self.assertIsNone(self.network.time_sync_age_ms())

    def test_status_is_cached_while_network_lock_is_busy(self):
        self.network.start_ap("AP", "esp32setup")
        self.network._lock.acquire()
        try:
            status = self.network.status()
        finally:
            self.network._lock.release()
        self.assertEqual("ap", status["mode"])
        self.assertTrue(status["ap_active"])


if __name__ == "__main__":
    unittest.main()
