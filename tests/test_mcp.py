import json
import os
import tempfile
import unittest

from harness.mcp import (
    MCPClient,
    MCPError,
    MCPManager,
    MCPStore,
    _sanitize_name,
)
from tests.support import DummyConfig


class FakeReader:
    def __init__(self, body=b"", status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.lines = body.splitlines(True)
        self.bytes_read = 0
        self.closed = False

    def readline(self, max_line=8192):
        if not self.lines:
            return b""
        line = self.lines.pop(0)
        self.bytes_read += len(line)
        return line

    def read_all(self, limit):
        data = self.body[:limit]
        self.bytes_read += len(data)
        return data

    def close(self):
        self.closed = True


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post_stream(self, url, body, headers, **kwargs):
        self.calls.append((url, json.loads(body), headers, kwargs))
        if not self.responses:
            raise AssertionError("unexpected MCP request: %s" % body)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def json_response(value, status=200, headers=None):
    return FakeReader(
        json.dumps(value).encode(), status, headers or {})


def sse_response(events, status=200, headers=None):
    lines = []
    for event in events:
        lines.append(("data: %s\n\n" % json.dumps(event)).encode())
    merged = {"content-type": "text/event-stream"}
    merged.update(headers or {})
    return FakeReader(b"".join(lines), status, merged)


def server(**overrides):
    value = {
        "name": "demo",
        "url": "https://mcp.example/mcp",
        "headers": {"Authorization": "Bearer secret"},
        "enabled": True,
        "auto": False,
        "timeout_s": 10,
        "protocol": "auto",
        "max_tools": 32,
    }
    value.update(overrides)
    return value


def init_sequence(tools=None, session="session-1"):
    tools = tools or [{
        "name": "echo",
        "description": "Echo text",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }]
    return [
        json_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "demo", "version": "1"},
            },
        }, headers={"mcp-session-id": session}),
        FakeReader(b"", 202),
        json_response({
            "jsonrpc": "2.0", "id": 2,
            "result": {"tools": tools},
        }),
    ]


