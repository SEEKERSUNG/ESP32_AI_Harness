import unittest

from harness.hardware import (
    HardwareAPI,
    HardwareManager,
    PeripheralError,
    validate_pin,
)
from tests.support import DummyConfig


class FakePin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    PULL_DOWN = 3

    def __init__(self, pin, mode=0, pull=None):
        self.pin = pin
        self.mode = mode
        self.pull = pull
        self._value = 0

    def value(self, value=None):
        if value is not None:
            self._value = value
        return self._value

    def deinit(self):
        pass


class FakeI2C:
    def __init__(self, scl=None, sda=None, freq=400000):
        self.scl = scl
        self.sda = sda
        self.freq = freq

    def scan(self):
        return [0x3C]


class FakeSPI:
    def __init__(self, baudrate=1000000, polarity=0, phase=0,
                 sck=None, mosi=None, miso=None):
        self.baudrate = baudrate
        self.sck = sck
        self.mosi = mosi
        self.miso = miso
        self.written = b""

    def write(self, data):
        self.written += bytes(data)

    def read(self, size):
        return b"\x00" * size

    def write_readinto(self, source, target):
        for index in range(min(len(source), len(target))):
            target[index] = source[index]


class FakeHardwareSPI(FakeSPI):
    def __init__(self, bus_id, **kwargs):
        FakeSPI.__init__(self, **kwargs)
        self.bus_id = bus_id


class FakeUART:
    def __init__(self, bus_id, baudrate=115200, tx=None, rx=None):
        self.bus_id = bus_id
        self.baudrate = baudrate
        self.tx = tx
        self.rx = rx


class FakeADC:
    ATTN_11DB = 3

    def __init__(self, pin):
        self.pin = pin

    def atten(self, value):
        pass

    def read(self):
        return 1234


class FakePWM:
    def __init__(self, pin, freq=1000, duty_u16=0, duty=None):
        self.pin = pin
        self._freq = freq
        self._duty = duty_u16 if duty is None else duty << 6
        self.deinited = False

    def freq(self, value=None):
        if value is not None:
            self._freq = value
        return self._freq

    def duty(self, value=None):
        if value is not None:
            self._duty = value << 6
        return self._duty >> 6

    def duty_u16(self, value=None):
        if value is not None:
            self._duty = value
        return self._duty

    def deinit(self):
        self.deinited = True


class FakeTouchPad:
    def __init__(self, pin):
        self.pin = pin
        self.threshold = None
        self.value = 12345

    def read(self):
        return self.value

    def config(self, threshold):
        self.threshold = threshold


class FakeRTC:
    def __init__(self):
        self.value = (2026, 11, 21, 5, 12, 0, 0, 0)

    def datetime(self, value=None):
        if value is not None:
            self.value = value
        return self.value


class FakeESP32:
    def mcu_temperature(self):
        return 41


class FakeMachine:
    Pin = FakePin
    SoftI2C = FakeI2C
    SoftSPI = FakeSPI
    SPI = FakeHardwareSPI
    UART = FakeUART
    ADC = FakeADC
    PWM = FakePWM
    TouchPad = FakeTouchPad
    RTC = FakeRTC

    @staticmethod
    def time_pulse_us(pin, level, timeout_us):
        return 123

    @staticmethod
    def wake_reason():
        return 2

    @staticmethod
    def reset_cause():
        return 1

    @staticmethod
    def freq():
        return 240000000

    @staticmethod
    def unique_id():
        return b"\x44\xbd\x8d\x70\xed\x00"


