import unittest
from unittest import mock

from harness.agent import (
    Agent,
    builtin_tool_prompt,
    parse_tool_call,
)
from harness.http_client import HTTPResponse
from harness.http_tool import (
    HttpTool,
    HttpToolError,
    is_public_ipv4,
    resolve_public_host,
    validate_headers,
    validate_url,
)
from tests.support import DummyBoard, DummyConfig


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, body=b"", headers=None, timeout=60,
                max_response=24576, tls_verify=True, ca_file="/ca.pem",
                connect_address=None):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers or {},
            "timeout": timeout,
            "max_response": max_response,
            "tls_verify": tls_verify,
            "ca_file": ca_file,
            "connect_address": connect_address,
        })
        return self.responses.pop(0)


class FakeWorkspace:
    def __init__(self):
        self.writes = []

    def write_bytes(self, path, data, limit=None, overwrite=True):
        if limit is not None and len(data) > limit:
            raise ValueError("too large")
        self.writes.append((path, bytes(data), limit))
        return {"path": "/workspace/" + path, "bytes": len(data)}


def config(**overrides):
    values = {
        "http_tool_enabled": True,
        "http_tool_timeout": 30,
        "http_tool_response_limit": 262144,
        "http_tool_output_limit": 8192,
        "http_tool_max_redirects": 3,
        "http_tool_allow_http": False,
        "http_tool_allowed_ports": [80, 443],
        "http_tool_max_url_chars": 512,
        "http_tool_max_headers": 8,
        "http_tool_header_chars": 512,
        "http_tool_download_limit": 65536,
        "tls_verify": True,
        "ca_file": "/ca.pem",
    }
    values.update(overrides)
    return DummyConfig(**values)


class UrlValidationTests(unittest.TestCase):
    def test_public_and_private_ipv4(self):
        self.assertTrue(is_public_ipv4("1.1.1.1"))
        self.assertTrue(is_public_ipv4("8.8.8.8"))
        for value in (
            "127.0.0.1", "10.0.0.1", "172.16.0.1", "172.31.255.255",
            "192.168.1.1", "169.254.169.254", "100.64.0.1",
            "0.0.0.0", "224.0.0.1", "255.255.255.255",
            "192.0.0.9", "192.0.2.1", "192.88.99.1", "198.18.0.1",
            "198.51.100.1", "203.0.113.1",
        ):
            self.assertFalse(is_public_ipv4(value), value)

    def test_urls_reject_private_and_plain_http(self):
        cfg = config()
        self.assertEqual(
            ("https", "1.1.1.1", 443, "/"),
            validate_url("https://1.1.1.1/", cfg),
        )
        with self.assertRaises(HttpToolError):
            validate_url("https://127.0.0.1/", cfg)
        with self.assertRaises(HttpToolError):
            validate_url("https://169.254.169.254/latest/meta-data", cfg)
        with self.assertRaises(HttpToolError):
            validate_url("http://1.1.1.1/", cfg)
        with self.assertRaises(HttpToolError):
            validate_url("https://1.1.1.1:8443/", cfg)
        with self.assertRaises(HttpToolError):
            validate_url("https://user:pass@1.1.1.1/", cfg)
        with self.assertRaises(HttpToolError):
            validate_url("https://localhost/", cfg)
        self.assertEqual(
            ("http", "1.1.1.1", 80, "/"),
            validate_url("http://1.1.1.1/", config(http_tool_allow_http=True)),
        )

    def test_dns_resolution_blocks_private_addresses(self):
        private = [(2, 1, 6, "", ("192.168.1.10", 443))]
        with mock.patch("harness.http_tool.socket.getaddrinfo", return_value=private):
            with self.assertRaises(HttpToolError):
                resolve_public_host("example.com", 443)
        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with mock.patch("harness.http_tool.socket.getaddrinfo", return_value=public):
            self.assertEqual(
                ["93.184.216.34"], resolve_public_host("example.com", 443)
            )

    def test_headers_reject_managed_fields_and_newlines(self):
        cfg = config()
        self.assertEqual(
            {"Accept": "text/plain"},
            validate_headers({"Accept": "text/plain"}, cfg),
        )
        for value in (
            {"Host": "evil"},
            {"Content-Length": "1"},
            {"X-Test": "a\r\nInjected: 1"},
            {"X-Test\r\nInjected": "1"},
            {"X Test": "1"},
            {"X-Test ": "1"},
            {"X:Test": "1"},
        ):
            with self.assertRaises(HttpToolError):
                validate_headers(value, cfg)