class MCPClientTests(unittest.TestCase):
    def client(self, responses, config=None, **overrides):
        http = FakeHttp(responses)
        return MCPClient(server(**overrides), config or DummyConfig(), http), http

    def test_initialize_session_and_tools(self):
        client, http = self.client(init_sequence())
        client.initialize()
        self.assertEqual("2025-11-25", client.protocol_version)
        self.assertEqual("session-1", client.session_id)
        tools = client.list_tools()
        self.assertEqual("echo", tools[0]["name"])
        init_call, notify_call, tools_call = http.calls
        self.assertEqual("initialize", init_call[1]["method"])
        self.assertNotIn("MCP-Session-Id", init_call[2])
        self.assertEqual("notifications/initialized", notify_call[1]["method"])
        self.assertEqual("session-1", notify_call[2]["MCP-Session-Id"])
        self.assertEqual("2025-11-25", notify_call[2]["MCP-Protocol-Version"])
        self.assertEqual("tools/list", tools_call[1]["method"])
        self.assertEqual("application/json, text/event-stream",
                         tools_call[2]["Accept"])
        self.assertEqual("Bearer secret", tools_call[2]["Authorization"])

    def test_sse_tools_list(self):
        responses = init_sequence()[:2] + [
            sse_response([
                {"jsonrpc": "2.0", "method": "notifications/progress",
                 "params": {"progress": 1}},
                {"jsonrpc": "2.0", "id": 2,
                 "result": {"tools": [{"name": "read", "description": "Read"}]}},
            ])
        ]
        client, _ = self.client(responses)
        client.initialize()
        tools = client.list_tools()
        self.assertEqual("read", tools[0]["name"])

    def test_tools_call_formats_text_and_structured_content(self):
        responses = init_sequence()[:2] + [
            json_response({
                "jsonrpc": "2.0", "id": 2,
                "result": {
                    "content": [{"type": "text", "text": "hello"}],
                    "structuredContent": {"answer": 42},
                    "isError": False,
                },
            })
        ]
        client, _ = self.client(responses)
        client.initialize()
        result = client.call_tool("echo", {"text": "hi"})
        self.assertTrue(result.ok)
        self.assertIn("hello", result.output)
        self.assertNotIn("answer", result.output)

    def test_tool_error_is_reported(self):
        responses = init_sequence()[:2] + [
            json_response({
                "jsonrpc": "2.0", "id": 2,
                "result": {
                    "content": [{"type": "text", "text": "bad input"}],
                    "isError": True,
                },
            })
        ]
        client, _ = self.client(responses)
        client.initialize()
        result = client.call_tool("echo", {})
        self.assertFalse(result.ok)
        self.assertIn("bad input", result.output)
        self.assertIn("isError", result.error)

    def test_pagination(self):
        responses = init_sequence()[:2] + [
            json_response({
                "jsonrpc": "2.0", "id": 2,
                "result": {"tools": [{"name": "one"}], "nextCursor": "next"},
            }),
            json_response({
                "jsonrpc": "2.0", "id": 3,
                "result": {"tools": [{"name": "two"}]},
            }),
        ]
        client, _ = self.client(responses)
        client.initialize()
        tools = client.list_tools()
        self.assertEqual(["one", "two"], [tool["name"] for tool in tools])

    def test_session_expiry_reinitializes_once(self):
        responses = init_sequence()[:2] + [
            json_response({
                "jsonrpc": "2.0", "id": 2,
                "error": {"code": -32600, "message": "session expired"},
            }, status=404),
            json_response({
                "jsonrpc": "2.0", "id": 3,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                },
            }, headers={"mcp-session-id": "session-2"}),
            FakeReader(b"", 202),
            json_response({
                "jsonrpc": "2.0", "id": 4,
                "result": {"tools": [{"name": "echo"}]},
            }),
        ]
        client, http = self.client(responses)
        client.initialize()
        tools = client.list_tools()
        self.assertEqual("echo", tools[0]["name"])
        self.assertEqual(6, len(http.calls))

    def test_auth_error_is_not_retried(self):
        client, http = self.client([
            json_response({
                "jsonrpc": "2.0", "id": 1,
                "error": {"code": -32000, "message": "unauthorized"},
            }, status=401),
        ])
        with self.assertRaises(MCPError) as caught:
            client.initialize()
        self.assertTrue(caught.exception.auth_required)
        self.assertEqual(1, len(http.calls))

    def test_modern_protocol_headers_and_meta(self):
        client, http = self.client([
            json_response({
                "jsonrpc": "2.0", "id": 1,
                "result": {"tools": [{"name": "echo"}]},
            }),
        ], protocol="2026-07-28")
        client.initialize()
        tools = client.list_tools()
        self.assertEqual("echo", tools[0]["name"])
        method, body, headers, _ = http.calls[0]
        self.assertEqual("tools/list", body["method"])
        self.assertEqual("2026-07-28", headers["MCP-Protocol-Version"])
        self.assertEqual("tools/list", headers["Mcp-Method"])
        self.assertEqual(
            "2026-07-28",
            body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"],
        )

    def test_auto_falls_back_to_modern_on_protocol_error(self):
        client, http = self.client([
            json_response({
                "jsonrpc": "2.0", "id": 1,
                "error": {"code": -32022, "message": "Unsupported protocol version"},
            }, status=400),
            json_response({
                "jsonrpc": "2.0", "id": 2,
                "result": {"tools": [{"name": "echo"}]},
            }),
        ])
        client.initialize()
        self.assertTrue(client.modern)
        tools = client.list_tools()
        self.assertEqual("echo", tools[0]["name"])
        self.assertEqual(2, len(http.calls))
        self.assertEqual("2026-07-28",
                         http.calls[1][2]["MCP-Protocol-Version"])

    def test_resource_and_prompt_formatting(self):
        responses = init_sequence()[:2] + [
            json_response({
                "jsonrpc": "2.0", "id": 2,
                "result": {"contents": [{"uri": "file:///a", "text": "content"}]},
            }),
            json_response({
                "jsonrpc": "2.0", "id": 3,
                "result": {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]},
            }),
        ]
        client, _ = self.client(responses)
        client.initialize()
        self.assertEqual("content", client.read_resource("file:///a"))
        self.assertIn("user: hi", client.get_prompt("demo"))


class MCPStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = DummyConfig(mcp_file="/mcp.json")

    def test_public_redacts_header_values(self):
        store = MCPStore(self.directory.name, self.config)
        saved = store.save([server()])
        public = store.public(saved)
        self.assertTrue(public[0]["headers_set"])
        self.assertEqual(["Authorization"], public[0]["header_names"])
        self.assertNotIn("Bearer secret", json.dumps(public))

    def test_merge_preserves_omitted_headers(self):
        store = MCPStore(self.directory.name, self.config)
        store.save([server()])
        merged = store.merge([{
            "name": "demo", "url": "https://mcp.example/other",
            "enabled": True, "auto": False,
        }])
        self.assertEqual("Bearer secret",
                         merged[0]["headers"]["Authorization"])

    def test_reserved_header_is_rejected(self):
        store = MCPStore(self.directory.name, self.config)
        with self.assertRaises(MCPError):
            store.save([server(headers={"Content-Type": "text/plain"})])

    def test_invalid_server_name_is_rejected(self):
        store = MCPStore(self.directory.name, self.config)
        with self.assertRaises(MCPError):
            store.save([server(name="bad name")])

    def test_missing_file_returns_empty(self):
        store = MCPStore(self.directory.name, self.config)
        self.assertEqual([], store.load())

    def test_sanitize_name(self):
        self.assertEqual("a_b_c", _sanitize_name("a.b c", "x"))
        self.assertEqual("tool", _sanitize_name("...", "tool"))


class MCPManagerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = DummyConfig(mcp_file="/mcp.json")
        self.store = MCPStore(self.directory.name, self.config)
        self.store.save([server()])

    def test_refresh_prompt_and_confirmation(self):
        http = FakeHttp(init_sequence() + [
            json_response({
                "jsonrpc": "2.0", "id": 3,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }),
        ])
        confirmed = []
        manager = MCPManager(
            self.config, http, self.store,
            on_confirm=lambda server_name, tool, args: confirmed.append(tool) or True,
        )
        manager.refresh()
        self.assertEqual(1, manager.status()["tools"])
        prompt = manager.prompt()
        self.assertIn("mcp__demo__echo", prompt)
        result = manager.call("mcp__demo__echo", {"text": "hi"})
        self.assertTrue(result.ok)
        self.assertEqual(["mcp__demo__echo"], confirmed)

    def test_read_only_tool_skips_confirmation(self):
        http = FakeHttp(init_sequence(tools=[{
            "name": "read",
            "description": "Read only",
            "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
        }]) + [
            json_response({
                "jsonrpc": "2.0", "id": 3,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }),
        ])
        manager = MCPManager(
            self.config, http, self.store,
            on_confirm=lambda *args: self.fail("confirmation should be skipped"),
        )
        manager.refresh()
        result = manager.call("mcp__demo__read", {})
        self.assertTrue(result.ok)

    def test_confirmation_denied_does_not_call_tool(self):
        http = FakeHttp(init_sequence())
        manager = MCPManager(
            self.config, http, self.store,
            on_confirm=lambda *args: False,
        )
        manager.refresh()
        result = manager.call("mcp__demo__echo", {})
        self.assertFalse(result.ok)
        self.assertIn("cancelled", result.error)
        self.assertEqual(3, len(http.calls))

    def test_unknown_tool_is_reported(self):
        http = FakeHttp(init_sequence())
        manager = MCPManager(self.config, http, self.store)
        manager.refresh()
        result = manager.call("mcp__demo__missing", {})
        self.assertFalse(result.ok)
        self.assertIn("Unknown MCP tool", result.error)


if __name__ == "__main__":
    unittest.main()
