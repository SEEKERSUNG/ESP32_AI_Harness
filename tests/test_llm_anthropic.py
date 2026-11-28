import json
import unittest

from harness.llm import LLMError, OpenAIClient, create_llm
from harness.llm_anthropic import AnthropicClient
from tests.support import DummyConfig


class FakeStreamReader:
    def __init__(self, lines, status=200, headers=None):
        raw = b"".join(lines)
        # Mirror a socket reader: splitline() boundaries, not caller chunks.
        self.lines = raw.splitlines(keepends=True)
        self.status = status
        self.headers = headers or {}
        self.bytes_read = len(raw)
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


def sse(data):
    return ("data: " + json.dumps(data) + "\n\n").encode()


def message_events(blocks, stop_reason="end_turn", usage=None,
                   delta_usage=None, stop_details=None):
    """Build a complete SSE event sequence for the given content blocks."""
    lines = [sse({
        "type": "message_start",
        "message": {"id": "msg_test", "usage": usage or {}},
    })]
    for index, block in enumerate(blocks):
        block_type = block.get("type")
        if block_type == "text":
            lines.append(sse({
                "type": "content_block_start", "index": index,
                "content_block": {"type": "text", "text": ""},
            }))
            text = block.get("text")
            if text:
                lines.append(sse({
                    "type": "content_block_delta", "index": index,
                    "delta": {"type": "text_delta", "text": text},
                }))
        elif block_type == "thinking":
            lines.append(sse({
                "type": "content_block_start", "index": index,
                "content_block": {"type": "thinking", "thinking": ""},
            }))
            text = block.get("text")
            if text:
                lines.append(sse({
                    "type": "content_block_delta", "index": index,
                    "delta": {"type": "thinking_delta", "thinking": text},
                }))
            signature = block.get("signature")
            if signature:
                lines.append(sse({
                    "type": "content_block_delta", "index": index,
                    "delta": {"type": "signature_delta", "signature": signature},
                }))
        elif block_type == "redacted_thinking":
            lines.append(sse({
                "type": "content_block_start", "index": index,
                "content_block": {
                    "type": "redacted_thinking",
                    "data": block.get("data", ""),
                },
            }))
        elif block_type == "tool_use":
            start = {
                "type": "tool_use",
                "id": block.get("id"),
                "name": block.get("name"),
                "input": {},
            }
            lines.append(sse({
                "type": "content_block_start", "index": index,
                "content_block": start,
            }))
            for chunk in block.get("json") or []:
                lines.append(sse({
                    "type": "content_block_delta", "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": chunk,
                    },
                }))
        lines.append(sse({"type": "content_block_stop", "index": index}))
    delta = {"stop_reason": stop_reason}
    if stop_details is not None:
        delta["stop_details"] = stop_details
    lines.append(sse({
        "type": "message_delta", "delta": delta,
        "usage": delta_usage or {},
    }))
    lines.append(sse({"type": "message_stop"}))
    return lines


def text_stream(text="hello", stop_reason="end_turn", usage=None,
                delta_usage=None):
    return message_events(
        [{"type": "text", "text": text}],
        stop_reason=stop_reason,
        usage=usage,
        delta_usage=delta_usage,
    )


