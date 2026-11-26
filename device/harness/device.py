import gc
import os
import sys
import time

try:
    import _thread
except ImportError:
    _thread = None

try:
    import network as _network
except ImportError:
    _network = None


# ESP-IDF keeps the previous disconnect reason (for example
# STAT_NO_AP_FOUND) while a new attempt is still scanning. Treating that
# stale value as final aborts a connection that would otherwise succeed.
FAST_FAIL_SETTLE_MS = 5000


def _new_lock():
    if _thread is None:
        return None
    return _thread.allocate_lock()


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


def ticks_add(value, delta):
    if hasattr(time, "ticks_add"):
        return time.ticks_add(value, delta)
    return value + delta


def ticks_diff(later, earlier):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(later, earlier)
    return later - earlier


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
    else:
        time.sleep(value / 1000.0)


class Board:
    def info(self):
        gc.collect()
        info = {
            "platform": sys.platform,
            "implementation": sys.implementation.name,
            "version": ".".join(str(part) for part in sys.implementation.version[:3]),
        }
        try:
            info["heap_free"] = gc.mem_free()
            info["heap_allocated"] = gc.mem_alloc()
        except AttributeError:
            pass
        try:
            stats = os.statvfs("/")
            info["filesystem_free"] = stats[0] * stats[3]
        except (OSError, AttributeError):
            pass
        try:
            import esp
            info["flash_size"] = esp.flash_size()
        except (ImportError, AttributeError):
            pass
        try:
            import machine
            info["cpu_freq"] = machine.freq()
            info["reset_cause"] = machine.reset_cause()
            info["unique_id"] = "".join(
                "%02X" % value for value in machine.unique_id()
            )
        except (ImportError, AttributeError):
            pass
        return info

    def capabilities(self):
        return (
            "ESP32-S3 N16R8 running MicroPython 1.29: 16 MB flash, 8 MB PSRAM, "
            "dual-core 240 MHz. Generated code runs directly on MicroPython and "
            "may import machine, esp, esp32, neopixel and framebuf. Use the "
            "standard MicroPython hardware classes: machine.Pin, machine.SPI or "
            "machine.SPI (hardware id 1 or 2) or machine.SoftSPI, "
            "machine.I2C or machine.SoftI2C, machine.UART, "
            "machine.PWM, machine.ADC, machine.TouchPad, machine.RTC and "
            "machine.time_pulse_us. There is no api object, no peripheral "
            "allowlist and no /peripherals prerequisite. Use the actual wiring "
            "and pins supplied by the user; CS/DC/RST/BL are ordinary GPIO "
            "pins. Avoid GPIO26-37 (flash/PSRAM), GPIO43/44 (console UART), "
            "GPIO19/20 (native USB) and strapping pins 0/3/45/46 unless the "
            "user explicitly knows the risk. device.info() is injected for "
            "board information; import machine/esp/esp32 directly for live "
            "values. Typical calls: "
            "from machine import Pin, SPI, I2C, UART, PWM, ADC, TouchPad, RTC, "
            "time_pulse_us; p=Pin(4, Pin.OUT); p.value(1); "
            "spi=SPI(1, baudrate=20000000, polarity=0, phase=0, sck=Pin(12), "
            "mosi=Pin(11), miso=Pin(13)); spi.write(b'\\x01'); "
            "i2c=I2C(0, scl=Pin(9), sda=Pin(8), freq=400000); print(i2c.scan()); "
            "uart=UART(1, baudrate=115200, tx=Pin(17), rx=Pin(18)); "
            "pwm=PWM(Pin(13)); pwm.freq(1000); pwm.duty_u16(32768); "
            "adc=ADC(Pin(1)); adc.atten(ADC.ATTN_11DB); print(adc.read_u16()); "
            "touch=TouchPad(Pin(4)); print(touch.read()); "
            "rtc=RTC(); print(rtc.datetime()); "
            "print(time_pulse_us(Pin(5, Pin.IN), 1, 30000)); "
            "from neopixel import NeoPixel; np=NeoPixel(Pin(48), 1); "
            "np[0]=(0, 32, 0); np.write(). For write-only SPI omit miso "
            "instead of passing None. "
            "Use time.sleep_ms()/sleep_us() and time.ticks_ms()/ticks_diff() "
            "for timing; framebuf.FrameBuffer(buffer, width, height, format) "
            "is available for display buffers. Do not erase files, reset the "
            "board or change harness/configuration files unless the user "
            "explicitly asks. Use print() for observations."
        )


