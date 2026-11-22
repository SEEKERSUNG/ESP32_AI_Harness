import http.client
import json
import socket
import time
import unittest
from unittest import mock

from harness.web import (
    WebError,
    WebServer,
    WebWriter,
    parse_query,
    read_request,
    unquote,
)


class FakeConn:
    def __init__(self, data=b"", chunk_size=512):
        self.data = data
        self.chunk_size = chunk_size
        self.sent = []
        self.timeouts = []
        self.closed = False

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, size):
        if not self.data:
            return b""
        size = min(size, self.chunk_size)
        chunk = self.data[:size]
        self.data = self.data[size:]
        return chunk

    def send(self, data):
        if self.closed:
            raise OSError("closed")
        self.sent.append(bytes(data))
        return len(data)

    def close(self):
        self.closed = True

    def response(self):
        return b"".join(self.sent)


def request_bytes(method="GET", path="/", headers=None, body=b""):
    headers = headers or {}
    lines = ["%s %s HTTP/1.1" % (method, path)]
    if "Host" not in headers:
        lines.append("Host: 192.168.4.1:8080")
    for name, value in headers.items():
        lines.append("%s: %s" % (name, value))
    if body and "Content-Length" not in headers:
        lines.append("Content-Length: %s" % len(body))
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


class ParseTests(unittest.TestCase):
    def test_unquote_and_query(self):
        self.assertEqual("a b中", unquote("a+b%E4%B8%AD"))
        self.assertEqual("a%zz", unquote("a%zz"))
        path, query = parse_query("/api/history?limit=5&q=%E4%B8%AD&empty=")
        self.assertEqual("/api/history", path)
        self.assertEqual("5", query["limit"])
        self.assertEqual("中", query["q"])
        self.assertEqual("", query["empty"])

    def test_get_request(self):
        conn = FakeConn(request_bytes("GET", "/api/status?x=1"))
        req = read_request(conn, "192.168.4.2")
        self.assertEqual("GET", req.method)
        self.assertEqual("/api/status", req.path)
        self.assertEqual("1", req.query["x"])
        self.assertEqual("192.168.4.1", req.host_name())
        self.assertEqual("192.168.4.2", req.peer_ip)
        self.assertEqual(b"", req.body)

    def test_post_request_and_100_continue(self):
        body = json.dumps({"password": "x"}).encode()
        conn = FakeConn(request_bytes(
            "POST", "/api/login",
            {"Content-Type": "application/json",
             "Expect": "100-continue",
             "Content-Length": str(len(body))},
            body,
        ))
        req = read_request(conn, "1.2.3.4")
        self.assertEqual(body, req.body)
        self.assertEqual(b"HTTP/1.1 100 Continue\r\n\r\n", conn.sent[0])

    def test_missing_length_rejected(self):
        raw = b"POST /api/login HTTP/1.1\r\nHost: x\r\n\r\n"
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(raw), "1.2.3.4")
        self.assertEqual(411, raised.exception.status)

    def test_transfer_encoding_rejected(self):
        raw = ("POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
               "Content-Length: 1\r\n\r\nx").encode()
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(raw), "x")
        self.assertEqual(411, raised.exception.status)

    def test_duplicate_length_rejected(self):
        raw = ("POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 1\r\n"
               "Content-Length: 1\r\n\r\nx").encode()
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(raw), "x")
        self.assertEqual(400, raised.exception.status)

    def test_body_limit_and_early_eof(self):
        with self.assertRaises(WebError) as raised:
            read_request(
                FakeConn(request_bytes("POST", "/", {"Content-Length": "20"}, b"x")),
                "x", body_limit=10,
            )
        self.assertEqual(413, raised.exception.status)
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(
                b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\nab"
            ), "x")
        self.assertEqual(400, raised.exception.status)

    def test_method_and_version_limits(self):
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(request_bytes("PUT", "/")), "x")
        self.assertEqual(405, raised.exception.status)
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(
                b"GET / HTTP/2.0\r\nHost: x\r\n\r\n"
            ), "x")
        self.assertEqual(400, raised.exception.status)

    def test_oversized_headers_rejected(self):
        raw = ("GET / HTTP/1.1\r\nHost: x\r\nX-Long: " + "a" * 9000 +
               "\r\n\r\n").encode()
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(raw, chunk_size=256), "x")
        self.assertEqual(431, raised.exception.status)
        raw = ("GET / HTTP/1.1\r\nHost: x\r\nX-Long: " + "a" * 1100 +
               "\r\n\r\n").encode()
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(raw), "x")
        self.assertEqual(431, raised.exception.status)
        lines = ["GET / HTTP/1.1", "Host: x"]
        for index in range(40):
            lines.append("X-%s: v" % index)
        with self.assertRaises(WebError) as raised:
            read_request(FakeConn(("\r\n".join(lines) + "\r\n\r\n").encode()), "x")
        self.assertEqual(431, raised.exception.status)

    def test_invalid_path_and_header(self):
        with self.assertRaises(WebError):
            read_request(FakeConn(
                "GET /é HTTP/1.1\r\nHost: x\r\n\r\n".encode("utf-8")
            ), "x")
        with self.assertRaises(WebError):
            read_request(FakeConn(b"GET / HTTP/1.1\r\nHost x\r\n\r\n"), "x")

    def test_socket_timeouts_are_bounded_by_total_request_deadline(self):
        class TimeoutConn(FakeConn):
            def recv(self, _size):
                raise OSError(11, "try again")

        with mock.patch("harness.web._ticks_ms", side_effect=[0, 0, 31000]):
            with self.assertRaises(WebError) as raised:
                read_request(TimeoutConn(), "x", timeout=30)
        self.assertEqual(408, raised.exception.status)
        self.assertEqual("timeout", raised.exception.code)