class HttpToolTests(unittest.TestCase):
    def test_get_text_and_truncation(self):
        http = FakeHttp([
            HTTPResponse(200, {"content-type": "text/plain"}, b"hello world"),
        ])
        result = HttpTool(config(http_tool_output_limit=5), http).run({
            "action": "get", "url": "https://1.1.1.1/",
        })
        self.assertTrue(result.ok, result.error)
        self.assertIn("http_status: 200", result.output)
        self.assertIn("truncated: true", result.output)
        self.assertIn("hello", result.output)
        self.assertNotIn("world", result.output)
        self.assertEqual("GET", http.calls[0]["method"])
        self.assertEqual(("1.1.1.1", 443), http.calls[0]["connect_address"])

    def test_dns_result_is_pinned_for_the_connection(self):
        public = [(2, 1, 6, "", ("93.184.216.34", 443))]
        http = FakeHttp([
            HTTPResponse(200, {"content-type": "text/plain"}, b"ok"),
        ])
        with mock.patch("harness.http_tool.socket.getaddrinfo",
                        return_value=public):
            result = HttpTool(config(), http).run({
                "action": "get", "url": "https://example.com/page",
            })
        self.assertTrue(result.ok, result.error)
        # TLS SNI and Host still use the hostname, but connect() must use the
        # exact address that passed the public-IP validation.
        self.assertEqual("https://example.com/page", http.calls[0]["url"])
        self.assertEqual(("93.184.216.34", 443),
                         http.calls[0]["connect_address"])

    def test_get_rejects_binary_and_http_errors(self):
        http = FakeHttp([
            HTTPResponse(200, {"content-type": "application/octet-stream"}, b"\x00\x01"),
        ])
        result = HttpTool(config(), http).run({
            "action": "get", "url": "https://1.1.1.1/file.bin",
        })
        self.assertFalse(result.ok)
        self.assertIn("action=download", result.error)
        http = FakeHttp([
            HTTPResponse(404, {"content-type": "text/plain"}, b"not found"),
        ])
        result = HttpTool(config(), http).run({
            "action": "get", "url": "https://1.1.1.1/missing",
        })
        self.assertFalse(result.ok)
        self.assertIn("HTTP 404", result.error)

    def test_head_returns_selected_headers(self):
        http = FakeHttp([
            HTTPResponse(200, {
                "content-type": "application/json",
                "content-length": "42",
                "server": "demo",
            }, b""),
        ])
        result = HttpTool(config(), http).run({
            "action": "head", "url": "https://1.1.1.1/api",
        })
        self.assertTrue(result.ok, result.error)
        self.assertIn("content-type: application/json", result.output)
        self.assertIn("content-length: 42", result.output)
        self.assertEqual("HEAD", http.calls[0]["method"])

    def test_redirects_are_validated(self):
        http = FakeHttp([
            HTTPResponse(302, {"location": "https://10.0.0.1/"}, b""),
        ])
        result = HttpTool(config(), http).run({
            "action": "get", "url": "https://1.1.1.1/",
        })
        self.assertFalse(result.ok)
        self.assertIn("blocked", result.error)
        self.assertEqual(1, len(http.calls))

    def test_cross_origin_redirect_drops_caller_headers(self):
        http = FakeHttp([
            HTTPResponse(302, {"location": "https://8.8.8.8/next"}, b""),
            HTTPResponse(200, {"content-type": "text/plain"}, b"ok"),
        ])
        result = HttpTool(config(), http).run({
            "action": "get", "url": "https://1.1.1.1/",
            "headers": {"Authorization": "Bearer secret",
                        "X-Custom": "value"},
        })
        self.assertTrue(result.ok, result.error)
        self.assertEqual(
            {"Authorization": "Bearer secret", "X-Custom": "value"},
            http.calls[0]["headers"])
        self.assertEqual({}, http.calls[1]["headers"])

    def test_same_origin_redirect_keeps_caller_headers(self):
        http = FakeHttp([
            HTTPResponse(302, {"location": "/next"}, b""),
            HTTPResponse(200, {"content-type": "text/plain"}, b"ok"),
        ])
        result = HttpTool(config(), http).run({
            "action": "get", "url": "https://1.1.1.1/",
            "headers": {"X-Custom": "value"},
        })
        self.assertTrue(result.ok, result.error)
        self.assertEqual({"X-Custom": "value"}, http.calls[1]["headers"])

    def test_https_to_http_redirect_is_blocked(self):
        http = FakeHttp([
            HTTPResponse(302, {"location": "http://1.1.1.1/next"}, b""),
        ])
        result = HttpTool(config(http_tool_allow_http=True), http).run({
            "action": "get", "url": "https://1.1.1.1/",
        })
        self.assertFalse(result.ok)
        self.assertIn("plain HTTP is blocked", result.error)
        self.assertEqual(1, len(http.calls))

    def test_download_writes_workspace_and_checks_limit(self):
        workspace = FakeWorkspace()
        http = FakeHttp([
            HTTPResponse(200, {"content-type": "application/octet-stream"}, b"abc"),
        ])
        result = HttpTool(config(), http, workspace).run({
            "action": "download", "url": "https://1.1.1.1/file.bin",
            "path": "downloads/file.bin",
        })
        self.assertTrue(result.ok, result.error)
        self.assertEqual("downloads/file.bin", workspace.writes[0][0])
        self.assertEqual(b"abc", workspace.writes[0][1])
        http = FakeHttp([
            HTTPResponse(200, {"content-type": "text/plain"}, b"x" * 20),
        ])
        result = HttpTool(
            config(http_tool_download_limit=5), http, workspace
        ).run({
            "action": "download", "url": "https://1.1.1.1/file.txt",
        })
        self.assertFalse(result.ok)
        self.assertIn("limit is 5 bytes", result.error)

    def test_disabled_tool(self):
        result = HttpTool(config(http_tool_enabled=False), FakeHttp([])).run({
            "action": "get", "url": "https://1.1.1.1/",
        })
        self.assertFalse(result.ok)
        self.assertIn("disabled", result.error)


class AgentHttpToolTests(unittest.TestCase):
    def test_agent_routes_http_tool_and_prompt_lists_it(self):
        tool_response = (
            "```tool\n"
            '{"name":"__http","args":{"action":"get","url":"https://1.1.1.1/"}}\n'
            "```"
        )
        http = FakeHttp([
            HTTPResponse(200, {"content-type": "text/plain"}, b"page body"),
        ])
        agent = Agent(
            DummyConfig(stream=False), FakeLLM(tool_response, "Done"),
            DummyBoard(), http=http,
        )
        explanation, has_code = agent.ask("Fetch it")
        self.assertEqual("Done", explanation)
        self.assertFalse(has_code)
        self.assertIn("page body", agent.history[-2]["content"])
        self.assertIn(
            "__http", builtin_tool_prompt(DummyConfig(), None, None, None)
        )
        _, call = parse_tool_call(
            "```tool\n"
            '{"name":"__http","args":{"action":"head","url":"https://1.1.1.1/"}}\n'
            "```"
        )
        self.assertEqual("__http", call["name"])


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False, timeout=None):
        return self.responses.pop(0)


if __name__ == "__main__":
    unittest.main()
