import unittest
from unittest import mock

from harness.http_client import HTTPBodyReader, HTTPError, HttpClient, chunked_complete, decode_chunked, split_url


class BodyReaderTests(unittest.TestCase):
    def test_chunked_body_reader_yields_sse_lines(self):
        chunked = b"16\r\ndata: one\n\ndata: two\n\n\r\n0\r\n\r\n"
        stream = BodyStream(chunked)
        reader = HTTPBodyReader(
            stream,
            {"_status_line": "HTTP/1.1 200 OK", "transfer-encoding": "chunked"},
            b"",
            max_response=1000,
            idle_timeout=5,
            feed=lambda: None,
            trace=lambda message: None,
        )
        self.assertEqual(b"data: one\n", reader.readline())
        self.assertEqual(b"\n", reader.readline())
        self.assertEqual(b"data: two\n", reader.readline())
        self.assertEqual(b"\n", reader.readline())
        self.assertEqual(b"", reader.readline())

    def test_content_length_body_reader(self):
        stream = BodyStream(b"abc\ndef")
        reader = HTTPBodyReader(
            stream,
            {"_status_line": "HTTP/1.1 200 OK", "content-length": "7"},
            b"",
            max_response=100,
            idle_timeout=5,
            feed=lambda: None,
            trace=lambda message: None,
        )
        self.assertEqual(b"abc\n", reader.readline())
        self.assertEqual(b"def", reader.readline())
        self.assertEqual(b"", reader.readline())

    def test_socket_poll_timeout_stays_below_watchdog(self):
        # The idle timeout may be minutes, but the socket must wake often
        # enough for the serial main thread to feed the watchdog.
        class RecordingStream(BodyStream):
            def __init__(self, payload):
                BodyStream.__init__(self, payload)
                self.timeout = None

            def settimeout(self, value):
                self.timeout = value

        stream = RecordingStream(b"")
        HTTPBodyReader(
            stream,
            {"_status_line": "HTTP/1.1 200 OK", "content-length": "0"},
            b"",
            max_response=100,
            idle_timeout=120,
            feed=lambda: None,
            trace=lambda message: None,
        )
        self.assertEqual(1, stream.timeout)

    def test_stream_keeps_running_while_data_arrives(self):
        # A long stream must not be killed by a fixed total timeout: every
        # received byte refreshes the idle timer, even past the old total.
        ticks = [0]

        class TickingStream:
            def __init__(self, payload):
                self.payload = payload

            def recv(self, size):
                ticks[0] += 2000
                if not self.payload:
                    return b""
                chunk = self.payload[:1]
                self.payload = self.payload[1:]
                return chunk

        reader = HTTPBodyReader(
            TickingStream(b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n"),
            {"_status_line": "HTTP/1.1 200 OK", "transfer-encoding": "chunked"},
            b"",
            max_response=1000,
            idle_timeout=5,
            feed=lambda: None,
            trace=lambda message: None,
        )
        with mock.patch.object(HTTPBodyReader, "_ticks", lambda _self: ticks[0]):
            self.assertEqual(b"Wikipedia", reader.read_all(100))

    def test_cancel_check_aborts_before_recv(self):
        class Cancelled(Exception):
            pass

        def cancel():
            raise Cancelled()

        reader = HTTPBodyReader(
            BodyStream(b"data: x\n\n"),
            {"_status_line": "HTTP/1.1 200 OK", "content-length": "9"},
            b"",
            max_response=100,
            idle_timeout=5,
            feed=lambda: None,
            trace=lambda message: None,
            cancel_check=cancel,
        )
        with self.assertRaises(Cancelled):
            reader.readline()

    def test_first_byte_timeout_outlasts_body_idle_timeout(self):
        ticks = [0]

        class SlowPrefillStream:
            def __init__(self):
                self.calls = 0

            def recv(self, size):
                self.calls += 1
                ticks[0] += 5000
                if self.calls < 100:
                    raise OSError(110, "timed out")
                if self.calls == 100:
                    return b"answer"
                return b""

        reader = HTTPBodyReader(
            SlowPrefillStream(),
            {"_status_line": "HTTP/1.1 200 OK"},
            b"",
            max_response=100,
            idle_timeout=5,
            feed=lambda: None,
            trace=lambda message: None,
            first_byte_timeout=600,
        )
        with mock.patch.object(HTTPBodyReader, "_ticks", lambda _self: ticks[0]):
            # 500 s of silence exceeds the 5 s body idle timeout but is inside
            # the 600 s prefill budget.
            self.assertEqual(b"answer", reader.readline())

    def test_first_byte_timeout_is_still_bounded(self):
        ticks = [0]

        class DeadStream:
            def recv(self, size):
                ticks[0] += 5000
                raise OSError(110, "timed out")

        reader = HTTPBodyReader(
            DeadStream(),
            {"_status_line": "HTTP/1.1 200 OK"},
            b"",
            max_response=100,
            idle_timeout=5,
            feed=lambda: None,
            trace=lambda message: None,
            first_byte_timeout=10,
        )
        with mock.patch.object(HTTPBodyReader, "_ticks", lambda _self: ticks[0]):
            with self.assertRaises(HTTPError) as raised:
                reader.readline()
        self.assertIn("SSE idle timeout", str(raised.exception))
        self.assertGreaterEqual(ticks[0], 10000)

    def test_stream_stops_when_data_stops(self):
        ticks = [0]

        class StalledStream:
            def recv(self, size):
                ticks[0] += 6000
                raise OSError(110, "timed out")

        reader = HTTPBodyReader(
            StalledStream(),
            {"_status_line": "HTTP/1.1 200 OK", "content-length": "3"},
            b"",
            max_response=100,
            idle_timeout=5,
            feed=lambda: None,
            trace=lambda message: None,
        )
        with mock.patch.object(HTTPBodyReader, "_ticks", lambda _self: ticks[0]):
            with self.assertRaises(HTTPError) as raised:
                reader.readline()
        self.assertIn("SSE idle timeout", str(raised.exception))


class URLTests(unittest.TestCase):
    def test_https_url_with_base_path(self):
        self.assertEqual(
            ("https", "example.com", 443, "/v1/chat/completions"),
            split_url("https://example.com/v1/chat/completions"),
        )

    def test_http_url_with_port(self):
        self.assertEqual(("http", "localhost", 8080, "/"), split_url("http://localhost:8080"))

    def test_url_rejects_control_characters(self):
        for value in (
            "https://example.com/\r\nX-Injected: 1",
            "https://example.com/\n",
            "https://example.com/\t",
        ):
            with self.assertRaises(HTTPError):
                split_url(value)


class FlakyStream:
    def __init__(self, payload):
        self.payload = payload
        self.first = True

    def recv(self, size):
        if self.first:
            self.first = False
            raise OSError(110, "timed out")
        if self.payload:
            chunk, self.payload = self.payload[:size], self.payload[size:]
            return chunk
        return b""


class OneShotStream:
    def __init__(self, payload):
        self.payload = payload

    def recv(self, size):
        if not self.payload:
            raise AssertionError("recv called after the response was complete")
        chunk, self.payload = self.payload[:size], self.payload[size:]
        return chunk


class BodyStream:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def recv(self, size):
        if self.payload:
            chunk, self.payload = self.payload[:size], self.payload[size:]
            return chunk
        return b""

    def close(self):
        self.closed = True


class KeepAliveStream:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = 0
        self.closed = False

    def send(self, data):
        self.sent += 1
        return len(data)

    def recv(self, size):
        if not self.responses:
            raise AssertionError("unexpected recv")
        response = self.responses.pop(0)
        return response[:size] if len(response) > size else response

    def close(self):
        self.closed = True


class ChunkedTests(unittest.TestCase):
    def test_decodes_multiple_chunks(self):
        data = b"4\r\nWiki\r\n5;ext=yes\r\npedia\r\n0\r\n\r\n"
        self.assertEqual(b"Wikipedia", decode_chunked(data, 100))

    def test_enforces_output_limit(self):
        with self.assertRaises(HTTPError):
            decode_chunked(b"5\r\n12345\r\n0\r\n\r\n", 4)

    def test_parses_chunked_http_response(self):
        raw = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"2\r\nOK\r\n0\r\n\r\n"
        )
        response = HttpClient()._parse_response(bytearray(raw), 100)
        self.assertEqual(200, response.status)
        self.assertEqual(b"OK", response.body)


