import json
import pathlib
import tempfile
import unittest

from harness.config import Config
from harness import webauth
from harness.webauth import (
    LoginLimiter,
    SessionStore,
    WebAuth,
    WebAuthError,
    constant_time_equal,
    cookie_header,
    expired_cookie_header,
    hash_password,
    is_hex,
    parse_cookie,
)


class Clock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, delta):
        self.value += delta


class SequenceRandom:
    def __init__(self):
        self.counter = 0

    def __call__(self, count):
        self.counter += 1
        return bytes((self.counter + index) % 256 for index in range(count))


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(self.temp.name)
        self.clock = Clock()
        self.random = SequenceRandom()

    def tearDown(self):
        self.temp.cleanup()

    def auth(self, **overrides):
        for key, value in overrides.items():
            self.config.values[key] = value
        return WebAuth(
            self.config,
            random_fn=self.random,
            clock=self.clock,
            sleep_fn=lambda _ms: None,
        )

    def test_password_is_salted_and_never_stored_plaintext(self):
        auth = self.auth()
        self.assertFalse(auth.configured())
        token = auth.set_password("correct horse battery")
        self.assertTrue(token)
        self.assertTrue(auth.configured())
        self.assertTrue(is_hex(token, 32))
        secrets = json.dumps(self.config.secrets)
        self.assertNotIn("correct horse battery", secrets)
        self.assertIn("web_password_hash", self.config.secrets)
        self.assertTrue(auth.verify("correct horse battery"))
        self.assertFalse(auth.verify("wrong password"))
        self.assertNotEqual(
            hash_password("correct horse battery", self.config.secret("web_password_salt")),
            "correct horse battery",
        )

    def test_password_validation(self):
        auth = self.auth()
        for password in ("short", "x" * 129, 12345678):
            with self.assertRaises(WebAuthError) as raised:
                auth.set_password(password)
            self.assertEqual(400, raised.exception.status)
        auth.set_password("12345678")
        self.assertTrue(auth.verify("12345678"))

    def test_login_rate_limit_and_retry_after(self):
        auth = self.auth(web_login_fail_limit=2, web_login_lock_ms=30000)
        auth.set_password("a-good-password")
        with self.assertRaises(WebAuthError) as first:
            auth.login("bad", "192.168.4.2")
        self.assertEqual(401, first.exception.status)
        self.assertFalse(first.exception.retry_after)
        with self.assertRaises(WebAuthError) as second:
            auth.login("bad", "192.168.4.2")
        self.assertEqual(429, second.exception.status)
        self.assertGreaterEqual(second.exception.retry_after, 1)
        with self.assertRaises(WebAuthError) as locked:
            auth.login("a-good-password", "192.168.4.2")
        self.assertEqual(429, locked.exception.status)
        self.clock.advance(31000)
        token = auth.login("a-good-password", "192.168.4.2")
        self.assertTrue(auth.valid(token))

    def test_limiter_levels_and_max_ips(self):
        clock = Clock()
        limiter = LoginLimiter(limit=1, lock_ms=1000, max_ips=2, clock=clock)
        self.assertEqual(1, limiter.failure("a"))
        self.assertEqual(1, limiter.retry_after("a"))
        self.assertEqual(0, limiter.retry_after("b"))
        clock.advance(1001)
        self.assertEqual(0, limiter.retry_after("a"))
        self.assertEqual(2, limiter.failure("a"))
        self.assertEqual(1, limiter.failure("b"))
        limiter.success("a")
        limiter.failure("c")
        self.assertEqual(2, len(limiter.entries))
        self.assertNotIn("a", limiter.entries)

    def test_sessions_expire_migrate_and_logout(self):
        clock = Clock()
        store = SessionStore(
            max_sessions=2, ttl_s=1, random_fn=SequenceRandom(), clock=clock
        )
        first = store.create()
        self.assertTrue(store.valid(first))
        clock.advance(1001)
        self.assertFalse(store.valid(first))
        self.assertEqual(0, store.count())

        first = store.create()
        second = store.create()
        third = store.create()
        self.assertEqual(2, store.count())
        self.assertFalse(store.valid(first))
        self.assertTrue(store.valid(second))
        self.assertTrue(store.valid(third))
        store.remove(second)
        self.assertFalse(store.valid(second))
        store.clear()
        self.assertEqual(0, store.count())

    def test_rotating_password_revokes_other_sessions(self):
        auth = self.auth()
        first = auth.set_password("first-password")
        second = auth.set_password("second-password")
        self.assertFalse(auth.valid(first))
        self.assertTrue(auth.valid(second))
        self.assertTrue(auth.verify("second-password"))
        auth.clear_password()
        self.assertFalse(auth.configured())
        self.assertFalse(auth.valid(second))
        self.assertEqual(0, auth.session_count())

    def test_cookie_helpers(self):
        header = cookie_header("a" * 32, 120)
        self.assertIn("harness_session=" + "a" * 32, header)
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Strict", header)
        self.assertNotIn("Secure", header)
        self.assertEqual("a" * 32, parse_cookie("x=1; " + header))
        self.assertIsNone(parse_cookie("harness_session=not-hex"))
        self.assertIsNone(parse_cookie("harness_session=" + "A" * 32))
        self.assertIn("Max-Age=0", expired_cookie_header())

    def test_constant_time_equal(self):
        self.assertTrue(constant_time_equal("abc", b"abc"))
        self.assertFalse(constant_time_equal("abc", "abd"))
        self.assertFalse(constant_time_equal("abc", "abcd"))

    def test_webauth_error_attributes(self):
        error = webauth.auth_error("slow down", "rate_limited", 429, 7)
        self.assertEqual("slow down", str(error))
        self.assertEqual(429, error.status)
        self.assertEqual("rate_limited", error.code)
        self.assertEqual(7, error.retry_after)


if __name__ == "__main__":
    unittest.main()