class HardwareTests(unittest.TestCase):
    def test_reserved_pins_are_rejected(self):
        with self.assertRaises(PeripheralError):
            validate_pin(26)
        with self.assertRaises(PeripheralError):
            validate_pin(43)
        self.assertEqual(4, validate_pin(4))

    def test_config_validation_reports_duplicate_and_reserved_pins(self):
        config = DummyConfig(
            allowed_gpio_pins=[4],
            i2c_buses=[{"id": 0, "sda": 4, "scl": 26, "freq": 400000}],
        )
        errors = HardwareManager(config, FakeMachine).validate_config()
        self.assertTrue(any("used by both" in error for error in errors))
        self.assertTrue(any("reserved" in error for error in errors))

    def test_config_validation_reports_neopixel_and_adc_conflicts(self):
        config = DummyConfig(
            neopixel_strips=[
                {"pin": 48, "count": 1},
                {"pin": 48, "count": 4},
            ],
            adc_pins=[4, 4],
        )
        errors = HardwareManager(config, FakeMachine).validate_config()
        self.assertTrue(
            any("GPIO48 is used by both neopixel and neopixel" in error
                for error in errors)
        )
        self.assertTrue(
            any("GPIO4 is used by both ADC and ADC" in error for error in errors)
        )

    def test_i2c_scan_uses_configured_bus(self):
        config = DummyConfig(
            peripherals_enabled=True,
            i2c_buses=[{"id": 0, "sda": 16, "scl": 15, "freq": 400000}],
        )
        manager = HardwareManager(config, FakeMachine)
        self.assertEqual([0x3C], manager.i2c_scan(0))
        status = manager.status()
        self.assertTrue(status["enabled"])
        self.assertEqual(1, len(status["i2c_buses"]))

    def test_hardware_api_uses_configured_gpio_and_spi(self):
        config = DummyConfig(
            peripherals_enabled=True,
            allowed_gpio_pins=[4, 5],
            spi_buses=[{
                "id": 1, "sck": 12, "mosi": 11, "miso": 13,
                "freq": 1000000,
            }],
        )
        manager = HardwareManager(config, FakeMachine)
        api = HardwareAPI(manager)
        self.assertTrue(api.available())
        self.assertTrue(api.status()["enabled"])
        api.gpio.setup(4, "out")
        api.gpio.write(4, 1)
        api.gpio.setup(5, "in")
        self.assertEqual(0, api.gpio.read(5))
        api.spi.write(1, b"\x01\x02")
        self.assertEqual(b"\x01\x02", manager.spi_bus(1).written)
        self.assertEqual(1, manager.spi_bus(1).bus_id)
        self.assertEqual(b"\x00\x00", api.spi.read(1, 2))

    def test_hardware_spi_without_miso_and_implicit_bus(self):
        config = DummyConfig(
            peripherals_enabled=True,
            allowed_gpio_pins=[7, 8, 9, 10],
            spi_buses=[{
                "id": 1, "sck": 12, "mosi": 11, "freq": 20000000,
                "mode": "hw",
            }],
        )
        manager = HardwareManager(config, FakeMachine)
        api = HardwareAPI(manager)
        bus = manager.spi_bus(1)
        self.assertIsInstance(bus, FakeHardwareSPI)
        self.assertIsNone(bus.miso)
        api.spi.write(b"\x01\x02")
        self.assertEqual(b"\x01\x02", bus.written)
        api.spi.write(memoryview(b"\x04\x05"))
        self.assertEqual(b"\x01\x02\x04\x05", bus.written)
        self.assertEqual(b"\x00\x00", api.spi.read(2))
        self.assertEqual(b"\x03", api.spi.transfer(b"\x03"))

    def test_spi_implicit_bus_requires_exactly_one_configured_bus(self):
        config = DummyConfig(
            peripherals_enabled=True,
            spi_buses=[
                {"id": 1, "sck": 12, "mosi": 11, "freq": 1000000},
                {"id": 2, "sck": 14, "mosi": 13, "freq": 1000000},
            ],
        )
        api = HardwareAPI(HardwareManager(config, FakeMachine))
        with self.assertRaises(PeripheralError) as raised:
            api.spi.write(b"\x01")
        self.assertIn("Multiple SPI buses", str(raised.exception))

    def test_software_spi_mode_still_available(self):
        config = DummyConfig(
            peripherals_enabled=True,
            spi_buses=[{
                "id": 0, "sck": 12, "mosi": 11, "miso": 13,
                "freq": 1000000, "mode": "sw",
            }],
        )
        manager = HardwareManager(config, FakeMachine)
        bus = manager.spi_bus(0)
        self.assertIsInstance(bus, FakeSPI)
        self.assertNotIsInstance(bus, FakeHardwareSPI)

    def test_config_validation_allows_write_only_spi(self):
        config = DummyConfig(
            spi_buses=[{
                "id": 1, "sck": 12, "mosi": 11, "freq": 20000000,
                "mode": "hw",
            }],
        )
        self.assertEqual([], HardwareManager(config, FakeMachine).validate_config())

    def test_config_validation_rejects_hardware_mode_on_unsupported_id(self):
        config = DummyConfig(
            spi_buses=[{
                "id": 0, "sck": 12, "mosi": 11, "freq": 1000000,
                "mode": "hw",
            }],
        )
        errors = HardwareManager(config, FakeMachine).validate_config()
        self.assertTrue(any("hardware host" in error for error in errors))

    def test_pwm_api_uses_allowlist_and_caches_duty(self):
        config = DummyConfig(peripherals_enabled=True, pwm_pins=[13])
        api = HardwareAPI(HardwareManager(config, FakeMachine))
        api.pwm.setup(13, 2000, 32768)
        self.assertEqual(32768, api.pwm.duty(13))
        self.assertEqual(2000, api.pwm.freq(13))
        api.pwm.write(13, 65535)
        self.assertEqual(65535, api.pwm.duty(13))
        api.pwm.freq(13, 4000)
        self.assertEqual(4000, api.pwm.freq(13))
        api.pwm.deinit(13)
        with self.assertRaises(PeripheralError):
            api.pwm.write(13, 0)

    def test_pwm_rejects_pins_not_in_pwm_pins(self):
        config = DummyConfig(peripherals_enabled=True, pwm_pins=[13])
        api = HardwareAPI(HardwareManager(config, FakeMachine))
        with self.assertRaises(PeripheralError):
            api.pwm.setup(14, 1000, 0)

    def test_touch_api_reads_and_configures(self):
        config = DummyConfig(peripherals_enabled=True, touch_pins=[4])
        api = HardwareAPI(HardwareManager(config, FakeMachine))
        self.assertEqual(12345, api.touch.read(4))
        self.assertEqual(777, api.touch.config(4, 777))
        with self.assertRaises(PeripheralError):
            api.touch.read(15)

    def test_rtc_reads_and_writes_only_when_enabled(self):
        config = DummyConfig(rtc_write=False)
        api = HardwareAPI(HardwareManager(config, FakeMachine))
        self.assertEqual(8, len(api.rtc.datetime()))
        with self.assertRaises(PeripheralError):
            api.rtc.set_datetime((2026, 11, 21, 5, 12, 34, 56, 0))
        config.values["rtc_write"] = True
        value = (2026, 11, 21, 5, 12, 34, 56, 0)
        self.assertEqual(value, api.rtc.set_datetime(value))
        self.assertEqual(value, api.rtc.datetime())

    def test_system_api_reports_chip_information(self):
        manager = HardwareManager(
            DummyConfig(), FakeMachine, esp32_module=FakeESP32())
        api = HardwareAPI(manager)
        self.assertEqual(41, api.system.temperature())
        self.assertEqual(2, api.system.wake_reason())
        self.assertEqual(1, api.system.reset_cause())
        self.assertEqual("44BD8D70ED00", api.system.unique_id())
        self.assertEqual(240000000, api.system.freq())
        self.assertGreaterEqual(api.system.uptime_ms(), 0)

    def test_pulse_measure_requires_input_and_allowed_pin(self):
        config = DummyConfig(
            peripherals_enabled=True, allowed_gpio_pins=[5])
        api = HardwareAPI(HardwareManager(config, FakeMachine))
        api.gpio.setup(5, "in")
        self.assertEqual(123, api.pulse.measure(5, 1, 10000))
        with self.assertRaises(PeripheralError):
            api.pulse.measure(6, 1, 10000)
        with self.assertRaises(PeripheralError):
            api.pulse.measure(5, 2, 10000)

    def test_config_validation_reports_pwm_and_touch_conflicts(self):
        config = DummyConfig(
            allowed_gpio_pins=[4],
            pwm_pins=[4],
            touch_pins=[15],
        )
        errors = HardwareManager(config, FakeMachine).validate_config()
        self.assertTrue(any("used by both GPIO and PWM" in error
                            for error in errors))
        self.assertTrue(any("not a touch-capable pin" in error
                            for error in errors))

    def test_hardware_api_stays_disabled_by_default(self):
        api = HardwareAPI(HardwareManager(DummyConfig(), FakeMachine))
        self.assertFalse(api.available())
        with self.assertRaises(PeripheralError):
            api.gpio.write(4, 1)


if __name__ == "__main__":
    unittest.main()