class ResponseFramingTests(unittest.TestCase):
    def test_receive_retries_socket_timeout_until_response(self):
        stream = FlakyStream(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        raw, reusable = HttpClient()._receive(stream, timeout=1, max_response=100)
        response = HttpClient()._parse_response(raw, 100)
        self.assertEqual(b"OK", response.body)
        self.assertTrue(reusable)

    def test_head_response_does_not_wait_for_content_length_body(self):
        stream = OneShotStream(
            b"HTTP/1.1 200 OK\r\nContent-Length: 577\r\n\r\n"
        )
        raw, reusable = HttpClient()._receive(
            stream, timeout=1, max_response=1000, header_only=True
        )
        response = HttpClient()._parse_response(raw, 1000, header_only=True)
        self.assertEqual(200, response.status)
        self.assertEqual(b"", response.body)
        self.assertFalse(reusable)

    def test_receive_rejects_premature_eof(self):
        stream = FlakyStream(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nabc")
        with self.assertRaises(HTTPError):
            HttpClient()._receive(stream, timeout=1, max_response=100)

    def test_chunked_keepalive_response_does_not_wait_for_close(self):
        stream = OneShotStream(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: keep-alive\r\n\r\n"
            b"2\r\nOK\r\n0\r\n\r\n"
        )
        raw, reusable = HttpClient()._receive(stream, timeout=1, max_response=100)
        response = HttpClient()._parse_response(raw, 100)
        self.assertEqual(b"OK", response.body)
        self.assertTrue(reusable)

    def test_chunked_completion_helper(self):
        self.assertTrue(chunked_complete(b"2\r\nOK\r\n0\r\n\r\n"))
        self.assertFalse(chunked_complete(b"2\r\nOK\r\n0\r\n"))

    def test_content_length_slices_trailing_bytes(self):
        raw = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOKEXTRA"
        response = HttpClient()._parse_response(bytearray(raw), 100)
        self.assertEqual(b"OK", response.body)

    def test_content_length_rejects_short_body(self):
        raw = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nabc"
        with self.assertRaises(HTTPError):
            HttpClient()._parse_response(bytearray(raw), 100)

    def test_negative_content_length_is_rejected(self):
        raw = b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n"
        with self.assertRaises(HTTPError):
            HttpClient()._parse_response(bytearray(raw), 100)

    def test_connection_close_is_not_reusable(self):
        stream = OneShotStream(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
        )
        _, reusable = HttpClient()._receive(stream, timeout=1, max_response=100)
        self.assertFalse(reusable)

    def test_http_client_reuses_keepalive_connection(self):
        response = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
            b"Connection: keep-alive\r\n\r\n2\r\nOK\r\n0\r\n\r\n"
        )
        stream = KeepAliveStream([response, response])
        client = HttpClient()
        opens = []

        def fake_open(scheme, host, port, tls_verify, ca_file,
                      connect_address=None):
            opens.append((host, port))
            client.stream = stream

        client._open_connection = fake_open
        first = client.post("https://example.com/v1/chat/completions", b"{}", {})
        second = client.post("https://example.com/v1/chat/completions", b"{}", {})
        self.assertEqual(b"OK", first.body)
        self.assertEqual(b"OK", second.body)
        self.assertEqual(1, len(opens))
        self.assertEqual(4, stream.sent)
        self.assertFalse(stream.closed)


if __name__ == "__main__":
    unittest.main()
