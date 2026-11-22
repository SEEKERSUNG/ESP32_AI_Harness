class PeripheralError(Exception):
    pass


RESERVED_PINS = (
    0, 3, 19, 20,
    26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37,
    43, 44, 45, 46,
)
MAX_PIN = 48
MAX_NEOPIXEL_PIXELS = 64
MAX_PWM_DUTY = 65535
MAX_PULSE_TIMEOUT_US = 1000000
# ESP32-S3 touch channels are GPIO1-GPIO14. Reserved entries are filtered by
# validate_pin(), so only the free channels are usable.
TOUCH_PINS = tuple(range(1, 15))


def _as_int(value, name, minimum=0, maximum=None):
    if isinstance(value, bool):
        raise PeripheralError("%s must be an integer" % name)
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise PeripheralError("%s must be an integer" % name)
    if result < minimum or (maximum is not None and result > maximum):
        raise PeripheralError("%s is out of range" % name)
    return result


def validate_pin(pin, name="pin"):
    pin = _as_int(pin, name, 0, MAX_PIN)
    if pin in RESERVED_PINS:
        raise PeripheralError("GPIO%s is reserved for flash/PSRAM, USB, strapping or the console UART" % pin)
    return pin


def _to_bytes(value, name="data"):
    if isinstance(value, str):
        data = value.encode()
    elif isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
    else:
        raise PeripheralError(
            "%s must be str, bytes, bytearray or memoryview" % name
        )
    return data


def _bus_items(config, key):
    items = config.get(key, [])
    if not isinstance(items, list):
        raise PeripheralError("%s must be a list" % key)
    return items


def _list_items(config, key):
    items = config.get(key, [])
    return items if isinstance(items, list) else []


def _find_bus(config, key, bus_id):
    bus_id = _as_int(bus_id, "bus id", 0, 3)
    for item in _bus_items(config, key):
        if not isinstance(item, dict):
            continue
        try:
            if _as_int(item.get("id", -1), "bus id", 0, 3) == bus_id:
                return item
        except PeripheralError:
            continue
    raise PeripheralError("%s bus %s is not configured" % (key, bus_id))


class _GPIOAPI:
    def __init__(self, manager):
        self._manager = manager

    def setup(self, pin, mode="in", pull=None):
        return self._manager.gpio_setup(pin, mode, pull)

    def write(self, pin, value):
        return self._manager.gpio_write(pin, value)

    def read(self, pin):
        return self._manager.gpio_read(pin)

    def deinit(self, pin):
        return self._manager.gpio_deinit(pin)


class _SPIAPI:
    """Configured SPI access.

    All methods accept an explicit ``bus`` id. When exactly one SPI bus is
    configured, the id may be omitted: ``write(data)``, ``read(size)`` and
    ``transfer(data)`` select that bus. This keeps simple display drivers
    short and avoids a failed probe on the common single-bus board.
    """

    def __init__(self, manager):
        self._manager = manager

    def write(self, bus, data=None):
        """write(bus, data); with one bus, write(data) is accepted."""
        if data is None:
            bus, data = self._manager.spi_default_bus(), bus
        return self._manager.spi_write(bus, data)

    def read(self, bus, size=None):
        """read(bus, size); with one bus, read(size) is accepted."""
        if size is None:
            bus, size = self._manager.spi_default_bus(), bus
        return self._manager.spi_read(bus, size)

    def transfer(self, bus, data=None):
        """transfer(bus, data); with one bus, transfer(data) is accepted."""
        if data is None:
            bus, data = self._manager.spi_default_bus(), bus
        return self._manager.spi_transfer(bus, data)


class _I2CAPI:
    def __init__(self, manager):
        self._manager = manager

    def scan(self, bus):
        return self._manager.i2c_scan(bus)

    def write(self, bus, address, data):
        return self._manager.i2c_write(bus, address, data)

    def read(self, bus, address, size):
        return self._manager.i2c_read(bus, address, size)

    def write_mem(self, bus, address, memaddr, data):
        return self._manager.i2c_write_mem(bus, address, memaddr, data)

    def read_mem(self, bus, address, memaddr, size):
        return self._manager.i2c_read_mem(bus, address, memaddr, size)


class _UARTAPI:
    def __init__(self, manager):
        self._manager = manager

    def write(self, bus, data):
        return self._manager.uart_write(bus, data)

    def read(self, bus, size=64):
        return self._manager.uart_read(bus, size)

    def any(self, bus):
        return self._manager.uart_any(bus)

    def readline(self, bus):
        return self._manager.uart_readline(bus)


