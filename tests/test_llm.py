import json
import unittest
from unittest import mock

from harness.http_client import HTTPError, HTTPResponse
from harness.llm import (
    LLMError,
    LLMStreamCancelled,
    OpenAIClient,
    THINKING_LEVELS,
    normalize_thinking_level,
)
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


class RaisingHttp:
    def __init__(self, exception):
        self.exception = exception

    def post(self, *args, **kwargs):
        raise self.exception


class LLMTests(unittest.TestCase):
    def test_chat_completion_contract(self):
        body = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(), http)
        result = client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual("hello", result)
        url, payload, headers, _ = http.calls[0]
        self.assertEqual("https://api.openai.com/v1/chat/completions", url)
        self.assertFalse(payload["stream"])
        self.assertEqual("test-model", payload["model"])
        self.assertEqual("Bearer test-api-key", headers["Authorization"])
        self.assertTrue(headers["x-opencode-session"].startswith("ses_"))

    def test_native_tool_definition_and_nonstream_call(self):
        body = json.dumps({
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "__workspace",
                            "arguments": '{"action":"list"}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }]
        }).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(), http)
        tools = [{
            "name": "__workspace",
            "description": "workspace",
            "parameters": {"type": "object", "properties": {}},
        }]
        result = client.complete(
            [{"role": "user", "content": "hi"}], tools=tools)
        self.assertEqual("", result)
        payload = http.calls[0][1]
        self.assertEqual("__workspace", payload["tools"][0]["function"]["name"])
        self.assertEqual("call_1", client.last_tool_calls[0]["id"])
        self.assertEqual({"action": "list"}, client.last_tool_calls[0]["args"])
        self.assertEqual("", client.last_tool_calls[0]["error"])

    def test_native_tool_messages_are_converted(self):
        body = json.dumps({
            "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]
        }).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(), http)
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c1", "name": "__workspace",
                             "args": {"action": "list"}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "__workspace",
             "content": "- notes.txt"},
        ]
        self.assertEqual("done", client.complete(messages))
        converted = http.calls[0][1]["messages"]
        self.assertEqual("assistant", converted[1]["role"])
        self.assertEqual("c1", converted[1]["tool_calls"][0]["id"])
        self.assertEqual(
            '{"action": "list"}',
            converted[1]["tool_calls"][0]["function"]["arguments"])
        self.assertEqual("tool", converted[2]["role"])
        self.assertEqual("c1", converted[2]["tool_call_id"])

    def test_streamed_native_tool_arguments_are_accumulated(self):
        lines = [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            b'"id":"call_9","function":{"name":"__workspace","arguments":"{\\"action\\":"}}]}}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            b'"function":{"arguments":"\\"list\\"}"}}]}}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n',
            b"\n",
        ]
        http = FakeStreamHttp(lines)
        client = OpenAIClient(DummyConfig(), http)
        self.assertEqual("", client.complete_stream([], tools=[]))
        self.assertEqual("call_9", client.last_tool_calls[0]["id"])
        self.assertEqual({"action": "list"}, client.last_tool_calls[0]["args"])
        self.assertEqual("tool_calls", client.last_finish_reason)

    def test_content_parts_are_joined(self):
        body = json.dumps({"choices": [{"message": {"content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}
        ]}}]}).encode()
        client = OpenAIClient(DummyConfig(), FakeHttp(HTTPResponse(200, {}, body)))
        self.assertEqual("ab", client.complete([]))

    def test_http_error_is_bounded(self):
        client = OpenAIClient(DummyConfig(), FakeHttp(HTTPResponse(401, {}, b"x" * 1000)))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertLess(len(str(caught.exception)), 700)

    def test_deepseek_thinking_defaults_to_high(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(model="deepseek-flash"), http)
        client.complete([])
        payload = http.calls[0][1]
        self.assertEqual({"type": "enabled"}, payload["thinking"])
        self.assertEqual("high", payload["reasoning_effort"])
        self.assertEqual(600, http.calls[0][3]["timeout"])

    def test_high_thinking_sets_effort_and_timeout(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(model="deepseek-flash", thinking_level="high"), http)
        client.complete([])
        payload = http.calls[0][1]
        self.assertEqual({"type": "enabled"}, payload["thinking"])
        self.assertEqual("high", payload["reasoning_effort"])
        self.assertEqual(600, http.calls[0][3]["timeout"])

    def test_middle_maps_to_high_effort(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(model="deepseek-flash", thinking_level="medium"), http)
        client.complete([])
        self.assertEqual("high", http.calls[0][1]["reasoning_effort"])

    def test_retired_default_level_maps_to_high(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(model="deepseek-flash", thinking_level="default"), http)
        client.complete([])
        payload = http.calls[0][1]
        self.assertEqual({"type": "enabled"}, payload["thinking"])
        self.assertEqual("high", payload["reasoning_effort"])
        self.assertEqual(600, http.calls[0][3]["timeout"])

    def test_only_four_thinking_levels_are_accepted(self):
        self.assertEqual(("disabled", "low", "high", "max"), THINKING_LEVELS)
        self.assertEqual("disabled", normalize_thinking_level("off"))
        self.assertEqual("low", normalize_thinking_level("low"))
        self.assertEqual("high", normalize_thinking_level("middle"))
        self.assertEqual("high", normalize_thinking_level("medium"))
        self.assertEqual("high", normalize_thinking_level("default"))
        self.assertEqual("max", normalize_thinking_level("max"))
        self.assertIsNone(normalize_thinking_level("bogus"))

    def test_max_thinking_extends_timeout(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(model="deepseek-flash", thinking_level="max"), http)
        client.complete([])
        self.assertEqual("max", http.calls[0][1]["reasoning_effort"])
        self.assertEqual(900, http.calls[0][3]["timeout"])

    def test_reasoning_metadata_is_captured(self):
        body = json.dumps({"choices": [{
            "message": {"content": "answer", "reasoning_content": "thought"},
            "finish_reason": "stop",
        }]}).encode()
        client = OpenAIClient(DummyConfig(), FakeHttp(HTTPResponse(200, {}, body)))
        self.assertEqual("answer", client.complete([]))
        self.assertEqual("thought", client.last_reasoning)
        self.assertEqual("stop", client.last_finish_reason)
        self.assertEqual(6, client.last_content_chars)

    def test_non_deepseek_omits_thinking_parameter(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(), http)
        client.complete([])
        self.assertNotIn("thinking", http.calls[0][1])

    def test_non_deepseek_thinking_extends_timeout(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(
            DummyConfig(model="iceman-contributor-free",
                        thinking_level="high"), http
        )
        client.complete([])
        self.assertNotIn("thinking", http.calls[0][1])
        self.assertEqual(600, http.calls[0][3]["timeout"])

    def test_non_deepseek_max_thinking_extends_timeout(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(
            DummyConfig(model="iceman-contributor-free",
                        thinking_level="max"), http
        )
        client.complete([])
        self.assertEqual(900, http.calls[0][3]["timeout"])

    def test_non_deepseek_disabled_keeps_request_timeout(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(
            DummyConfig(model="iceman-contributor-free",
                        thinking_level="disabled"), http
        )
        client.complete([])
        self.assertEqual(120, http.calls[0][3]["timeout"])

    def test_sse_stream_prints_reasoning_and_content(self):
        lines = [
            b'data: {"choices":[{"delta":{"reasoning_content":"think "},"finish_reason":null}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"reasoning_content":"more"},"finish_reason":null}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":"hello "},"finish_reason":null}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":"world"},"finish_reason":"stop"}]}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ]
        http = FakeStreamHttp(lines)
        reasoning = []
        content = []
        client = OpenAIClient(DummyConfig(model="deepseek-flash"), http)
        result = client.complete_stream(
            [{"role": "user", "content": "hi"}],
            on_reasoning=reasoning.append,
            on_content=content.append,
        )
        self.assertEqual("hello world", result)
        self.assertEqual("think more", "".join(reasoning))
        self.assertEqual("hello world", "".join(content))
        self.assertTrue(client.last_streamed)
        self.assertEqual("stop", client.last_finish_reason)
        self.assertEqual(10, client.last_reasoning_chars)
        self.assertTrue(http.reader.closed)
        self.assertTrue(http.calls[0][1]["stream"])
        self.assertEqual(300, http.calls[0][3]["idle_timeout"])
        self.assertEqual(600, http.calls[0][3]["first_byte_timeout"])
        self.assertEqual(16777216, http.calls[0][3]["max_response"])

    def test_stream_callback_cancellation_propagates(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"first"},"finish_reason":null}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":"second"},"finish_reason":"stop"}]}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ]
        http = FakeStreamHttp(lines)
        client = OpenAIClient(DummyConfig(), http)
        seen = []

        def cancel(text):
            seen.append(text)
            raise LLMStreamCancelled()

        with self.assertRaises(LLMStreamCancelled):
            client.complete_stream([], on_content=cancel)
        self.assertEqual(["first"], seen)
        self.assertTrue(http.reader.closed)

    def test_stream_header_timeout_is_not_raised_by_thinking(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n',
            b"\n",
        ]
        http = FakeStreamHttp(lines)
        client = OpenAIClient(
            DummyConfig(model="deepseek-flash", thinking_level="high",
                        stream_header_timeout=300),
            http,
        )
        self.assertEqual("ok", client.complete_stream([]))
        self.assertEqual(300, http.calls[0][3]["timeout"])
        self.assertEqual(300000, client.last_header_timeout_ms)

    def test_finish_reason_completes_without_done(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"complete"},"finish_reason":"stop"}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":"ignored"}}]}\n',
            b"\n",
        ]
        http = FakeStreamHttp(lines)
        client = OpenAIClient(DummyConfig(), http)
        self.assertEqual("complete", client.complete_stream([]))
        self.assertEqual("stop", client.last_finish_reason)
        self.assertTrue(http.reader.closed)

    def test_event_idle_timeout_ignores_keepalive_pings(self):
        ticks = [0]

        class PingReader(FakeStreamReader):
            def readline(self):
                ticks[0] += 200000
                return FakeStreamReader.readline(self)

        lines = [
            b": ping\n", b"\n",
            b": ping\n", b"\n",
        ]
        reader = PingReader(lines)
        http = FakeStreamHttp([])
        http.reader = reader

        client = OpenAIClient(DummyConfig(stream_event_idle_timeout=300), http)
        with mock.patch.object(OpenAIClient, "_ticks", lambda _self: ticks[0]):
            with mock.patch.object(
                    OpenAIClient, "_elapsed",
                    lambda _self, started: ticks[0] - started):
                with self.assertRaises(LLMError) as caught:
                    client.complete_stream([])
        self.assertIn("event idle timeout", str(caught.exception))

    def test_sse_stream_rejects_empty_content(self):
        lines = [
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ]
        client = OpenAIClient(DummyConfig(), FakeStreamHttp(lines))
        with self.assertRaises(LLMError) as caught:
            client.complete_stream([])
        self.assertIn("empty content", str(caught.exception))

    def test_sse_stream_rejects_unfinished_stream(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
            b"\n",
        ]
        client = OpenAIClient(DummyConfig(), FakeStreamHttp(lines))
        with self.assertRaises(LLMError) as caught:
            client.complete_stream([])
        self.assertIn("before [DONE]", str(caught.exception))

    def test_truncated_response_is_rejected(self):
        body = json.dumps({"choices": [{
            "message": {"content": "partial"},
            "finish_reason": "length",
        }]}).encode()
        client = OpenAIClient(DummyConfig(), FakeHttp(HTTPResponse(200, {}, body)))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertIn("truncated", str(caught.exception))

    def test_truncated_summary_can_be_accepted(self):
        body = json.dumps({"choices": [{
            "message": {"content": "partial summary"},
            "finish_reason": "length",
        }]}).encode()
        client = OpenAIClient(DummyConfig(), FakeHttp(HTTPResponse(200, {}, body)))
        result = client.complete([], allow_truncated=True)
        self.assertEqual("partial summary", result)

    def test_empty_response_is_rejected(self):
        body = json.dumps({"choices": [{
            "message": {"content": ""},
            "finish_reason": "stop",
        }]}).encode()
        client = OpenAIClient(DummyConfig(), FakeHttp(HTTPResponse(200, {}, body)))
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertIn("empty content", str(caught.exception))
        self.assertFalse(caught.exception.retryable)

    def test_retryable_http_status_and_retry_after(self):
        client = OpenAIClient(
            DummyConfig(),
            FakeHttp(HTTPResponse(503, {"retry-after": "7"}, b"busy")),
        )
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(503, caught.exception.status)
        self.assertEqual(7, caught.exception.retry_after)

    def test_non_retryable_http_status(self):
        client = OpenAIClient(
            DummyConfig(),
            FakeHttp(HTTPResponse(401, {}, b"unauthorized")),
        )
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(401, caught.exception.status)

    def test_network_timeout_is_retryable(self):
        client = OpenAIClient(
            DummyConfig(), RaisingHttp(OSError(110, "timed out"))
        )
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertTrue(caught.exception.retryable)

    def test_wrapped_etimedout_is_retryable(self):
        client = OpenAIClient(
            DummyConfig(),
            RaisingHttp(HTTPError("HTTPS request failed: [Errno 116] ETIMEDOUT")),
        )
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertTrue(caught.exception.retryable)

    def test_certificate_error_is_not_retryable(self):
        client = OpenAIClient(
            DummyConfig(),
            RaisingHttp(OSError("certificate verify failed")),
        )
        with self.assertRaises(LLMError) as caught:
            client.complete([])
        self.assertFalse(caught.exception.retryable)

    def test_stream_http_error_is_retryable(self):
        http = FakeStreamHttp([b"busy"], status=503, headers={"retry-after": "2"})
        client = OpenAIClient(DummyConfig(), http)
        with self.assertRaises(LLMError) as caught:
            client.complete_stream([])
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(503, caught.exception.status)
        self.assertEqual(2, caught.exception.retry_after)

    def test_stream_error_event_is_retryable(self):
        lines = [
            b'data: {"error":{"message":"server overloaded"}}\n',
            b"\n",
        ]
        client = OpenAIClient(DummyConfig(), FakeStreamHttp(lines))
        with self.assertRaises(LLMError) as caught:
            client.complete_stream([])
        self.assertTrue(caught.exception.retryable)

    def test_session_header_is_stable_and_rotates(self):
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        http = FakeHttp(HTTPResponse(200, {}, body))
        client = OpenAIClient(DummyConfig(), http)
        first = client.session_id
        self.assertTrue(first.startswith("ses_"))
        client.complete([])
        client.complete([])
        self.assertEqual(first, client.session_id)
        self.assertEqual(first, http.calls[0][2]["x-opencode-session"])
        self.assertEqual(first, http.calls[1][2]["x-opencode-session"])
        rotated = client.new_session()
        self.assertNotEqual(first, rotated)
        self.assertTrue(rotated.startswith("ses_"))
        client.complete([])
        self.assertEqual(rotated, http.calls[2][2]["x-opencode-session"])

    def test_unfinished_stream_with_content_is_partial(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
            b"\n",
        ]
        client = OpenAIClient(DummyConfig(), FakeStreamHttp(lines))
        with self.assertRaises(LLMError) as caught:
            client.complete_stream([])
        self.assertFalse(caught.exception.retryable)
        self.assertTrue(caught.exception.partial)


if __name__ == "__main__":
    unittest.main()
