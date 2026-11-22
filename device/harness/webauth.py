"""Password hashing, RAM sessions and login throttling for the web console."""

import os
import time

try:
    import _thread
except ImportError:
    _thread = None


class WebAuthError(Exception):
    status = 400
    code = "auth"
    retry_after = None


def auth_error(message, code="auth", status=400, retry_after=None):
    error = WebAuthError(message)
    error.code = code
    error.status = status
    error.retry_after = retry_after
    return error


def now_ms():
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


def sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
    else:
        time.sleep(value / 1000.0)


def _hex(data):
    result = []
    for byte in data:
        if not isinstance(byte, int):
            byte = ord(byte)
        result.append("%02x" % byte)
    return "".join(result)


def _unhex(value):
    try:
        out = bytearray()
        for index in range(0, len(value), 2):
            out.append(int(value[index:index + 2], 16))
        return bytes(out)
    except (ValueError, TypeError):
        return None


def is_hex(value, length=None):
    if not isinstance(value, str):
        return False
    if length is not None and len(value) != length:
        return False
    for char in value:
        if char not in "0123456789abcdef":
            return False
    return True


def constant_time_equal(left, right):
    left = left if isinstance(left, bytes) else str(left).encode()
    right = right if isinstance(right, bytes) else str(right).encode()
    if len(left) != len(right):
        return False
    diff = 0
    for index in range(len(left)):
        diff |= left[index] ^ right[index]
    return diff == 0


def random_bytes(count, fallback_seed=None):
    try:
        data = os.urandom(count)
        if data:
            return bytes(data)
    except (AttributeError, OSError):
        pass
    try:
        import machine
        seed = bytes(machine.unique_id())
    except Exception:
        seed = b"esp32"
    if fallback_seed is not None:
        seed += str(fallback_seed).encode()
    seed += str(now_ms()).encode()
    out = bytearray()
    counter = 0
    while len(out) < count:
        try:
            import hashlib
            digest = hashlib.sha256(seed + str(counter).encode()).digest()
        except ImportError:
            digest = bytes((seed[index % len(seed)] ^ (counter + index)) % 256
                           for index in range(32))
        out.extend(digest)
        counter += 1
    return bytes(out[:count])


def _digest_hex(data):
    try:
        import hashlib
    except ImportError:
        raise auth_error("hashlib is unavailable; web login is disabled", "internal", 500)
    digest = hashlib.sha256(data).digest()
    return _hex(digest)


def hash_password(password, salt):
    return _digest_hex((salt + ":" + password).encode())


def new_salt(random_fn=None):
    random_fn = random_fn or random_bytes
    return _hex(random_fn(16))


def new_token(random_fn=None):
    random_fn = random_fn or random_bytes
    return _hex(random_fn(16))


def parse_cookie(header, name="harness_session"):
    if not header:
        return None
    for part in header.split(";"):
        part = part.strip()
        if not part:
            continue
        key, separator, value = part.partition("=")
        if separator and key.strip() == name:
            token = value.strip()
            if is_hex(token, 32):
                return token
    return None


def cookie_header(token, max_age):
    return ("harness_session=%s; Path=/; Max-Age=%s; HttpOnly; SameSite=Strict"
            % (token, max_age))


def expired_cookie_header():
    return "harness_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"


class SessionStore:
    def __init__(self, max_sessions=8, ttl_s=43200, random_fn=None, clock=None):
        self.max_sessions = max(1, int(max_sessions))
        self.ttl_ms = max(1, int(ttl_s)) * 1000
        self.random_fn = random_fn or random_bytes
        self.clock = clock or now_ms
        self.sessions = {}
        self.lock = _thread.allocate_lock() if _thread is not None else None

    def _locked(self):
        if self.lock is None:
            return False
        self.lock.acquire()
        return True

    def _unlock(self, acquired):
        if acquired:
            self.lock.release()

    def _prune(self, now):
        expired = []
        for token, expiry in self.sessions.items():
            if ticks_diff(now, expiry) >= 0:
                expired.append(token)
        for token in expired:
            del self.sessions[token]

    def create(self):
        now = self.clock()
        acquired = self._locked()
        try:
            self._prune(now)
            while len(self.sessions) >= self.max_sessions:
                oldest = None
                oldest_expiry = None
                for token, expiry in self.sessions.items():
                    if oldest is None or ticks_diff(expiry, oldest_expiry) < 0:
                        oldest = token
                        oldest_expiry = expiry
                if oldest is None:
                    break
                del self.sessions[oldest]
            token = new_token(self.random_fn)
            self.sessions[token] = ticks_add(now, self.ttl_ms)
            return token
        finally:
            self._unlock(acquired)

    def valid(self, token):
        if not is_hex(token, 32):
            return False
        now = self.clock()
        acquired = self._locked()
        try:
            expiry = self.sessions.get(token)
            if expiry is None:
                return False
            if ticks_diff(now, expiry) >= 0:
                del self.sessions[token]
                return False
            return True
        finally:
            self._unlock(acquired)

    def remove(self, token):
        acquired = self._locked()
        try:
            self.sessions.pop(token, None)
        finally:
            self._unlock(acquired)

    def clear(self):
        acquired = self._locked()
        try:
            self.sessions.clear()
        finally:
            self._unlock(acquired)

    def count(self):
        acquired = self._locked()
        try:
            self._prune(self.clock())
            return len(self.sessions)
        finally:
            self._unlock(acquired)