class _NeoPixelAPI:
    def __init__(self, manager):
        self._manager = manager

    def write(self, pin, pixels):
        return self._manager.neopixel_write(pin, pixels)

    def fill(self, pin, red, green, blue):
        return self._manager.neopixel_fill(pin, red, green, blue)

    def clear(self, pin):
        return self._manager.neopixel_clear(pin)

    def read(self, pin):
        return self._manager.neopixel_read(pin)


class _ADCAPI:
    def __init__(self, manager):
        self._manager = manager

    def read(self, pin):
        return self._manager.adc_read(pin)


class _PWMAPI:
    """PWM output on pins listed in ``pwm_pins``.

    Duty is 0-65535. The hardware duty getter is unreliable on this port, so
    ``duty()`` reports the last value written through this API.
    """

    def __init__(self, manager):
        self._manager = manager

    def setup(self, pin, freq=1000, duty=0):
        return self._manager.pwm_setup(pin, freq, duty)

    def write(self, pin, duty):
        return self._manager.pwm_write(pin, duty)

    def duty(self, pin):
        return self._manager.pwm_duty(pin)

    def freq(self, pin, value=None):
        return self._manager.pwm_freq(pin, value)

    def deinit(self, pin):
        return self._manager.pwm_deinit(pin)


class _TouchAPI:
    """Capacitive touch channels listed in ``touch_pins``."""

    def __init__(self, manager):
        self._manager = manager

    def read(self, pin):
        return self._manager.touch_read(pin)

    def config(self, pin, threshold):
        return self._manager.touch_config(pin, threshold)


class _RTCAPI:
    """On-chip RTC. Reads are always available; writes need ``rtc_write``."""

    def __init__(self, manager):
        self._manager = manager

    def datetime(self):
        return self._manager.rtc_datetime()

    def set_datetime(self, value):
        return self._manager.rtc_set_datetime(value)


class _SystemAPI:
    """Read-only chip information; no pin configuration is involved."""

    def __init__(self, manager):
        self._manager = manager

    def temperature(self):
        return self._manager.system_temperature()

    def wake_reason(self):
        return self._manager.system_wake_reason()

    def reset_cause(self):
        return self._manager.system_reset_cause()

    def unique_id(self):
        return self._manager.system_unique_id()

    def freq(self):
        return self._manager.system_freq()

    def uptime_ms(self):
        return self._manager.system_uptime_ms()


class _PulseAPI:
    """Pulse-width measurement (for example HC-SR04 echo) on allowed GPIO."""

    def __init__(self, manager):
        self._manager = manager

    def measure(self, pin, level=1, timeout_us=1000000):
        return self._manager.pulse_measure(pin, level, timeout_us)


class HardwareAPI:
    """Reviewed hardware surface exposed to generated code as ``api``.

    Every call still goes through HardwareManager, so the opt-in
    ``peripherals_enabled`` flag, reserved-pin checks, allowed GPIO list,
    configured buses and transfer limits apply. This object is the only
    hardware access intended for generated code; ``machine`` remains off
    limits.
    """

    def __init__(self, manager):
        self._manager = manager
        self.gpio = _GPIOAPI(manager)
        self.spi = _SPIAPI(manager)
        self.i2c = _I2CAPI(manager)
        self.uart = _UARTAPI(manager)
        self.neopixel = _NeoPixelAPI(manager)
        self.adc = _ADCAPI(manager)
        self.pwm = _PWMAPI(manager)
        self.touch = _TouchAPI(manager)
        self.rtc = _RTCAPI(manager)
        self.system = _SystemAPI(manager)
        self.pulse = _PulseAPI(manager)

    def status(self):
        return self._manager.status()

    def available(self):
        return bool(self._manager.status().get("enabled"))