class Network:
    def __init__(self, feed=None, wlan_factory=None, unique_id=None,
                 auth_mode=None, clock=None):
        self.feed = feed or (lambda: None)
        self._time_synced = False
        self._time_synced_ms = None
        self._time_syncing = False
        self._wlan_factory = wlan_factory
        self._unique_id = unique_id
        self._auth_mode = auth_mode
        self._clock = clock or _ticks_ms
        self._lock = _new_lock()
        self._status_cache = {
            "connected": False, "ip": "", "time_synced": False,
            "mode": "off", "sta_connected": False, "sta_ip": "",
            "sta_ssid": "", "ap_active": False, "ap_ip": "", "ap_ssid": "",
        }
        self._ap_ssid = ""
        self._ap_password = ""
        self._ap_off_at = None
        self._last_sta_seen = self._clock()
        self._last_attempt = None
        self._wifi_configured = False
        self._connecting = False
        self._last_error = ""

    def _wlan(self, interface="sta"):
        if self._wlan_factory is not None:
            return self._wlan_factory(interface)
        import network
        if interface == "ap":
            return network.WLAN(network.AP_IF)
        return network.WLAN(network.STA_IF)

    def _auth(self):
        if self._auth_mode is not None:
            return self._auth_mode
        try:
            import network
            return network.AUTH_WPA_WPA2_PSK
        except (ImportError, AttributeError):
            return 3

    def _machine_id(self):
        if self._unique_id is not None:
            return self._unique_id()
        try:
            import machine
            return machine.unique_id()
        except Exception:
            return b"SETUP"

    def _acquire(self, blocking=True):
        if self._lock is None:
            return False
        if blocking:
            self._lock.acquire()
            return True
        return self._lock.acquire(False)

    def _release(self, acquired):
        if acquired:
            self._lock.release()

    def _ssid_text(self, value):
        if isinstance(value, bytes):
            try:
                return value.decode()
            except ValueError:
                return ""
        return value or ""

    def _wlan_reason(self, wlan):
        """Return (constant name, human text) for WLAN.status(), if available."""
        try:
            code = wlan.status()
        except (OSError, AttributeError, ValueError):
            return None, ""
        if _network is None:
            return None, "status=%s" % code
        for name, text in (
            ("STAT_WRONG_PASSWORD", "wrong password"),
            ("STAT_NO_AP_FOUND",
             "AP not found (check SSID, 2.4 GHz band and signal)"),
            ("STAT_CONNECT_FAIL", "connect failed"),
            ("STAT_IDLE", "idle"),
            ("STAT_CONNECTING", "still connecting"),
            ("STAT_GOT_IP", "got IP"),
        ):
            value = getattr(_network, name, None)
            if value is not None and code == value:
                return name, text
        return None, "status=%s" % code

    def _query_status(self):
        sta = self._wlan("sta")
        ap = self._wlan("ap")
        sta_connected = bool(sta.isconnected())
        sta_ip = ""
        sta_ssid = ""
        if sta_connected:
            try:
                sta_ip = sta.ifconfig()[0]
            except (OSError, ValueError):
                sta_ip = ""
            try:
                sta_ssid = self._ssid_text(sta.config("essid"))
            except (OSError, ValueError):
                sta_ssid = ""
        ap_active = False
        ap_ip = ""
        ap_ssid = ""
        try:
            ap_active = bool(ap.active())
        except (OSError, ValueError):
            pass
        if ap_active:
            try:
                ap_ip = ap.ifconfig()[0]
            except (OSError, ValueError):
                ap_ip = "192.168.4.1"
            try:
                ap_ssid = self._ssid_text(ap.config("essid")) or self._ap_ssid
            except (OSError, ValueError):
                ap_ssid = self._ap_ssid
        if sta_connected:
            mode = "apsta" if ap_active else "sta"
        elif ap_active:
            mode = "ap"
        else:
            mode = "off"
        if sta_connected:
            self._last_sta_seen = self._clock()
        ap_off_in_s = 0
        if ap_active and self._ap_off_at is not None:
            remaining = ticks_diff(self._ap_off_at, self._clock())
            if remaining > 0:
                ap_off_in_s = (remaining + 999) // 1000
        status = {
            "connected": sta_connected,
            "ip": sta_ip,
            "time_synced": self._time_synced,
            "mode": mode,
            "sta_connected": sta_connected,
            "sta_ip": sta_ip,
            "sta_ssid": sta_ssid,
            "ap_active": ap_active,
            "ap_ip": ap_ip,
            "ap_ssid": ap_ssid or self._ap_ssid,
            "ap_off_in_s": ap_off_in_s,
            "connecting": self._connecting,
            "last_error": self._last_error,
        }
        self._status_cache = status
        return status

    def status(self):
        acquired = self._acquire(False)
        if not acquired:
            cached = dict(self._status_cache)
            cached["connecting"] = self._connecting
            return cached
        try:
            return self._query_status()
        except Exception as exc:
            self._last_error = str(exc)
            cached = dict(self._status_cache)
            cached["connecting"] = self._connecting
            cached["last_error"] = self._last_error
            return cached
        finally:
            self._release(acquired)

    def ap_ssid(self, prefix="ESP32-Harness"):
        prefix = (prefix or "ESP32-Harness").strip() or "ESP32-Harness"
        unique = self._machine_id()
        try:
            suffix = "".join("%02X" % value for value in unique[-2:])
        except (TypeError, ValueError):
            suffix = "SETUP"
        value = prefix + "-" + suffix
        return value[:32]

    def start_ap(self, ssid, password, ip="192.168.4.1", channel=6):
        if not ssid:
            raise RuntimeError("AP SSID is empty")
        if not isinstance(password, str) or len(password) < 8 or len(password) > 63:
            raise RuntimeError("AP password must be 8-63 characters")
        acquired = self._acquire()
        try:
            ap = self._wlan("ap")
            ap.active(True)
            config = {
                "essid": ssid,
                "password": password,
                "authmode": self._auth(),
            }
            try:
                config["channel"] = int(channel)
            except (TypeError, ValueError):
                pass
            try:
                ap.config(**config)
            except TypeError:
                config.pop("channel", None)
                ap.config(**config)
            ap.ifconfig((ip, "255.255.255.0", ip, ip))
            self._ap_ssid = ssid
            self._ap_password = password
            self._ap_off_at = None
            self._last_error = ""
            self._status_cache = self._query_status()
            return self._status_cache
        except Exception as exc:
            self._last_error = "AP start failed: %s" % exc
            raise
        finally:
            self._release(acquired)

    def stop_ap(self):
        acquired = self._acquire()
        try:
            ap = self._wlan("ap")
            try:
                ap.active(False)
            except (OSError, ValueError):
                pass
            self._ap_off_at = None
            self._ap_ssid = ""
            self._ap_password = ""
            self._status_cache = self._query_status()
            return self._status_cache
        finally:
            self._release(acquired)

    def grace_ap(self, grace_ms):
        acquired = self._acquire()
        try:
            if self._ap_ssid:
                self._ap_off_at = ticks_add(self._clock(), max(0, int(grace_ms)))
        finally:
            self._release(acquired)

    def _sta_status_code(self, wlan):
        try:
            return wlan.status()
        except (OSError, AttributeError, ValueError):
            return None

    def _status_is_connecting(self, code):
        if _network is None or code is None:
            return False
        expected = getattr(_network, "STAT_CONNECTING", None)
        return expected is not None and code == expected

    def _disconnect_sta(self, wlan, wait_ms=2000):
        """Stop any in-flight connection attempt and wait for the driver."""
        try:
            wlan.disconnect()
        except (OSError, ValueError):
            pass
        start = self._clock()
        while ticks_diff(self._clock(), start) < wait_ms:
            if (not wlan.isconnected()
                    and not self._status_is_connecting(
                        self._sta_status_code(wlan))):
                return True
            self.feed()
            _sleep_ms(100)
        return False

    @staticmethod
    def _connect_state_error(exc):
        text = str(exc).lower()
        return ("internal state" in text
                or "cannot set config" in text
                or "sta is connecting" in text)

    def connect(self, ssid, password, timeout=25, force=False, grace_ms=None):
        if not ssid:
            raise RuntimeError("Wi-Fi SSID is empty")
        acquired = self._acquire()
        self._connecting = True
        self._wifi_configured = True
        self._last_attempt = self._clock()
        attempt_started = False
        try:
            wlan = self._wlan("sta")
            wlan.active(True)
            started_connected = bool(wlan.isconnected())
            established = False
            current_ssid = ""
            try:
                current_ssid = self._ssid_text(wlan.config("essid"))
            except (OSError, ValueError):
                pass
            # An unknown SSID cannot prove that the current connection is the
            # requested one. Reconnect explicitly instead of reporting the old
            # access point as a successful connection to the configured target.
            if wlan.isconnected() and (force or current_ssid != ssid):
                if not self._disconnect_sta(wlan):
                    raise RuntimeError("Timed out while disconnecting old Wi-Fi")
            if not wlan.isconnected():
                # ESP-IDF may still be finishing a previous attempt. Calling
                # connect() in that state raises "Wifi Internal State Error"
                # ("sta is connecting, cannot set config"), so stop it first.
                if self._status_is_connecting(self._sta_status_code(wlan)):
                    if not self._disconnect_sta(wlan):
                        raise RuntimeError(
                            "Timed out while clearing the previous Wi-Fi attempt")
                established = True
                attempt_started = True
                try:
                    wlan.connect(ssid, password)
                except OSError as exc:
                    if not self._connect_state_error(exc):
                        raise
                    if not self._disconnect_sta(wlan):
                        raise RuntimeError(
                            "Timed out while clearing the previous Wi-Fi attempt")
                    try:
                        wlan.connect(ssid, password)
                    except OSError as retry_exc:
                        if not self._connect_state_error(retry_exc):
                            raise
                        raise RuntimeError(
                            "Wi-Fi driver is still busy: %s" % retry_exc)
                start = self._clock()
                while not wlan.isconnected():
                    self.feed()
                    if ticks_diff(self._clock(), start) > timeout * 1000:
                        _, reason = self._wlan_reason(wlan)
                        raise RuntimeError(
                            "Wi-Fi connection timed out%s" % (
                                " (%s)" % reason if reason else "")
                        )
                    _sleep_ms(200)
                    status_name, reason = self._wlan_reason(wlan)
                    fast_fail = status_name in (
                        "STAT_WRONG_PASSWORD", "STAT_NO_AP_FOUND")
                    if (fast_fail and ticks_diff(
                            self._clock(), start) >= FAST_FAIL_SETTLE_MS):
                        raise RuntimeError(
                            "Wi-Fi connection failed: %s" % reason
                        )
                connected_ssid = ""
                try:
                    connected_ssid = self._ssid_text(wlan.config("essid"))
                except (OSError, ValueError):
                    pass
                if connected_ssid and connected_ssid != ssid:
                    self._disconnect_sta(wlan)
                    raise RuntimeError(
                        "Wi-Fi connected to unexpected SSID: %s"
                        % connected_ssid
                    )
            self.feed()
            self._last_error = ""
            self._last_sta_seen = self._clock()
            changed_target = bool(current_ssid and current_ssid != ssid)
            restart_grace = established and (
                not started_connected or force or changed_target
            )
            if (self._ap_ssid and grace_ms is not None
                    and (self._ap_off_at is None or restart_grace)):
                # Repeated prepare_cloud() calls must not keep the recovery AP
                # alive forever. Only a real connection/recovery or an explicit
                # target change starts a new grace period.
                self._ap_off_at = ticks_add(self._clock(), max(0, int(grace_ms)))
            self._status_cache = self._query_status()
            return self._status_cache
        except Exception as exc:
            link_up = False
            try:
                link_up = bool(self._wlan("sta").isconnected())
            except Exception:
                pass
            # Only clear the driver when this call actually started a failed
            # attempt. An exception from feed(), grace conversion or status
            # collection must not tear down a healthy existing connection.
            if attempt_started and not link_up:
                try:
                    self._disconnect_sta(self._wlan("sta"), wait_ms=1000)
                except Exception:
                    pass
            self._last_error = str(exc)
            self._status_cache = dict(self._status_cache)
            self._status_cache["last_error"] = self._last_error
            if not link_up:
                self._status_cache["connected"] = False
                self._status_cache["sta_connected"] = False
                self._status_cache["ip"] = ""
                self._status_cache["sta_ip"] = ""
            raise
        finally:
            self._connecting = False
            self._release(acquired)

    def maintain(self, ssid, password, ap_ssid, ap_password,
                 grace_ms=300000, password_set=True, enabled=True,
                 lost_grace_ms=30000, reconnect_interval=60000,
                 timeout=10, channel=6):
        if not enabled:
            return self.status()
        status = self.status()
        now = self._clock()
        if status.get("ap_active") and status.get("sta_connected") and password_set:
            if self._ap_off_at is None:
                self.grace_ap(grace_ms)
            elif ticks_diff(now, self._ap_off_at) >= 0:
                return self.stop_ap()
        if not self._wifi_configured and not ssid:
            if not status.get("ap_active"):
                try:
                    return self.start_ap(ap_ssid, ap_password, channel=channel)
                except Exception:
                    return self.status()
            return status
        if not status.get("sta_connected"):
            if not status.get("ap_active") and ticks_diff(now, self._last_sta_seen) > lost_grace_ms:
                try:
                    return self.start_ap(ap_ssid, ap_password,
                                         channel=channel)
                except Exception:
                    return self.status()
            if (self._last_attempt is None
                    or ticks_diff(now, self._last_attempt) >= reconnect_interval):
                self._last_attempt = now
                try:
                    return self.connect(ssid, password, timeout=timeout,
                                        force=True, grace_ms=grace_ms)
                except Exception:
                    return self.status()
        return status

    def sync_time(self, server="pool.ntp.org", force=False):
        if self._time_synced and not force:
            return False
        if self._time_syncing:
            return False
        self._time_syncing = True
        import ntptime
        try:
            ntptime.host = server
        except AttributeError:
            pass
        last_error = None
        try:
            for _ in range(5):
                try:
                    ntptime.settime()
                    self._time_synced = True
                    self._time_synced_ms = self._clock()
                    self._status_cache = dict(self._status_cache)
                    self._status_cache["time_synced"] = True
                    self.feed()
                    return True
                except Exception as exc:
                    last_error = exc
                    self.feed()
                    _sleep_ms(300)
            raise RuntimeError("NTP synchronization failed: %s" % last_error)
        finally:
            self._time_syncing = False

    def time_sync_age_ms(self):
        if not self._time_synced or self._time_synced_ms is None:
            return None
        return max(0, ticks_diff(self._clock(), self._time_synced_ms))

    @staticmethod
    def time_is_valid():
        try:
            return time.localtime()[0] >= 2025
        except Exception:
            return False
