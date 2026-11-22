import json
import unittest

from harness.http_client import HTTPResponse
from harness.llm import LLMError, OpenAIClient, create_llm
from harness.llm_anthropic import AnthropicClient
from tests.support import DummyConfig


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, body, headers, **kwargs):
        self.calls.append((url, json.loads(body), headers, kwargs))
        return self.response


class FakeStreamReader:
    def __init__(self, lines, status=200, headers=None):
        self.lines = list(lines)
        self.status = status
        self.headers = headers or {}
        self.bytes_read = sum(len(line) for line in lines)
        self.closed = False

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        return b""

    def read_all(self, limit):
        data = b"".join(self.lines)
        self.lines = []
        return data[:limit]

    def close(self):
        self.closed = True


class FakeStreamHttp:
    def __init__(self, lines, status=200, headers=None):
        self.reader = FakeStreamReader(lines, status, headers)
        self.calls = []

    def post_stream(self, url, body, headers, **kwargs):
        self.calls.append((url, json.loads(body), headers, kwargs))
        return self.reader


def anthropic_config(**overrides):
    values = {
        "api_provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model": "claude-opus-5-5",
        "thinking_level": "high",
    }
    values.update(overrides)
    return DummyConfig(**values)


def message_body(content, stop_reason="end_turn"):
    return json.dumps({
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": content,
        "stop_reason": stop_reason,
    }).encode()