class WriterTests(unittest.TestCase):
    def test_response_framing_and_security_headers(self):
        conn = FakeConn()
        writer = WebWriter(conn)
        writer.send_json(200, {"ok": True})
        raw = conn.response()
        head, body = raw.split(b"\r\n\r\n", 1)
        text = head.decode()
        self.assertIn("HTTP/1.1 200 OK", text)
        self.assertIn("Content-Type: application/json", text)
        self.assertIn("Content-Length: 12", text)
        self.assertIn("Connection: close", text)
        self.assertIn("X-Content-Type-Options: nosniff", text)
        self.assertIn("X-Frame-Options: DENY", text)
        self.assertIn("default-src 'none'", text)
        self.assertEqual({"ok": True}, json.loads(body))

    def test_stream_is_newline_delimited_and_closes(self):
        conn = FakeConn()
        writer = WebWriter(conn)
        self.assertTrue(writer.start_stream())
        self.assertTrue(writer.event({"t": "content", "d": "中"}))
        writer.close()
        raw = conn.response()
        self.assertNotIn(b"Content-Length", raw)
        lines = raw.split(b"\r\n\r\n", 1)[1].split(b"\n")
        self.assertEqual({"t": "content", "d": "中"}, json.loads(lines[0]))
        self.assertTrue(writer.closed)
        self.assertFalse(writer.event({"t": "x"}))
        writer.close()

    def test_send_failure_marks_writer_closed(self):
        class Broken(FakeConn):
            def send(self, data):
                raise OSError("boom")
        writer = WebWriter(Broken())
        with self.assertRaises(OSError):
            writer.send_response(200, "x")
        self.assertTrue(writer.closed)
        self.assertFalse(writer.event({"t": "x"}))


class EchoApp:
    def __init__(self):
        self.ticks = 0
        self.notices = []

    def body_limit(self):
        return 1024

    def tick(self):
        self.ticks += 1

    def queue_notice(self, text):
        self.notices.append(text)

    def handle(self, request, writer):
        if request.path == "/json":
            writer.send_json(200, {"ok": True, "path": request.path})
        elif request.path == "/stream":
            writer.start_stream()
            writer.event({"t": "a"})
            writer.event({"t": "b"})
            writer.close()
        else:
            writer.send_response(200, "hello")


class ServerIntegrationTests(unittest.TestCase):
    def test_localhost_requests_and_streaming(self):
        app = EchoApp()
        server = WebServer(app, host="127.0.0.1", port=0)
        self.assertTrue(server.start(), server.error)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
            conn.request("GET", "/json")
            response = conn.getresponse()
            self.assertEqual(200, response.status)
            self.assertEqual({"ok": True, "path": "/json"},
                             json.loads(response.read()))
            conn.request("GET", "/stream")
            response = conn.getresponse()
            self.assertEqual(200, response.status)
            body = response.read().decode()
            self.assertEqual(
                [{"t": "a"}, {"t": "b"}],
                [json.loads(line) for line in body.splitlines()],
            )
            conn.request("GET", "/")
            response = conn.getresponse()
            self.assertEqual(b"hello", response.read())
            conn.close()
            deadline = time.time() + 2
            while app.ticks < 3 and time.time() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(app.ticks, 3)
        finally:
            server.stop()
        self.assertFalse(server.alive)
        self.assertFalse(server.running)

    def test_start_rejects_stale_alive_state_until_old_thread_exits(self):
        server = WebServer(EchoApp(), host="127.0.0.1", port=0)
        server.alive = True
        server.running = False
        self.assertFalse(server.start())
        self.assertIn("still stopping", server.error)
        self.assertFalse(server.running)

        server.alive = False
        self.assertTrue(server.start(), server.error)
        try:
            self.assertIsNotNone(server.server)
        finally:
            server.stop()
        self.assertFalse(server.alive)


if __name__ == "__main__":
    unittest.main()
