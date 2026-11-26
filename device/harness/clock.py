"""RTC clock formatting and automatic NTP refresh.

The ESP32 RTC is kept in UTC.  ``ntptime.settime()`` updates it from the
configured NTP server, and this module converts that UTC value to the
configured fixed offset for display.  No host timezone database is used.
"""

import time

WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)
DEFAULT_OFFSET_MINUTES = 480
MIN_OFFSET_MINUTES = -720
MAX_OFFSET_MINUTES = 840
DEFAULT_SYNC_INTERVAL_S = 21600
MAX_SYNC_INTERVAL_S = 86400
RETRY_DELAY_MS = 60000

_MISSING = object()


class ClockError(Exception):
    pass


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


def _ticks_add(value, delta):
    if hasattr(time, "ticks_add"):
        return time.ticks_add(value, delta)
    return value + delta


def _ticks_diff(later, earlier):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(later, earlier)
    return later - earlier


def _int_config(config, name, default):
    try:
        return int(config.get(name, default))
    except (TypeError, ValueError):
        return default


def timezone_offset_minutes(config):
    value = _int_config(
        config, "timezone_offset_minutes", DEFAULT_OFFSET_MINUTES)
    return max(MIN_OFFSET_MINUTES, min(value, MAX_OFFSET_MINUTES))


def _days_from_civil(year, month, day):
    """Days since 1970-01-01 for a proleptic Gregorian date."""
    year -= 1 if month <= 2 else 0
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    shifted_month = month - 3 if month > 2 else month + 9
    doy = (153 * shifted_month + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(days):
    """Inverse of _days_from_civil."""
    days += 719468
    era = (days if days >= 0 else days - 146096) // 146097
    doe = days - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    year = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    year += 1 if month <= 2 else 0
    return year, month, day


def _parse_fields(value):
    if not isinstance(value, (tuple, list)) or len(value) < 7:
        return None
    try:
        year = int(value[0])
        month = int(value[1])
        day = int(value[2])
        hour = int(value[4])
        minute = int(value[5])
        second = int(value[6])
    except (TypeError, ValueError, IndexError):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    if not (1970 <= year <= 2200):
        return None
    return year, month, day, hour, minute, second


def read_rtc():
    """Return machine.RTC().datetime(), or None when RTC is unavailable."""
    try:
        import machine
        value = machine.RTC().datetime()
    except Exception:
        return None
    return _parse_fields(value)


def _system_utc():
    try:
        value = time.gmtime()
    except Exception:
        return None
    return _parse_fields(value)


def _format_offset(offset):
    sign = "+" if offset >= 0 else "-"
    value = abs(int(offset))
    return "UTC%s%02d:%02d" % (sign, value // 60, value % 60)


def _format_iso(fields, suffix):
    return "%04d-%02d-%02dT%02d:%02d:%02d%s" % (
        fields[0], fields[1], fields[2],
        fields[3], fields[4], fields[5], suffix,
    )


def snapshot(config, rtc_value=_MISSING):
    """Return the current RTC time converted to the configured timezone."""
    if rtc_value is _MISSING:
        fields = read_rtc()
        source = "RTC"
        if fields is None:
            fields = _system_utc()
            source = "system"
    else:
        fields = _parse_fields(rtc_value)
        source = "RTC"
    if fields is None:
        raise ClockError("RTC time is unavailable or invalid")

    year, month, day, hour, minute, second = fields
    utc_days = _days_from_civil(year, month, day)
    epoch = utc_days * 86400 + hour * 3600 + minute * 60 + second
    offset = timezone_offset_minutes(config)
    local_seconds = epoch + offset * 60
    local_days = local_seconds // 86400
    local_remainder = local_seconds - local_days * 86400
    local_year, local_month, local_day = _civil_from_days(local_days)
    local_hour = local_remainder // 3600
    local_remainder -= local_hour * 3600
    local_minute = local_remainder // 60
    local_second = local_remainder - local_minute * 60
    local_fields = (
        local_year, local_month, local_day,
        local_hour, local_minute, local_second,
    )
    weekday = WEEKDAY_NAMES[(local_days + 3) % 7]
    tz = _format_offset(offset)
    return {
        "source": source,
        "valid": 2025 <= year <= 2200,
        "epoch": epoch,
        "offset_minutes": offset,
        "timezone": tz,
        "utc": _format_iso(fields, "Z"),
        "local": _format_iso(local_fields, "%s%s" % (
            "+" if offset >= 0 else "-",
            "%02d:%02d" % (abs(offset) // 60, abs(offset) % 60),
        )),
        "local_text": "%04d-%02d-%02d %02d:%02d:%02d %s (%s)" % (
            local_year, local_month, local_day,
            local_hour, local_minute, local_second, tz, weekday,
        ),
        "weekday": weekday,
    }


def format_snapshot(value):
    lines = [
        "source: %s" % value["source"],
        "timezone: %s" % value["timezone"],
        "local: %s" % value["local_text"],
        "utc: %s" % value["utc"],
        "epoch: %s" % value["epoch"],
        "valid: %s" % ("true" if value["valid"] else "false"),
    ]
    if not value["valid"]:
        lines.append(
            "note=The RTC year is before 2025; NTP may not have synchronized "
            "since boot. Do not treat this as an accurate wall-clock time.")
    return "\n".join(lines)


class TimeSync:
    """Best-effort automatic NTP synchronization for a connected network."""

    def __init__(self, config, network, clock=None):
        self.config = config
        self.network = network
        self.clock = clock or _ticks_ms
        self.retry_at = 0
        self.last_error = ""

    def _interval_s(self):
        value = _int_config(
            self.config, "ntp_sync_interval_s", DEFAULT_SYNC_INTERVAL_S)
        return max(0, min(value, MAX_SYNC_INTERVAL_S))

    def _network_age_ms(self):
        getter = getattr(self.network, "time_sync_age_ms", None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def maybe_sync(self, force=False):
        """Refresh the RTC when due; return True only when NTP ran successfully."""
        try:
            status = self.network.status()
        except Exception:
            return False
        if not (status.get("sta_connected") or status.get("connected")):
            return False
        age_ms = self._network_age_ms()
        interval_s = self._interval_s()
        if age_ms is not None and not force:
            if interval_s <= 0 or age_ms < interval_s * 1000:
                return False
        now = self.clock()
        if not force and self.retry_at:
            if _ticks_diff(now, self.retry_at) < 0:
                return False
        try:
            result = self.network.sync_time(
                self.config.get("ntp_server", "pool.ntp.org"),
                force=(age_ms is not None),
            )
            self.retry_at = 0
            self.last_error = ""
            return result is not False
        except Exception as exc:
            self.retry_at = _ticks_add(now, RETRY_DELAY_MS)
            self.last_error = str(exc)
            return False