class AnthropicTests(unittest.TestCase):
    def test_messages_contract(self):
        http = FakeHttp(HTTPResponse(
            200, {}, message_body([{"type": "text", "text": "hello"}])))
        client = AnthropicClient(anthropic_config(), http)
        result = client.complete([
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "hi"},
        ])
        self.assertEqual("hello", result)
        url, payload, headers, kwargs = http.calls[0]
        self.assertEqual("https://api.anthropic.com/v1/messages", url)
        self.assertEqual("system rules", payload["system"])
        self.assertEqual([{"role": "user", "content": "hi"}], payload["messages"])
        self.assertFalse(payload["stream"])
        self.assertEqual(32768, payload["max_tokens"])
        self.assertNotIn("temperature", payload)
        self.assertEqual(
            {"type": "adaptive", "display": "summarized"}, payload["thinking"])
        self.assertEqual({"effort": "high"}, payload["output_config"])
        self.assertEqual("test-api-key", headers["x-api-key"])
        self.assertEqual("2023-06-01", headers["anthropic-version"])
        self.assertTrue(headers["x-opencode-session"].startswith("ses_"))
        self.assertNotIn("Authorization", headers)
        self.assertEqual(600, kwargs["timeout"])

    def test_native_tool_definition_and_nonstream_call(self):
        body = message_body([
            {"type": "tool_use", "id": "toolu_1", "name": "__workspace",
             "input": {"action": "list"}},
        ], stop_reason="tool_use")
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = AnthropicClient(anthropic_config(), http)
        tools = [{
            "name": "__workspace",
            "description": "workspace",
            "parameters": {"type": "object", "properties": {}},
        }]
        self.assertEqual("", client.complete(
            [{"role": "user", "content": "hi"}], tools=tools))
        payload = http.calls[0][1]
        self.assertEqual("__workspace", payload["tools"][0]["name"])
        self.assertEqual(
            {"type": "object", "properties": {}},
            payload["tools"][0]["input_schema"])
        self.assertEqual("toolu_1", client.last_tool_calls[0]["id"])
        self.assertEqual({"action": "list"}, client.last_tool_calls[0]["args"])
        self.assertEqual("tool_calls", client.last_finish_reason)

    def test_native_tool_messages_are_converted(self):
        body = message_body([{"type": "text", "text": "done"}])
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = AnthropicClient(anthropic_config(), http)
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "toolu_1", "name": "__workspace",
                             "args": {"action": "list"}}]},
            {"role": "tool", "tool_call_id": "toolu_1",
             "name": "__workspace", "content": "- notes.txt"},
        ]
        self.assertEqual("done", client.complete(messages))
        converted = http.calls[0][1]["messages"]
        self.assertEqual("assistant", converted[1]["role"])
        self.assertEqual("tool_use", converted[1]["content"][0]["type"])
        self.assertEqual("toolu_1", converted[1]["content"][0]["id"])
        self.assertEqual("user", converted[2]["role"])
        self.assertEqual("tool_result", converted[2]["content"][0]["type"])
        self.assertEqual(
            "toolu_1", converted[2]["content"][0]["tool_use_id"])

    def test_streamed_native_tool_input_is_accumulated(self):
        lines = [
            b'data: {"type":"content_block_start","index":0,'
            b'"content_block":{"type":"tool_use","id":"toolu_9",'
            b'"name":"__workspace","input":{}}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"input_json_delta","partial_json":'
            b'"{\\"action\\":\\"list\\"}"}}\n',
            b"\n",
            b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}\n',
            b"\n",
            b'data: {"type":"message_stop"}\n',
            b"\n",
        ]
        http = FakeStreamHttp(lines)
        client = AnthropicClient(anthropic_config(), http)
        self.assertEqual("", client.complete_stream([], tools=[]))
        self.assertEqual("toolu_9", client.last_tool_calls[0]["id"])
        self.assertEqual({"action": "list"}, client.last_tool_calls[0]["args"])
        self.assertEqual("tool_calls", client.last_finish_reason)

    def test_disabled_thinking_omits_parameters(self):
        http = FakeHttp(HTTPResponse(
            200, {}, message_body([{"type": "text", "text": "ok"}])))
        client = AnthropicClient(anthropic_config(thinking_level="disabled"), http)
        client.complete([])
        payload = http.calls[0][1]
        self.assertNotIn("thinking", payload)
        self.assertNotIn("output_config", payload)
        self.assertNotIn("temperature", payload)

    def test_deepseek_anthropic_uses_enabled_thinking(self):
        http = FakeHttp(HTTPResponse(
            200, {}, message_body([{"type": "text", "text": "ok"}])))
        client = AnthropicClient(anthropic_config(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash",
            thinking_level="high",
        ), http)
        client.complete([])
        url, payload, headers, kwargs = http.calls[0]
        self.assertEqual("https://api.deepseek.com/anthropic/v1/messages", url)
        self.assertEqual({"type": "enabled"}, payload["thinking"])
        self.assertNotIn("output_config", payload)
        self.assertEqual(600, kwargs["timeout"])

    def test_deepseek_anthropic_disabled_thinking_is_explicit(self):
        http = FakeHttp(HTTPResponse(
            200, {}, message_body([{"type": "text", "text": "ok"}])))
        client = AnthropicClient(anthropic_config(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash",
            thinking_level="disabled",
        ), http)
        client.complete([])
        payload = http.calls[0][1]
        self.assertEqual({"type": "disabled"}, payload["thinking"])
        self.assertNotIn("output_config", payload)

    def test_non_stream_extracts_thinking_and_text(self):
        http = FakeHttp(HTTPResponse(200, {}, message_body([
            {"type": "thinking", "thinking": "reason"},
            {"type": "text", "text": "answer"},
        ])))
        client = AnthropicClient(anthropic_config(), http)
        self.assertEqual("answer", client.complete([]))
        self.assertEqual("reason", client.last_reasoning)
        self.assertEqual("stop", client.last_finish_reason)

    def test_max_tokens_stop_reason_is_truncation(self):
        http = FakeHttp(HTTPResponse(
            200, {}, message_body([{"type": "text", "text": "partial"}], "max_tokens")))
        client = AnthropicClient(anthropic_config(), http)
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertIn("truncated at max_tokens", str(caught.exception))
        self.assertEqual("length", client.last_finish_reason)

    def test_refusal_raises(self):
        body = json.dumps({
            "type": "message",
            "content": [],
            "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "cyber"},
        }).encode()
        client = AnthropicClient(anthropic_config(), FakeHttp(HTTPResponse(200, {}, body)))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertIn("cyber", str(caught.exception))

    def test_stream_events(self):
        lines = [
            b"event: message_start\n",
            b'data: {"type":"message_start","message":{"id":"msg_1"}}\n',
            b"\n",
            b"event: content_block_start\n",
            b'data: {"type":"content_block_start","index":0,'
            b'"content_block":{"type":"thinking","thinking":""}}\n',
            b"\n",
            b"event: content_block_delta\n",
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"thinking_delta","thinking":"think"}}\n',
            b"\n",
            b"event: content_block_delta\n",
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"hel"}}\n',
            b"\n",
            b"event: content_block_delta\n",
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"text_delta","text":"lo"}}\n',
            b"\n",
            b"event: message_delta\n",
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n',
            b"\n",
            b"event: message_stop\n",
            b'data: {"type":"message_stop"}\n',
            b"\n",
        ]
        http = FakeStreamHttp(lines)
        reasoning = []
        content = []
        client = AnthropicClient(anthropic_config(), http)
        result = client.complete_stream(
            [{"role": "user", "content": "hi"}],
            on_reasoning=reasoning.append,
            on_content=content.append,
        )
        self.assertEqual("hello", result)
        self.assertEqual(["think"], reasoning)
        self.assertEqual(["hel", "lo"], content)
        self.assertEqual("stop", client.last_finish_reason)
        self.assertEqual(3, client.last_token_count)
        self.assertTrue(http.calls[0][3]["max_response"] > 0)

    def test_stream_error_event_is_retryable(self):
        lines = [
            b"event: error\n",
            b'data: {"type":"error","error":{"type":"overloaded_error",'
            b'"message":"Overloaded"}}\n',
            b"\n",
        ]
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        with self.assertRaises(LLMError) as caught:
            client.complete_stream([])
        self.assertTrue(caught.exception.retryable)
        self.assertIn("Overloaded", str(caught.exception))

    def test_http_error_authentication_is_not_retryable(self):
        body = json.dumps({
            "type": "error",
            "error": {"type": "authentication_error", "message": "invalid x-api-key"},
        }).encode()
        client = AnthropicClient(
            anthropic_config(), FakeHttp(HTTPResponse(401, {}, body)))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(401, caught.exception.status)
        self.assertIn("invalid x-api-key", str(caught.exception))

    def test_http_529_is_retryable(self):
        body = json.dumps({
            "type": "error",
            "error": {"type": "overloaded_error", "message": "busy"},
        }).encode()
        client = AnthropicClient(
            anthropic_config(), FakeHttp(HTTPResponse(529, {}, body)))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertTrue(caught.exception.retryable)

    def test_endpoint_accepts_v1_and_full_message_urls(self):
        for base, expected in (
            ("https://api.anthropic.com/v1", "https://api.anthropic.com/v1/messages"),
            ("https://proxy.example/messages", "https://proxy.example/messages"),
        ):
            http = FakeHttp(HTTPResponse(
                200, {}, message_body([{"type": "text", "text": "ok"}])))
            client = AnthropicClient(anthropic_config(base_url=base), http)
            client.complete([])
            self.assertEqual(expected, http.calls[0][0])

    def test_system_is_split_and_leading_assistant_is_dropped(self):
        http = FakeHttp(HTTPResponse(
            200, {}, message_body([{"type": "text", "text": "ok"}])))
        client = AnthropicClient(anthropic_config(), http)
        client.complete([
            {"role": "system", "content": "rules"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new task"},
        ])
        payload = http.calls[0][1]
        self.assertEqual("rules", payload["system"])
        self.assertEqual([{"role": "user", "content": "new task"}],
                         payload["messages"])

    def test_factory_selects_provider(self):
        self.assertIsInstance(
            create_llm(anthropic_config()), AnthropicClient)
        self.assertIsInstance(
            create_llm(DummyConfig(api_provider="openai")), OpenAIClient)


if __name__ == "__main__":
    unittest.main()