class AnthropicTests(unittest.TestCase):
    def test_messages_contract(self):
        http = FakeStreamHttp(text_stream("hello"))
        client = AnthropicClient(anthropic_config(), http)
        result = client.complete([
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "hi"},
        ])
        self.assertEqual("hello", result)
        url, payload, headers, kwargs = http.calls[0]
        self.assertEqual("https://api.anthropic.com/v1/messages", url)
        self.assertEqual([{
            "type": "text",
            "text": "system rules",
            "cache_control": {"type": "ephemeral"},
        }], payload["system"])
        self.assertEqual([{"role": "user", "content": "hi"}], payload["messages"])
        self.assertTrue(payload["stream"])
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
        self.assertTrue(client.last_streamed)

    def test_native_tool_definition_and_streamed_call(self):
        lines = message_events([{
            "type": "tool_use",
            "id": "toolu_1",
            "name": "__workspace",
            "json": ['{"action":', '"list"}'],
        }], stop_reason="tool_use")
        http = FakeStreamHttp(lines)
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

    def test_zero_argument_tool_input_is_empty_object(self):
        lines = message_events([{
            "type": "tool_use",
            "id": "toolu_zero",
            "name": "__background",
            "json": [],
        }], stop_reason="tool_use")
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        self.assertEqual("", client.complete_stream([], tools=[]))
        self.assertEqual({}, client.last_tool_calls[0]["args"])
        self.assertEqual("", client.last_tool_calls[0]["error"])

    def test_native_tool_messages_are_converted(self):
        http = FakeStreamHttp(text_stream("done"))
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

    def test_tool_results_stay_adjacent_and_are_marked_as_errors(self):
        http = FakeStreamHttp(text_stream("done"))
        client = AnthropicClient(anthropic_config(), http)
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "",
             "tool_calls": [
                 {"id": "toolu_1", "name": "__workspace",
                  "args": {"action": "list"}},
                 {"id": "toolu_2", "name": "__search",
                  "args": {"query": "x"}},
             ]},
            # A local event must not split the tool_use/tool_result pair.
            {"role": "user", "content": "Local harness event: background done"},
            {"role": "tool", "tool_call_id": "toolu_1",
             "name": "__workspace", "content": "ok"},
            {"role": "tool", "tool_call_id": "toolu_2",
             "name": "__search", "content": "", "is_error": True},
        ]
        self.assertEqual("done", client.complete(messages))
        converted = http.calls[0][1]["messages"]
        self.assertEqual(3, len(converted))
        self.assertEqual("assistant", converted[1]["role"])
        user = converted[2]
        self.assertEqual("user", user["role"])
        self.assertEqual("tool_result", user["content"][0]["type"])
        self.assertEqual("toolu_1", user["content"][0]["tool_use_id"])
        self.assertFalse(user["content"][0].get("is_error"))
        self.assertEqual("tool_result", user["content"][1]["type"])
        self.assertEqual("toolu_2", user["content"][1]["tool_use_id"])
        self.assertTrue(user["content"][1]["is_error"])
        self.assertEqual("(no output)", user["content"][1]["content"])
        self.assertEqual("text", user["content"][2]["type"])
        self.assertIn("background done", user["content"][2]["text"])
        self.assertEqual(
            {"type": "ephemeral"},
            user["content"][1]["cache_control"])

    def test_missing_tool_result_is_filled_in(self):
        http = FakeStreamHttp(text_stream("done"))
        client = AnthropicClient(anthropic_config(), http)
        client.complete([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "toolu_1", "name": "__workspace",
                             "args": {}}]},
            {"role": "user", "content": "next question"},
        ])
        converted = http.calls[0][1]["messages"]
        self.assertEqual(3, len(converted))
        self.assertEqual("user", converted[2]["role"])
        self.assertEqual("tool_result", converted[2]["content"][0]["type"])
        self.assertTrue(converted[2]["content"][0]["is_error"])

    def test_thinking_blocks_are_replayed(self):
        lines = message_events([
            {"type": "thinking", "text": "plan", "signature": "sig-1"},
            {"type": "text", "text": ""},
            {"type": "tool_use", "id": "toolu_1", "name": "__workspace",
             "json": ['{"action":"list"}']},
        ], stop_reason="tool_use")
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        client.complete_stream([{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(1, len(client.last_thinking_blocks))
        self.assertEqual("sig-1", client.last_thinking_blocks[0]["signature"])
        thinking_blocks = client.last_thinking_blocks
        http = FakeStreamHttp(text_stream("done"))
        client = AnthropicClient(anthropic_config(), http)
        client.complete([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "",
             "thinking_blocks": thinking_blocks,
             "tool_calls": [{"id": "toolu_1", "name": "__workspace",
                             "args": {"action": "list"}}]},
            {"role": "tool", "tool_call_id": "toolu_1",
             "name": "__workspace", "content": "ok"},
        ])
        assistant = http.calls[0][1]["messages"][1]
        self.assertEqual("thinking", assistant["content"][0]["type"])
        self.assertEqual("sig-1", assistant["content"][0]["signature"])
        self.assertEqual("tool_use", assistant["content"][1]["type"])

    def test_assistant_blocks_keep_their_original_order(self):
        lines = message_events([
            {"type": "thinking", "text": "plan", "signature": "sig-1"},
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "toolu_1", "name": "__workspace",
             "json": ['{"action":"list"}']},
        ], stop_reason="tool_use")
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        client.complete_stream([{"role": "user", "content": "hi"}], tools=[])
        self.assertEqual(
            ["thinking", "text", "tool_use"],
            [block["type"] for block in client.last_assistant_blocks])
        self.assertEqual("checking", client.last_assistant_blocks[1]["text"])
        raw_blocks = client.last_assistant_blocks
        http = FakeStreamHttp(text_stream("done"))
        client = AnthropicClient(anthropic_config(), http)
        client.complete([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "checking",
             "assistant_blocks": raw_blocks,
             "tool_calls": [{"id": "toolu_1", "name": "__workspace",
                             "args": {"action": "list"}}]},
            {"role": "tool", "tool_call_id": "toolu_1",
             "name": "__workspace", "content": "ok"},
        ])
        assistant = http.calls[0][1]["messages"][1]
        self.assertEqual(
            ["thinking", "text", "tool_use"],
            [block["type"] for block in assistant["content"]])
        self.assertEqual("sig-1", assistant["content"][0]["signature"])

    def test_thinking_blocks_are_dropped_when_disabled(self):
        http = FakeStreamHttp(text_stream("done"))
        client = AnthropicClient(
            anthropic_config(thinking_level="disabled"), http)
        client.complete([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "",
             "thinking_blocks": [{
                 "type": "thinking", "thinking": "plan", "signature": "sig",
             }],
             "tool_calls": [{"id": "toolu_1", "name": "__workspace",
                             "args": {}}]},
            {"role": "tool", "tool_call_id": "toolu_1",
             "name": "__workspace", "content": "ok"},
        ])
        assistant = http.calls[0][1]["messages"][1]
        self.assertEqual("tool_use", assistant["content"][0]["type"])

    def test_streamed_native_tool_input_is_accumulated(self):
        lines = [
            b'data: {"type":"content_block_start","index":0,'
            b'"content_block":{"type":"tool_use","id":"toolu_9",'
            b'"name":"__workspace","input":{}}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"input_json_delta","partial_json":""}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"input_json_delta","partial_json":'
            b'"{\\"action\\":"}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"input_json_delta","partial_json":'
            b'"\\"list\\"}"}}\n',
            b"\n",
            b'data: {"type":"content_block_stop","index":0}\n',
            b"\n",
            b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}\n',
            b"\n",
            b'data: {"type":"message_stop"}\n',
            b"\n",
        ]
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        self.assertEqual("", client.complete_stream([], tools=[]))
        self.assertEqual("toolu_9", client.last_tool_calls[0]["id"])
        self.assertEqual({"action": "list"}, client.last_tool_calls[0]["args"])

    def test_invalid_tool_json_keeps_its_error(self):
        lines = message_events([{
            "type": "tool_use",
            "id": "toolu_bad",
            "name": "__workspace",
            "json": ['{"action":'],
        }], stop_reason="tool_use")
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        self.assertEqual("", client.complete_stream([], tools=[]))
        self.assertEqual({}, client.last_tool_calls[0]["args"])
        self.assertEqual(
            "Invalid JSON tool arguments",
            client.last_tool_calls[0]["error"])

    def test_disabled_thinking_omits_parameters(self):
        http = FakeStreamHttp(text_stream("ok"))
        client = AnthropicClient(anthropic_config(thinking_level="disabled"), http)
        client.complete([])
        payload = http.calls[0][1]
        self.assertNotIn("thinking", payload)
        self.assertNotIn("output_config", payload)
        self.assertNotIn("temperature", payload)

    def test_prompt_cache_can_be_disabled(self):
        http = FakeStreamHttp(text_stream("ok"))
        client = AnthropicClient(anthropic_config(prompt_cache=False), http)
        client.complete([{"role": "system", "content": "rules"}])
        payload = http.calls[0][1]
        self.assertEqual("rules", payload["system"])
        for message in payload["messages"]:
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    self.assertNotIn("cache_control", block)

    def test_deepseek_anthropic_uses_enabled_thinking(self):
        http = FakeStreamHttp(text_stream("ok"))
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
        http = FakeStreamHttp(text_stream("ok"))
        client = AnthropicClient(anthropic_config(
            base_url="https://api.deepseek.com/anthropic",
            model="deepseek-flash",
            thinking_level="disabled",
        ), http)
        client.complete([])
        payload = http.calls[0][1]
        self.assertEqual({"type": "disabled"}, payload["thinking"])
        self.assertNotIn("output_config", payload)

    def test_stream_extracts_thinking_and_text(self):
        lines = message_events([
            {"type": "thinking", "text": "reason", "signature": "sig"},
            {"type": "text", "text": "answer"},
        ])
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        self.assertEqual("answer", client.complete([]))
        self.assertEqual("stop", client.last_finish_reason)
        self.assertEqual(1, len(client.last_thinking_blocks))

    def test_usage_is_captured(self):
        lines = message_events([{"type": "text", "text": "answer"}], usage={
            "input_tokens": 120,
            "output_tokens": 1,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
        }, delta_usage={"output_tokens": 42})
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        client.complete([])
        self.assertEqual(120, client.last_input_tokens)
        self.assertEqual(42, client.last_output_tokens)
        self.assertEqual(100, client.last_cache_read_tokens)
        self.assertEqual(20, client.last_cache_creation_tokens)
        self.assertEqual(42, client.last_usage["output_tokens"])

    def test_max_tokens_stop_reason_is_truncation(self):
        lines = text_stream("partial", stop_reason="max_tokens")
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertIn("truncated at max_tokens", str(caught.exception))
        self.assertEqual("length", client.last_finish_reason)

    def test_truncated_tool_call_is_not_returned(self):
        lines = message_events([{
            "type": "tool_use",
            "id": "toolu_partial",
            "name": "__workspace",
            "json": ['{"action":'],
        }], stop_reason="max_tokens")
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertIn("tool call", str(caught.exception))
        self.assertEqual([], client.last_tool_calls)

    def test_refusal_raises_with_category(self):
        lines = message_events([{"type": "text", "text": ""}],
                               stop_reason="refusal",
                               stop_details={
                                   "type": "refusal", "category": "cyber"})
        client = AnthropicClient(anthropic_config(), FakeStreamHttp(lines))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertIn("cyber", str(caught.exception))

    def test_stream_events(self):
        lines = message_events([
            {"type": "thinking", "text": "think"},
            {"type": "text", "text": "hello"},
        ])
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
        self.assertEqual(["hello"], content)
        self.assertEqual("stop", client.last_finish_reason)
        self.assertEqual(2, client.last_token_count)
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
            anthropic_config(), FakeStreamHttp([body], status=401))
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
            anthropic_config(), FakeStreamHttp([body], status=529))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertTrue(caught.exception.retryable)

    def test_endpoint_accepts_v1_and_full_message_urls(self):
        for base, expected in (
            ("https://api.anthropic.com/v1", "https://api.anthropic.com/v1/messages"),
            ("https://proxy.example/messages", "https://proxy.example/messages"),
        ):
            http = FakeStreamHttp(text_stream("ok"))
            client = AnthropicClient(anthropic_config(base_url=base), http)
            client.complete([])
            self.assertEqual(expected, http.calls[0][0])

    def test_system_is_split_and_leading_assistant_is_dropped(self):
        http = FakeStreamHttp(text_stream("ok"))
        client = AnthropicClient(anthropic_config(prompt_cache=False), http)
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