class HardwareManager:
    def __init__(self, config, machine_module=None, neopixel_module=None,
                 esp32_module=None):
        self.config = config
        self.machine = machine_module
        self.neopixel_module = neopixel_module
        self.esp32_module = esp32_module
        self._pins = {}
        self._pin_modes = {}
        self._i2c = {}
        self._spi = {}
        self._uart = {}
        self._neopixels = {}
        self._adc = {}
        self._pwm = {}
        self._pwm_duty = {}
        self._pwm_freq = {}
        self._touch = {}
        self._rtc = None

    def _module(self):
        if self.machine is None:
            try:
                import machine
            except ImportError:
                raise PeripheralError("machine module is unavailable")
            self.machine = machine
        return self.machine

    def _neopixel_module(self):
        if self.neopixel_module is None:
            try:
                import neopixel
            except ImportError:
                raise PeripheralError("neopixel module is unavailable")
            self.neopixel_module = neopixel
        return self.neopixel_module

    def _esp32_module(self):
        if self.esp32_module is None:
            try:
                import esp32
            except ImportError:
                raise PeripheralError(
                    "internal temperature sensor (esp32 module) is unavailable"
                )
            self.esp32_module = esp32
        return self.esp32_module

    def _enabled(self):
        if not self.config.get("peripherals_enabled", False):
            raise PeripheralError("Peripherals are disabled; enable them with /peripherals enable")

    def _limit(self):
        return _as_int(self.config.get("peripheral_max_transfer", 256), "peripheral_max_transfer", 1, 4096)

    def _check_gpio_allowed(self, pin):
        allowed = self.config.get("allowed_gpio_pins", [])
        try:
            pins = [int(value) for value in allowed] if isinstance(allowed, list) else []
        except (TypeError, ValueError):
            pins = []
        if pin not in pins:
            raise PeripheralError("GPIO%s is not in allowed_gpio_pins" % pin)

    @staticmethod
    def _cached(cache, key, signature, factory):
        cached = cache.get(key)
        if cached is not None:
            if cached[0] == signature:
                return cached[1]
            cache.pop(key, None)
            try:
                cached[1].deinit()
            except Exception:
                pass
        value = factory()
        cache[key] = (signature, value)
        return value

    def status(self):
        config = self.config
        allowed = []
        for pin in config.get("allowed_gpio_pins", []):
            try:
                allowed.append(validate_pin(pin, "allowed GPIO pin"))
            except PeripheralError:
                allowed.append(pin)
        try:
            max_transfer = self._limit()
        except PeripheralError:
            max_transfer = 256
        result = {
            "enabled": bool(config.get("peripherals_enabled", False)),
            "max_transfer": max_transfer,
            "allowed_gpio_pins": allowed,
            "i2c_buses": _bus_items(config, "i2c_buses"),
            "spi_buses": _bus_items(config, "spi_buses"),
            "uart_buses": _bus_items(config, "uart_buses"),
            "neopixel_strips": _list_items(config, "neopixel_strips"),
            "adc_pins": _list_items(config, "adc_pins"),
            "pwm_pins": _list_items(config, "pwm_pins"),
            "touch_pins": _list_items(config, "touch_pins"),
            "rtc_write": bool(config.get("rtc_write", False)),
        }
        return result

    def validate_config(self):
        errors = []
        used = {}
        allowed = self.config.get("allowed_gpio_pins", [])
        if not isinstance(allowed, list):
            errors.append("allowed_gpio_pins must be a list")
            allowed = []
        for pin in allowed:
            try:
                pin = validate_pin(pin, "allowed GPIO pin")
            except PeripheralError as exc:
                errors.append(str(exc))
                continue
            if pin in used:
                errors.append("GPIO%s is used by both %s and GPIO" % (pin, used[pin]))
            else:
                used[pin] = "GPIO"
        for key in ("i2c_buses", "spi_buses", "uart_buses"):
            try:
                items = _bus_items(self.config, key)
            except PeripheralError as exc:
                errors.append(str(exc))
                continue
            for item in items:
                if not isinstance(item, dict):
                    errors.append("%s entry must be an object" % key)
                    continue
                bus_id = item.get("id")
                try:
                    bus_id = _as_int(bus_id, "%s id" % key, 0, 3)
                except PeripheralError as exc:
                    errors.append(str(exc))
                    continue
                if key == "uart_buses" and bus_id == 0:
                    errors.append("UART0 is reserved for the console")
                    continue
                if key == "i2c_buses":
                    pin_names = ("sda", "scl")
                elif key == "spi_buses":
                    pin_names = ("sck", "mosi", "miso")
                    mode = str(item.get("mode", "hw")).lower()
                    if mode not in ("hw", "sw"):
                        errors.append("SPI bus %s mode must be 'hw' or 'sw'" % bus_id)
                    elif mode == "hw" and bus_id not in (1, 2):
                        errors.append(
                            "SPI bus %s has no hardware host; use id 1 or 2 "
                            "with mode hw, or mode sw" % bus_id
                        )
                else:
                    pin_names = ("tx", "rx")
                bus_pins = []
                valid_pins = True
                for name in pin_names:
                    if key == "spi_buses" and name == "miso":
                        value = item.get(name)
                        if value in (None, "", "-", "none", "off"):
                            continue
                    try:
                        pin = validate_pin(item.get(name), "%s %s" % (key, name))
                    except PeripheralError as exc:
                        errors.append(str(exc))
                        valid_pins = False
                        continue
                    bus_pins.append(pin)
                    if pin in used:
                        errors.append("GPIO%s is used by both %s and %s" % (pin, used[pin], key))
                    else:
                        used[pin] = key
                if not valid_pins:
                    continue
                if len(set(bus_pins)) != len(bus_pins):
                    errors.append("%s bus %s reuses a pin" % (key, bus_id))
                if key == "i2c_buses":
                    try:
                        _as_int(item.get("freq", 400000), "I2C frequency", 1000, 1000000)
                    except PeripheralError as exc:
                        errors.append(str(exc))
                elif key == "spi_buses":
                    try:
                        _as_int(item.get("freq", 1000000), "SPI frequency", 1000, 80000000)
                    except PeripheralError as exc:
                        errors.append(str(exc))
                else:
                    try:
                        _as_int(item.get("baud", 115200), "UART baud", 1200, 4000000)
                    except PeripheralError as exc:
                        errors.append(str(exc))
        for item in _list_items(self.config, "neopixel_strips"):
            if not isinstance(item, dict):
                errors.append("neopixel_strips entry must be an object")
                continue
            try:
                pin = validate_pin(item.get("pin"), "neopixel pin")
                _as_int(item.get("count", 1), "neopixel count", 1, MAX_NEOPIXEL_PIXELS)
            except PeripheralError as exc:
                errors.append(str(exc))
                continue
            if pin in used:
                errors.append("GPIO%s is used by both %s and neopixel" % (pin, used[pin]))
            else:
                used[pin] = "neopixel"
        for item in _list_items(self.config, "adc_pins"):
            try:
                pin = validate_pin(item, "ADC pin")
            except PeripheralError as exc:
                errors.append(str(exc))
                continue
            if pin in used:
                errors.append("GPIO%s is used by both %s and ADC" % (pin, used[pin]))
            else:
                used[pin] = "ADC"
        for key, label in (("pwm_pins", "PWM"), ("touch_pins", "touch")):
            for item in _list_items(self.config, key):
                try:
                    pin = validate_pin(item, "%s pin" % label)
                except PeripheralError as exc:
                    errors.append(str(exc))
                    continue
                if key == "touch_pins" and pin not in TOUCH_PINS:
                    errors.append(
                        "GPIO%s is not a touch-capable pin (GPIO1-GPIO14)"
                        % pin
                    )
                if pin in used:
                    errors.append(
                        "GPIO%s is used by both %s and %s"
                        % (pin, used[pin], label)
                    )
                else:
                    used[pin] = label
        return errors

    def gpio_setup(self, pin, mode="in", pull=None):
        self._enabled()
        pin = validate_pin(pin)
        self._check_gpio_allowed(pin)
        module = self._module()
        mode = str(mode).lower()
        if mode == "in":
            pin_mode = module.Pin.IN
        elif mode == "out":
            pin_mode = module.Pin.OUT
        else:
            raise PeripheralError("GPIO mode must be 'in' or 'out'")
        kwargs = {}
        if pull is not None:
            pull = str(pull).lower()
            if pull == "up":
                kwargs["pull"] = module.Pin.PULL_UP
            elif pull == "down":
                kwargs["pull"] = module.Pin.PULL_DOWN
            else:
                raise PeripheralError("GPIO pull must be 'up', 'down' or None")
        obj = module.Pin(pin, pin_mode, **kwargs)
        self._pins[pin] = obj
        self._pin_modes[pin] = mode

    def _pin(self, pin, mode=None):
        pin = validate_pin(pin)
        self._check_gpio_allowed(pin)
        if pin not in self._pins:
            raise PeripheralError("GPIO%s is not set up; call gpio.setup first" % pin)
        if mode is not None and self._pin_modes.get(pin) != mode:
            raise PeripheralError("GPIO%s is not configured for %s" % (pin, mode))
        return self._pins[pin]

    def gpio_write(self, pin, value):
        self._enabled()
        obj = self._pin(pin, "out")
        obj.value(1 if value else 0)

    def gpio_read(self, pin):
        self._enabled()
        obj = self._pin(pin, "in")
        return obj.value()

    def gpio_deinit(self, pin):
        self._enabled()
        pin = validate_pin(pin)
        obj = self._pins.pop(pin, None)
        self._pin_modes.pop(pin, None)
        if obj is not None:
            try:
                obj.deinit()
            except AttributeError:
                pass

    def i2c_bus(self, bus_id):
        self._enabled()
        bus_id = _as_int(bus_id, "I2C bus id", 0, 3)
        # Validate the current configuration before considering a cached bus.
        item = _find_bus(self.config, "i2c_buses", bus_id)
        sda = validate_pin(item.get("sda"), "I2C sda")
        scl = validate_pin(item.get("scl"), "I2C scl")
        freq = _as_int(item.get("freq", 400000), "I2C frequency", 1000, 1000000)

        def create():
            module = self._module()
            if hasattr(module, "SoftI2C"):
                return module.SoftI2C(scl=module.Pin(scl), sda=module.Pin(sda), freq=freq)
            return module.I2C(bus_id, scl=module.Pin(scl), sda=module.Pin(sda), freq=freq)

        return self._cached(self._i2c, bus_id, (sda, scl, freq), create)

    @staticmethod
    def _address(address):
        return _as_int(address, "I2C address", 0, 127)

    def i2c_scan(self, bus_id):
        return self.i2c_bus(bus_id).scan()

    def i2c_write(self, bus_id, address, data):
        data = _to_bytes(data)
        if len(data) > self._limit():
            raise PeripheralError("I2C transfer exceeds peripheral_max_transfer")
        self.i2c_bus(bus_id).writeto(self._address(address), data)

    def i2c_read(self, bus_id, address, size):
        size = _as_int(size, "I2C read size", 1, self._limit())
        return self.i2c_bus(bus_id).readfrom(self._address(address), size)

    def i2c_write_mem(self, bus_id, address, memaddr, data):
        data = _to_bytes(data)
        if len(data) > self._limit():
            raise PeripheralError("I2C transfer exceeds peripheral_max_transfer")
        self.i2c_bus(bus_id).writeto_mem(self._address(address), memaddr, data)

    def i2c_read_mem(self, bus_id, address, memaddr, size):
        size = _as_int(size, "I2C read size", 1, self._limit())
        return self.i2c_bus(bus_id).readfrom_mem(self._address(address), memaddr, size)

    def spi_default_bus(self):
        """Return the only configured SPI bus id, or explain why it is ambiguous."""
        buses = [
            item for item in _bus_items(self.config, "spi_buses")
            if isinstance(item, dict)
        ]
        if len(buses) == 1:
            return _as_int(buses[0].get("id"), "SPI bus id", 0, 3)
        if not buses:
            raise PeripheralError(
                "No SPI bus is configured; use /peripherals spi first"
            )
        ids = []
        for item in buses:
            try:
                ids.append(str(_as_int(item.get("id"), "SPI bus id", 0, 3)))
            except PeripheralError:
                continue
        raise PeripheralError(
            "Multiple SPI buses are configured (%s); pass the bus id to api.spi"
            % ", ".join(ids)
        )

    def spi_bus(self, bus_id):
        self._enabled()
        bus_id = _as_int(bus_id, "SPI bus id", 0, 3)
        item = _find_bus(self.config, "spi_buses", bus_id)
        sck = validate_pin(item.get("sck"), "SPI sck")
        mosi = validate_pin(item.get("mosi"), "SPI mosi")
        raw_miso = item.get("miso")
        if raw_miso in (None, "", "-", "none", "off"):
            miso = None
        else:
            miso = validate_pin(raw_miso, "SPI miso")
        freq = _as_int(item.get("freq", 1000000), "SPI frequency", 1000, 80000000)
        mode = str(item.get("mode", "hw")).lower()
        if mode not in ("hw", "sw"):
            raise PeripheralError("SPI mode must be 'hw' or 'sw'")
        if mode == "hw" and bus_id not in (1, 2):
            raise PeripheralError(
                "SPI bus %s has no hardware host; use id 1 or 2, or mode sw"
                % bus_id
            )

        def create():
            module = self._module()
            kwargs = {
                "baudrate": freq,
                "polarity": 0,
                "phase": 0,
                "sck": module.Pin(sck),
                "mosi": module.Pin(mosi),
            }
            if miso is not None:
                kwargs["miso"] = module.Pin(miso)
            if mode == "hw":
                factory = getattr(module, "SPI", None)
                if factory is None:
                    raise PeripheralError(
                        "hardware SPI is unavailable; configure the bus with mode sw"
                    )
                return factory(bus_id, **kwargs)
            factory = getattr(module, "SoftSPI", None)
            if factory is None:
                raise PeripheralError("software SPI is unavailable on this firmware")
            return factory(**kwargs)

        return self._cached(
            self._spi, bus_id, (sck, mosi, miso, freq, mode), create
        )

    def spi_write(self, bus_id, data):
        data = _to_bytes(data)
        if len(data) > self._limit():
            raise PeripheralError("SPI transfer exceeds peripheral_max_transfer")
        self.spi_bus(bus_id).write(data)

    def spi_read(self, bus_id, size):
        size = _as_int(size, "SPI read size", 1, self._limit())
        return self.spi_bus(bus_id).read(size)

    def spi_transfer(self, bus_id, data):
        data = _to_bytes(data)
        if len(data) > self._limit():
            raise PeripheralError("SPI transfer exceeds peripheral_max_transfer")
        output = bytearray(len(data))
        self.spi_bus(bus_id).write_readinto(data, output)
        return bytes(output)

    def uart_bus(self, bus_id):
        self._enabled()
        bus_id = _as_int(bus_id, "UART id", 1, 2)
        item = _find_bus(self.config, "uart_buses", bus_id)
        tx = validate_pin(item.get("tx"), "UART tx")
        rx = validate_pin(item.get("rx"), "UART rx")
        baud = _as_int(item.get("baud", 115200), "UART baud", 1200, 4000000)

        def create():
            module = self._module()
            return module.UART(
                bus_id,
                baudrate=baud,
                tx=module.Pin(tx),
                rx=module.Pin(rx),
            )

        return self._cached(self._uart, bus_id, (tx, rx, baud), create)

    def uart_write(self, bus_id, data):
        data = _to_bytes(data)
        if len(data) > self._limit():
            raise PeripheralError("UART transfer exceeds peripheral_max_transfer")
        return self.uart_bus(bus_id).write(data)

    def uart_read(self, bus_id, size=64):
        size = _as_int(size, "UART read size", 1, self._limit())
        return self.uart_bus(bus_id).read(size)

    def uart_any(self, bus_id):
        bus = self.uart_bus(bus_id)
        if hasattr(bus, "any"):
            return bus.any()
        return 0

    def uart_readline(self, bus_id):
        return self.uart_bus(bus_id).readline()

    def _neopixel_config(self, pin):
        pin = validate_pin(pin)
        for item in _list_items(self.config, "neopixel_strips"):
            if not isinstance(item, dict):
                continue
            try:
                if _as_int(item.get("pin", -1), "neopixel pin", 0, MAX_PIN) == pin:
                    return item
            except PeripheralError:
                continue
        raise PeripheralError("NeoPixel pin %s is not configured" % pin)

    def neopixel_strip(self, pin):
        self._enabled()
        item = self._neopixel_config(pin)
        pin = validate_pin(item.get("pin"))
        count = _as_int(item.get("count", 1), "neopixel count", 1, MAX_NEOPIXEL_PIXELS)

        def create():
            module = self._module()
            neopixel = self._neopixel_module()
            return neopixel.NeoPixel(module.Pin(pin), count)

        return self._cached(self._neopixels, pin, count, create)

    def neopixel_write(self, pin, pixels):
        self._enabled()
        strip = self.neopixel_strip(pin)
        if not isinstance(pixels, (list, tuple)):
            raise PeripheralError("NeoPixel pixels must be a list of (r,g,b)")
        if len(pixels) != len(strip):
            raise PeripheralError(
                "NeoPixel pixels must contain %s entries" % len(strip)
            )
        for index in range(len(strip)):
            pixel = pixels[index]
            if not isinstance(pixel, (list, tuple)) or len(pixel) != 3:
                raise PeripheralError(
                    "NeoPixel pixel %s must be (r,g,b)" % index
                )
            strip[index] = (
                _as_int(pixel[0], "NeoPixel red", 0, 255),
                _as_int(pixel[1], "NeoPixel green", 0, 255),
                _as_int(pixel[2], "NeoPixel blue", 0, 255),
            )
        strip.write()

    def neopixel_fill(self, pin, red, green, blue):
        strip = self.neopixel_strip(pin)
        color = (
            _as_int(red, "NeoPixel red", 0, 255),
            _as_int(green, "NeoPixel green", 0, 255),
            _as_int(blue, "NeoPixel blue", 0, 255),
        )
        for index in range(len(strip)):
            strip[index] = color
        strip.write()

    def neopixel_clear(self, pin):
        self.neopixel_fill(pin, 0, 0, 0)

    def neopixel_read(self, pin):
        strip = self.neopixel_strip(pin)
        return [strip[index] for index in range(len(strip))]

    def adc_read(self, pin):
        self._enabled()
        pin = validate_pin(pin)
        allowed = []
        for value in _list_items(self.config, "adc_pins"):
            try:
                allowed.append(int(value))
            except (TypeError, ValueError):
                pass
        if pin not in allowed:
            raise PeripheralError("GPIO%s is not in adc_pins" % pin)
        if pin not in self._adc:
            module = self._module()
            adc = module.ADC(module.Pin(pin))
            try:
                adc.atten(module.ADC.ATTN_11DB)
            except (AttributeError, ValueError):
                pass
            self._adc[pin] = adc
        return self._adc[pin].read()

    @staticmethod
    def _require_pin_in(config, key, pin, label):
        pin = validate_pin(pin, "%s pin" % label)
        allowed = []
        for value in _list_items(config, key):
            try:
                allowed.append(int(value))
            except (TypeError, ValueError):
                pass
        if pin not in allowed:
            raise PeripheralError("GPIO%s is not in %s" % (pin, key))
        return pin

    @staticmethod
    def _set_pwm_duty(obj, duty):
        setter = getattr(obj, "duty_u16", None)
        if setter is not None:
            try:
                setter(duty)
                return
            except TypeError:
                pass
        obj.duty(duty >> 6)

    def pwm_setup(self, pin, freq=1000, duty=0):
        self._enabled()
        pin = self._require_pin_in(self.config, "pwm_pins", pin, "PWM")
        freq = _as_int(freq, "PWM frequency", 1, 40000000)
        duty = _as_int(duty, "PWM duty", 0, MAX_PWM_DUTY)
        module = self._module()
        cls = getattr(module, "PWM", None)
        if cls is None:
            raise PeripheralError("PWM is unavailable on this firmware")
        obj = self._pwm.get(pin)
        if obj is None:
            pin_obj = module.Pin(pin)
            try:
                obj = cls(pin_obj, freq=freq, duty_u16=duty)
            except TypeError:
                try:
                    obj = cls(pin_obj, freq=freq, duty=duty >> 6)
                except TypeError:
                    obj = cls(pin_obj)
                    obj.freq(freq)
                    self._set_pwm_duty(obj, duty)
            self._pwm[pin] = obj
        else:
            obj.freq(freq)
            self._set_pwm_duty(obj, duty)
        self._pwm_freq[pin] = freq
        self._pwm_duty[pin] = duty

    def pwm_write(self, pin, duty):
        self._enabled()
        pin = self._require_pin_in(self.config, "pwm_pins", pin, "PWM")
        obj = self._pwm.get(pin)
        if obj is None:
            raise PeripheralError(
                "GPIO%s PWM is not set up; call pwm.setup first" % pin
            )
        duty = _as_int(duty, "PWM duty", 0, MAX_PWM_DUTY)
        self._set_pwm_duty(obj, duty)
        self._pwm_duty[pin] = duty
        return duty

    def pwm_duty(self, pin):
        self._enabled()
        pin = self._require_pin_in(self.config, "pwm_pins", pin, "PWM")
        if pin not in self._pwm:
            raise PeripheralError(
                "GPIO%s PWM is not set up; call pwm.setup first" % pin
            )
        return self._pwm_duty.get(pin, 0)

    def pwm_freq(self, pin, value=None):
        self._enabled()
        pin = self._require_pin_in(self.config, "pwm_pins", pin, "PWM")
        obj = self._pwm.get(pin)
        if obj is None:
            raise PeripheralError(
                "GPIO%s PWM is not set up; call pwm.setup first" % pin
            )
        if value is None:
            return self._pwm_freq.get(pin, 0)
        value = _as_int(value, "PWM frequency", 1, 40000000)
        obj.freq(value)
        self._pwm_freq[pin] = value
        return value

    def pwm_deinit(self, pin):
        self._enabled()
        pin = validate_pin(pin)
        obj = self._pwm.pop(pin, None)
        self._pwm_duty.pop(pin, None)
        self._pwm_freq.pop(pin, None)
        if obj is not None:
            obj.deinit()

    def _touch_allowed(self, pin):
        pin = validate_pin(pin, "touch pin")
        if pin not in TOUCH_PINS:
            raise PeripheralError(
                "GPIO%s is not a touch-capable pin (GPIO1-GPIO14)" % pin
            )
        return self._require_pin_in(self.config, "touch_pins", pin, "touch")

    def _touch_object(self, pin):
        obj = self._touch.get(pin)
        if obj is not None:
            return obj
        module = self._module()
        cls = getattr(module, "TouchPad", None)
        if cls is None:
            raise PeripheralError("capacitive touch is unavailable on this firmware")
        obj = cls(module.Pin(pin))
        self._touch[pin] = obj
        return obj

    def touch_read(self, pin):
        self._enabled()
        pin = self._touch_allowed(pin)
        return self._touch_object(pin).read()

    def touch_config(self, pin, threshold):
        self._enabled()
        pin = self._touch_allowed(pin)
        threshold = _as_int(threshold, "touch threshold", 0, 65535)
        self._touch_object(pin).config(threshold)
        return threshold

    def _rtc_object(self):
        module = self._module()
        cls = getattr(module, "RTC", None)
        if cls is None:
            raise PeripheralError("RTC is unavailable on this firmware")
        if self._rtc is None:
            self._rtc = cls()
        return self._rtc

    def rtc_datetime(self):
        return self._rtc_object().datetime()

    def rtc_set_datetime(self, value):
        if not self.config.get("rtc_write", False):
            raise PeripheralError(
                "RTC writes are disabled; enable with /peripherals rtc write on"
            )
        if not isinstance(value, (list, tuple)) or len(value) != 8:
            raise PeripheralError(
                "RTC datetime must be an 8-item tuple: "
                "(year, month, day, weekday, hour, minute, second, subsecond)"
            )
        ranges = (
            (2000, 2099), (1, 12), (1, 31), (0, 6),
            (0, 23), (0, 59), (0, 59), (0, 999999),
        )
        result = []
        for index in range(8):
            minimum, maximum = ranges[index]
            result.append(_as_int(
                value[index], "RTC datetime field %s" % index,
                minimum, maximum
            ))
        result = tuple(result)
        self._rtc_object().datetime(result)
        return result

    def system_temperature(self):
        module = self._esp32_module()
        func = getattr(module, "mcu_temperature", None)
        if func is None:
            raise PeripheralError("internal temperature is unavailable")
        return func()

    def system_wake_reason(self):
        module = self._module()
        func = getattr(module, "wake_reason", None)
        if func is None:
            raise PeripheralError("wake_reason is unavailable")
        return func()

    def system_reset_cause(self):
        module = self._module()
        func = getattr(module, "reset_cause", None)
        if func is None:
            raise PeripheralError("reset_cause is unavailable")
        return func()

    def system_unique_id(self):
        module = self._module()
        func = getattr(module, "unique_id", None)
        if func is None:
            raise PeripheralError("unique_id is unavailable")
        value = func()
        if isinstance(value, str):
            return value
        return "".join("%02X" % byte for byte in value)

    def system_freq(self):
        module = self._module()
        func = getattr(module, "freq", None)
        if func is None:
            raise PeripheralError("freq is unavailable")
        return func()

    def system_uptime_ms(self):
        import time
        if hasattr(time, "ticks_ms"):
            return time.ticks_ms()
        return int(time.monotonic() * 1000)

    def pulse_measure(self, pin, level=1, timeout_us=1000000):
        self._enabled()
        pin = validate_pin(pin)
        self._check_gpio_allowed(pin)
        obj = self._pin(pin, "in")
        level = _as_int(level, "pulse level", 0, 1)
        timeout_us = _as_int(
            timeout_us, "pulse timeout", 1, MAX_PULSE_TIMEOUT_US
        )
        module = self._module()
        func = getattr(module, "time_pulse_us", None)
        if func is None:
            raise PeripheralError("time_pulse_us is unavailable")
        return func(obj, level, timeout_us)