class LoginLimiter:
    def __init__(self, limit=5, lock_ms=30000, max_ips=8, clock=None):
        self.limit = max(1, int(limit))
        self.lock_ms = max(1, int(lock_ms))
        self.max_ips = max(1, int(max_ips))
        self.clock = clock or now_ms
        self.entries = {}

    def _entry(self, ip):
        now = self.clock()
        entry = self.entries.get(ip)
        if entry is None:
            if len(self.entries) >= self.max_ips:
                oldest_ip = None
                oldest = None
                for key, value in self.entries.items():
                    if oldest is None or ticks_diff(value["seen"], oldest) < 0:
                        oldest_ip = key
                        oldest = value["seen"]
                if oldest_ip is not None:
                    del self.entries[oldest_ip]
            entry = {"count": 0, "locked_until": None, "level": 0, "seen": now}
            self.entries[ip] = entry
        entry["seen"] = now
        return entry

    def retry_after(self, ip):
        entry = self._entry(ip)
        now = self.clock()
        if entry["locked_until"] is None:
            return 0
        remaining = ticks_diff(entry["locked_until"], now)
        if remaining > 0:
            return (remaining + 999) // 1000
        return 0

    def failure(self, ip):
        entry = self._entry(ip)
        now = self.clock()
        if entry["locked_until"] is not None:
            remaining = ticks_diff(entry["locked_until"], now)
            if remaining > 0:
                return max(1, (remaining + 999) // 1000)
        entry["count"] += 1
        if entry["count"] >= self.limit:
            delay = self.lock_ms * (2 ** min(entry["level"], 4))
            entry["locked_until"] = ticks_add(now, delay)
            entry["count"] = 0
            entry["level"] += 1
            return (delay + 999) // 1000
        return 0

    def success(self, ip):
        self.entries.pop(ip, None)


class WebAuth:
    def __init__(self, config, random_fn=None, clock=None, sleep_fn=None):
        self.config = config
        self.random_fn = random_fn or random_bytes
        self.clock = clock or now_ms
        self.sleep_fn = sleep_fn or sleep_ms
        self.sessions = SessionStore(
            config.get("web_max_sessions", 8),
            config.get("web_session_ttl_s", 43200),
            self.random_fn,
            self.clock,
        )
        self.limiter = LoginLimiter(
            config.get("web_login_fail_limit", 5),
            config.get("web_login_lock_ms", 30000),
            8,
            self.clock,
        )

    def configured(self):
        return bool(self.config.secret("web_password_hash")
                    and self.config.secret("web_password_salt"))

    def password_credentials(self, password):
        self._validate_password(password)
        salt = new_salt(self.random_fn)
        return salt, hash_password(password, salt)

    def password_saved(self):
        self.sessions.clear()
        return self.sessions.create()

    def set_password(self, password):
        salt, digest = self.password_credentials(password)
        self.config.set_secret("web_password_salt", salt, save=False)
        self.config.set_secret("web_password_hash", digest, save=False)
        self.config.save_secrets()
        return self.password_saved()

    def clear_password(self):
        self.config.set_secret("web_password_hash", "", save=False)
        self.config.set_secret("web_password_salt", "", save=False)
        self.config.save_secrets()
        self.sessions.clear()

    def verify(self, password):
        salt = self.config.secret("web_password_salt")
        digest = self.config.secret("web_password_hash")
        if not salt or not digest:
            return False
        return constant_time_equal(hash_password(password, salt), digest)

    def login(self, password, ip):
        if not self.configured():
            raise auth_error("Web setup is required", "setup_required", 409)
        retry = self.limiter.retry_after(ip)
        if retry:
            raise auth_error("Too many login failures", "rate_limited", 429, retry)
        if not self.verify(password):
            retry = self.limiter.failure(ip)
            self.sleep_fn(250)
            if retry:
                raise auth_error("Too many login failures", "rate_limited", 429, retry)
            raise auth_error("Invalid password", "auth", 401)
        self.limiter.success(ip)
        return self.sessions.create()

    def logout(self, token):
        self.sessions.remove(token)

    def valid(self, token):
        return self.sessions.valid(token)

    def clear_sessions(self):
        self.sessions.clear()

    def session_count(self):
        return self.sessions.count()

    @staticmethod
    def _validate_password(password):
        if not isinstance(password, str):
            raise auth_error("Password must be text", "invalid", 400)
        if len(password) < 8 or len(password) > 128:
            raise auth_error("Password must be 8-128 characters", "invalid", 400)
