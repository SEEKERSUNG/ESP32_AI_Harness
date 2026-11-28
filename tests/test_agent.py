import json
import tempfile
import unittest
from unittest import mock

from harness.agent import (
    Agent,
    AgentError,
    auto_source_is_safe,
    builtin_tool_prompt,
    estimate_tokens,
    format_question,
    parse_response,
    parse_response_ex,
    parse_skill_proposal,
    parse_tool_call,
    response_asks_question,
    system_prompt,
)
from harness.device import Board
from harness.executor import ExecutionResult, Executor
from harness.llm import LLMError, LLMStreamCancelled, llm_error
from harness.memory import MemoryStore
from harness.workspace import Workspace
from tests.support import DummyBoard, DummyConfig


class FakeSkills:
    def __init__(self):
        self.saved = []
        self.deleted = []

    def list(self, limit=50):
        return [{
            "name": "board-notes",
            "description": "Board inspection notes",
            "chars": 100,
        }][:limit]

    def read_bundle(self, name, limit=16384):
        return {
            "name": name,
            "description": "Board inspection notes",
            "content": "# Board notes\nUse device.info().",
            "chars": 36,
            "truncated": False,
        }

    def save(self, name, description, body, limit=16384):
        self.saved.append((name, description, body))
        return {"name": name, "description": description, "chars": len(body)}

    def delete(self, name):
        self.deleted.append(name)


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.stream_options = []

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False, timeout=None):
        self.calls.append(messages)
        return self.responses.pop(0)

    def complete_stream(self, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, thinking_level=None,
                        allow_truncated=False, timeout=None):
        self.calls.append(messages)
        self.stream_options.append({
            "max_tokens": max_tokens,
            "thinking_level": thinking_level,
            "allow_truncated": allow_truncated,
            "timeout": timeout,
        })
        return self.responses.pop(0)


class ReasoningLLM(FakeLLM):
    def __init__(self, *responses):
        super().__init__(*responses)
        self.last_reasoning = ""

    def complete_stream(self, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, thinking_level=None,
                        allow_truncated=False, timeout=None):
        self.last_reasoning = "step-by-step check"
        if on_reasoning is not None:
            on_reasoning(self.last_reasoning)
        return super().complete_stream(
            messages, on_reasoning=on_reasoning, on_content=on_content,
            max_tokens=max_tokens, thinking_level=thinking_level,
            allow_truncated=allow_truncated, timeout=timeout)


class RaisingLLM:
    def __init__(self, exception):
        self.exception = exception

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False):
        raise self.exception


class ErrorLLM:
    def __init__(self, failures, message="Model API HTTP 503: busy",
                 status=503, retryable=True, retry_after=None):
        self.failures = failures
        self.calls = 0
        self.message = message
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False):
        self.calls += 1
        if self.calls <= self.failures:
            raise llm_error(
                self.message,
                status=self.status,
                retryable=self.retryable,
                retry_after=self.retry_after,
            )
        return "Recovered"


class FailingSummaryLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("summary unavailable")
        return "Final answer"

    def complete_stream(self, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, thinking_level=None,
                        allow_truncated=False, timeout=None):
        return self.complete(
            messages, max_tokens, thinking_level, allow_truncated, timeout
        )


class FallbackLLM:
    def __init__(self):
        self.calls = []

    def complete_stream(self, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, thinking_level=None):
        self.calls.append(thinking_level)
        if len(self.calls) == 1:
            raise llm_error("SSE idle timeout", retryable=True)
        return "OK"


class TruncationLLM:
    def __init__(self, failures=1):
        self.calls = []
        self.failures = failures

    def complete_stream(self, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, thinking_level=None):
        self.calls.append((thinking_level, max_tokens))
        if len(self.calls) <= self.failures:
            raise RuntimeError(
                "Model output was truncated at max_tokens=4096 "
                "(reasoning=12762 chars, content=0 chars); increase max_tokens"
            )
        return "OK"


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body


class FakeSearchHttp:
    def __init__(self, status=200, payload=None, html=None, request_status=200,
                 rss=None):
        self.status = status
        self.payload = payload or {"results": []}
        self.html = html or b""
        self.rss = rss
        self.request_status = request_status
        self.calls = []
        self.request_calls = []

    def post(self, url, body, headers, timeout=60, max_response=24576,
             tls_verify=True, ca_file="/ca.pem"):
        self.calls.append({
            "url": url,
            "payload": json.loads(body),
            "headers": headers,
            "timeout": timeout,
            "max_response": max_response,
            "tls_verify": tls_verify,
            "ca_file": ca_file,
        })
        return FakeResponse(self.status, json.dumps(self.payload).encode())

    def request(self, method, url, body=b"", headers=None, timeout=60,
                max_response=24576, tls_verify=True, ca_file="/ca.pem"):
        self.request_calls.append({
            "method": method,
            "url": url,
            "headers": headers or {},
            "timeout": timeout,
            "max_response": max_response,
            "tls_verify": tls_verify,
            "ca_file": ca_file,
        })
        if "format=rss" in url and self.rss is not None:
            return FakeResponse(self.request_status, self.rss)
        return FakeResponse(self.request_status, self.html)


class ContextBudgetTests(unittest.TestCase):
    def test_auto_source_guard_detects_indefinite_loops(self):
        self.assertFalse(auto_source_is_safe("while True:\n    pass"))
        self.assertFalse(auto_source_is_safe("while 1:\n    pass"))
        self.assertFalse(auto_source_is_safe("while (True):\n    pass"))
        self.assertTrue(auto_source_is_safe("for i in range(3):\n    print(i)"))

    def test_auto_source_guard_allows_bounded_while_true_loops(self):
        self.assertTrue(auto_source_is_safe(
            "while True:\n"
            "    data = sock.recv(512)\n"
            "    if not data:\n"
            "        break\n"
        ))
        self.assertTrue(auto_source_is_safe(
            "def read(sock):\n"
            "    while True:\n"
            "        if sock.recv(1) == b'':\n"
            "            return b''\n"
        ))
        self.assertTrue(auto_source_is_safe("while True: break"))
        self.assertFalse(auto_source_is_safe(
            "while True:\n"
            "    print('break')\n"
        ))
        self.assertFalse(auto_source_is_safe(
            "while True:\n"
            "    if ready:\n"
            "        print('waiting')\n"
            "    # break is only in this comment\n"
        ))

    def test_token_estimator_handles_ascii_and_chinese(self):
        self.assertEqual(1, estimate_tokens("abcd"))
        self.assertEqual(2, estimate_tokens("你好"))
        self.assertEqual(3, estimate_tokens("abcd你好"))
        self.assertEqual(1, estimate_tokens("é"))
        self.assertEqual(1, estimate_tokens("😀"))

    def test_history_trimming_uses_token_budget(self):
        config = DummyConfig(
            history_messages=10,
            history_chars=100000,
            context_tokens=20,
        )
        agent = Agent(config, FakeLLM(), DummyBoard())
        agent.history = [
            {"role": "user", "content": "x" * 100}
            for _ in range(5)
        ]
        agent._trim_history()
        self.assertEqual(4, len(agent.history))


class CompactionTests(unittest.TestCase):
    def test_manual_compaction_summarizes_and_keeps_recent_messages(self):
        llm = FakeLLM("The user asked for a sensor reading; it succeeded.")
        config = DummyConfig(
            auto_compact=False,
            compact_keep_messages=2,
            summary_char_limit=200,
            history_messages=20,
        )
        agent = Agent(config, llm, DummyBoard())
        agent.history = [
            {"role": "user", "content": "first task"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second task"},
            {"role": "assistant", "content": "second answer"},
        ]
        self.assertTrue(agent.compact())
        self.assertEqual(2, len(agent.history))
        self.assertEqual("second task", agent.history[0]["content"])
        self.assertEqual(
            "The user asked for a sensor reading; it succeeded.", agent.summary
        )
        self.assertIn("first task", llm.calls[0][1]["content"])
        self.assertEqual("first task", agent.original_request)
        self.assertIn("Summary of earlier conversation", agent._system_content())
        self.assertIn(agent.summary, agent._system_content())
        self.assertIn("Original user request", agent._system_content())
        self.assertIn("first task", agent._system_content())

    def test_compaction_uses_streaming_with_long_timeout(self):
        llm = FakeLLM("Streamed summary")
        config = DummyConfig(
            auto_compact=False,
            compact_keep_messages=2,
            summary_char_limit=200,
            summary_timeout=321,
        )
        agent = Agent(config, llm, DummyBoard())
        agent.history = [
            {"role": "user", "content": "first task"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second task"},
            {"role": "assistant", "content": "second answer"},
        ]
        self.assertTrue(agent.compact())
        self.assertEqual(321, llm.stream_options[0]["timeout"])
        self.assertTrue(llm.stream_options[0]["allow_truncated"])
        self.assertEqual("disabled", llm.stream_options[0]["thinking_level"])
        self.assertEqual("Streamed summary", agent.summary)

    def test_compaction_merges_existing_summary(self):
        llm = FakeLLM("Merged summary")
        config = DummyConfig(
            auto_compact=False,
            compact_keep_messages=2,
            summary_char_limit=200,
        )
        agent = Agent(config, llm, DummyBoard())
        agent.summary = "Older fact: the device is on COM8."
        agent.history = [
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "recent task"},
            {"role": "assistant", "content": "recent answer"},
        ]
        self.assertTrue(agent.compact())
        transcript = llm.calls[0][1]["content"]
        self.assertIn("Existing summary", transcript)
        self.assertIn("COM8", transcript)
        self.assertEqual("Merged summary", agent.summary)

    def test_manual_compaction_requires_enough_history(self):
        agent = Agent(DummyConfig(auto_compact=False, compact_keep_messages=4),
                      FakeLLM(), DummyBoard())
        agent.history = [{"role": "user", "content": "only task"}]
        self.assertFalse(agent.compact())
        self.assertEqual(1, len(agent.history))

    def test_auto_compaction_runs_before_request(self):
        llm = FakeLLM("Auto summary", "Final answer")
        config = DummyConfig(
            auto_compact=True,
            auto_compact_tokens=100,
            compact_keep_messages=1,
            context_tokens=100000,
            history_messages=20,
            history_chars=100000,
        )
        agent = Agent(config, llm, DummyBoard())
        agent.history = [
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
        ]
        explanation, has_code = agent.ask("new question")
        self.assertFalse(has_code)
        self.assertEqual("Final answer", explanation)
        self.assertEqual("Auto summary", agent.summary)
        self.assertIn("new question", agent.history[-2]["content"])
        self.assertEqual(2, len(llm.calls))

    def test_auto_compaction_failure_does_not_block_request(self):
        llm = FailingSummaryLLM()
        notices = []
        config = DummyConfig(
            auto_compact=True,
            auto_compact_tokens=100,
            compact_keep_messages=1,
            context_tokens=100000,
        )
        agent = Agent(config, llm, DummyBoard())
        agent.on_notice = notices.append
        agent.history = [
            {"role": "user", "content": "x" * 400},
            {"role": "assistant", "content": "y" * 400},
        ]
        explanation, has_code = agent.ask("new question")
        self.assertFalse(has_code)
        self.assertEqual("Final answer", explanation)
        self.assertEqual("", agent.summary)
        self.assertTrue(any("Automatic compaction failed" in text for text in notices))
        self.assertEqual(2, llm.calls)

    def test_context_usage_reports_summary_and_budget(self):
        config = DummyConfig(
            auto_compact=False,
            context_tokens=1000,
            auto_compact_tokens=750,
        )
        agent = Agent(config, FakeLLM(), DummyBoard())
        agent.summary = "old context"
        agent.history = [{"role": "user", "content": "abcd"}]
        usage = agent.context_usage()
        self.assertEqual(1000, usage["context_tokens"])
        self.assertEqual(1, usage["messages"])
        self.assertEqual(11, usage["summary_chars"])
        self.assertGreater(usage["estimated_tokens"], 0)
        self.assertEqual(750, usage["auto_compact_tokens"])
        self.assertEqual(False, usage["auto_compact"])

    def test_context_usage_caches_system_prompt_tokens(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        calls = []
        original = agent._system_content

        def counted():
            calls.append(1)
            return original()

        agent._system_content = counted
        clock = {"now": 1000}
        with mock.patch("harness.agent._ticks_ms", lambda: clock["now"]):
            first = agent.context_usage()
            second = agent.context_usage()
            self.assertEqual(1, len(calls))
            self.assertEqual(first["system_tokens"], second["system_tokens"])
            clock["now"] += 61000
            third = agent.context_usage()
            self.assertEqual(2, len(calls))
            fourth = agent.context_usage(fresh=True)
            self.assertEqual(3, len(calls))
            self.assertEqual(third["system_tokens"], fourth["system_tokens"])
            agent.clear()
            agent.context_usage()
            self.assertEqual(4, len(calls))

    def test_clear_resets_summary(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        agent.summary = "remember this"
        agent.original_request = "first question"
        agent.history = [{"role": "user", "content": "hello"}]
        agent.clear()
        self.assertEqual("", agent.summary)
        self.assertEqual("", agent.original_request)
        self.assertEqual([], agent.history)

    def test_clear_rotates_model_session(self):
        class SessionLLM:
            def __init__(self):
                self.rotations = 0

            def complete(self, messages, max_tokens=None, thinking_level=None,
                         allow_truncated=False):
                return "ok"

            def new_session(self):
                self.rotations += 1

        llm = SessionLLM()
        agent = Agent(DummyConfig(), llm, DummyBoard())
        agent.clear()
        self.assertEqual(1, llm.rotations)


class ResponseParsingTests(unittest.TestCase):
    def test_extracts_python_and_explanation(self):
        explanation, source = parse_response("Before\n```python\nprint(42)\n```\nAfter")
        self.assertEqual("Before\nAfter", explanation)
        self.assertEqual("print(42)", source)

    def test_plain_response_has_no_code(self):
        self.assertEqual(("Done.", None), parse_response("Done."))

    def test_untagged_code_block_is_not_executable(self):
        text = "Output:\n```\nA\nB\nC\n```"
        explanation, source = parse_response(text)
        self.assertIsNone(source)
        self.assertIn("A\nB\nC", explanation)

    def test_searches_past_non_python_blocks(self):
        text = "Output:\n```text\nA\n```\nCode:\n```python\nprint('ok')\n```"
        explanation, source = parse_response(text)
        self.assertEqual("print('ok')", source)
        self.assertIn("Output:", explanation)

    def test_unterminated_code_is_rejected(self):
        with self.assertRaises(AgentError):
            parse_response("```python\nprint(1)")

    def test_tool_call_parsing(self):
        text = "Calling camera.\n```tool\n{\"name\":\"camera_capture\",\"args\":{\"pin\":4}}\n```"
        explanation, call = parse_tool_call(text)
        self.assertEqual("camera_capture", call["name"])
        self.assertEqual({"pin": 4}, call["args"])
        self.assertNotIn("```tool", explanation)

    def test_inline_tool_call_parsing(self):
        text = '```tool {"name":"__skill","args":{"action":"list"}}```'
        explanation, call = parse_tool_call(text)
        self.assertEqual("__skill", call["name"])
        self.assertEqual({"action": "list"}, call["args"])
        self.assertEqual("", explanation)

    def test_tool_call_accepts_arguments_alias_and_block_alias(self):
        text = (
            "```tool_call\n"
            '{"name":"__workspace","arguments":"{\\"action\\":\\"read\\",'
            '\\"path\\":\\"note.txt\\"}"}\n'
            "```"
        )
        _, call = parse_tool_call(text)
        self.assertEqual("__workspace", call["name"])
        self.assertEqual(
            {"action": "read", "path": "note.txt"}, call["args"]
        )

    def test_tool_call_accepts_stringified_args(self):
        text = (
            "```tool\n"
            '{"name":"__workspace","args":"{\\"action\\":\\"list\\"}"}\n'
            "```"
        )
        _, call = parse_tool_call(text)
        self.assertEqual({"action": "list"}, call["args"])

    def test_inline_python_block_parsing(self):
        explanation, source = parse_response("Run: ```python print(7)```")
        self.assertEqual("print(7)", source)
        self.assertIn("Run:", explanation)

    def test_tool_call_rejects_bad_json(self):
        with self.assertRaises(AgentError):
            parse_tool_call("```tool\nnot-json\n```")

    def test_builtin_skill_tool_call_is_allowed(self):
        _, call = parse_tool_call(
            "```tool\n{\"name\":\"__skill\",\"args\":{\"action\":\"list\"}}\n```"
        )
        self.assertEqual("__skill", call["name"])

    def test_builtin_background_tool_call_is_allowed(self):
        _, call = parse_tool_call(
            "```tool\n{\"name\":\"__background\",\"args\":{\"action\":\"stop\"}}\n```"
        )
        self.assertEqual("__background", call["name"])
        self.assertEqual({"action": "stop"}, call["args"])

    def test_background_start_allows_large_source_args(self):
        source = "print('line')\n" * 400
        text = (
            "```tool\n"
            '{"name":"__background","args":{"action":"start",'
            '"source":%s}}\n```' % json.dumps(source)
        )
        with self.assertRaises(AgentError):
            parse_tool_call(text)
        _, call = parse_tool_call(
            text, args_limit=2048, background_args_limit=20000)
        self.assertEqual("start", call["args"]["action"])
        self.assertEqual(source, call["args"]["source"])
        # Non-start actions keep the small limit.
        with self.assertRaises(AgentError):
            parse_tool_call(
                "```tool\n"
                '{"name":"__background","args":{"action":"note","text":%s}}\n'
                "```" % json.dumps(source),
                background_args_limit=20000,
            )

    def test_unregistered_dunder_tool_is_still_rejected(self):
        with self.assertRaises(AgentError) as raised:
            parse_tool_call(
                "```tool\n{\"name\":\"__not_a_tool\",\"args\":{}}\n```"
            )
        self.assertIn("Names may not start", str(raised.exception))

    def test_skill_proposal_parsing(self):
        text = (
            "Proposing.\n```skill\n"
            '{"name":"board-notes","description":"Board inspection notes"}\n'
            "---\n"
            "# Board notes\n"
            "Use device.info() first.\n"
            "```"
        )
        explanation, proposal = parse_skill_proposal(text)
        self.assertEqual("board-notes", proposal["name"])
        self.assertEqual("Board inspection notes", proposal["description"])
        self.assertIn("Use device.info()", proposal["body"])
        self.assertNotIn("```skill", explanation)

    def test_skill_proposal_rejects_missing_description(self):
        text = (
            "```skill\n"
            '{"name":"bad"}\n'
            "---\n"
            "# Bad\n"
            "```"
        )
        with self.assertRaises(AgentError):
            parse_skill_proposal(text)

class ApiRetryTests(unittest.TestCase):
    def test_transient_api_error_is_retried(self):
        llm = ErrorLLM(failures=2)
        notices = []
        agent = Agent(
            DummyConfig(
                stream=False, api_retries=3,
                api_retry_base_ms=0, api_retry_max_ms=0,
            ),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("hello")
        self.assertEqual("Recovered", explanation)
        self.assertFalse(has_code)
        self.assertEqual(3, llm.calls)
        self.assertEqual(2, sum("retrying in 0ms" in text for text in notices))

    def test_api_retry_limit_is_bounded(self):
        llm = ErrorLLM(failures=10)
        agent = Agent(
            DummyConfig(
                stream=False, api_retries=2,
                api_retry_base_ms=0, api_retry_max_ms=0,
            ),
            llm, DummyBoard(),
        )
        with self.assertRaises(LLMError):
            agent.ask("hello")
        self.assertEqual(3, llm.calls)
        self.assertEqual("hello", agent.history[0]["content"])
        self.assertIn("did not finish", agent.history[-1]["content"])

    def test_non_retryable_api_error_is_not_retried(self):
        llm = ErrorLLM(
            failures=10, message="Model API HTTP 401: unauthorized",
            status=401, retryable=False,
        )
        agent = Agent(
            DummyConfig(
                stream=False, api_retries=3,
                api_retry_base_ms=0, api_retry_max_ms=0,
            ),
            llm, DummyBoard(),
        )
        with self.assertRaises(LLMError):
            agent.ask("hello")
        self.assertEqual(1, llm.calls)

    def test_retry_backoff_honors_retry_after(self):
        agent = Agent(
            DummyConfig(
                api_retries=3, api_retry_base_ms=1000,
                api_retry_max_ms=30000, api_retry_jitter_pct=0,
            ),
            FakeLLM(), DummyBoard(),
        )
        plain = llm_error("busy", retryable=True)
        self.assertEqual(1000, agent._api_retry_delay_ms(plain, 1))
        self.assertEqual(2000, agent._api_retry_delay_ms(plain, 2))
        self.assertEqual(4000, agent._api_retry_delay_ms(plain, 3))
        self.assertEqual(30000, agent._api_retry_delay_ms(plain, 10))
        limited = llm_error("busy", retryable=True, retry_after=7)
        self.assertEqual(7000, agent._api_retry_delay_ms(limited, 1))
        long_wait = llm_error("busy", retryable=True, retry_after=120)
        self.assertEqual(120000, agent._api_retry_delay_ms(long_wait, 1))

    def test_cancelled_stream_does_not_emit_model_result(self):
        class CancelLLM:
            def __init__(self):
                self.calls = 0

            def complete_stream(self, messages, **kwargs):
                self.calls += 1
                raise LLMStreamCancelled()

        llm = CancelLLM()
        agent = Agent(DummyConfig(stream=True), llm, DummyBoard())
        results = []
        agent.on_model_result = results.append
        with self.assertRaises(LLMStreamCancelled):
            agent.ask("hello")
        self.assertEqual(1, llm.calls)
        self.assertEqual([], results)

    def test_partial_stream_output_is_not_retried(self):
        class PartialLLM:
            def __init__(self):
                self.calls = 0
                self.last_streamed = True
                self.last_content_chars = 5
                self.last_reasoning_chars = 0

            def complete_stream(self, messages, **kwargs):
                self.calls += 1
                error = llm_error("SSE idle timeout", retryable=True)
                error.partial = True
                raise error

        llm = PartialLLM()
        agent = Agent(
            DummyConfig(
                stream=True, api_retries=3,
                api_retry_base_ms=0, api_retry_max_ms=0,
                thinking_fallback="low",
            ),
            llm, DummyBoard(),
        )
        with self.assertRaises(LLMError):
            agent.ask("hello")
        self.assertEqual(1, llm.calls)

    def test_compaction_request_is_retried(self):
        class FlakySummaryLLM:
            def __init__(self):
                self.calls = 0

            def complete(self, messages, max_tokens=None, thinking_level=None,
                         allow_truncated=False, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise llm_error(
                        "Model API HTTP 503: busy",
                        status=503, retryable=True,
                    )
                return "Recovered summary"

        llm = FlakySummaryLLM()
        agent = Agent(
            DummyConfig(
                auto_compact=False, compact_keep_messages=2,
                api_retries=2, api_retry_base_ms=0, api_retry_max_ms=0,
            ),
            llm, DummyBoard(),
        )
        agent.history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "answer"},
        ]
        self.assertTrue(agent.compact())
        self.assertEqual(2, llm.calls)
        self.assertEqual("Recovered summary", agent.summary)


class FakeMCP:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))

        class Result:
            ok = True
            output = "remote result"
            error = ""
            elapsed_ms = 3

        return Result()


class RecordingExecutor:
    def __init__(self, result=None):
        self.timeout_s = "unset"
        self.result = result or ExecutionResult(True, "ok", "", 0)

    def execute(self, source, filename="<agent>", timeout_s=None):
        self.timeout_s = timeout_s
        return self.result


class AgentTests(unittest.TestCase):
    def test_assistant_turn_keeps_reasoning_for_history(self):
        agent = Agent(DummyConfig(), ReasoningLLM("Done"), DummyBoard())
        agent.ask("explain the plan")
        self.assertEqual("assistant", agent.history[-1]["role"])
        self.assertEqual("Done", agent.history[-1]["content"])
        self.assertEqual("step-by-step check",
                         agent.history[-1]["reasoning"])

    def test_rewind_removes_user_message_and_later_replies(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        agent.history = [
            {"role": "user", "content": "first", "kind": "user"},
            {"role": "assistant", "content": "one", "kind": "assistant"},
            {"role": "user", "content": "Local ESP32 execution result.\nstatus=ok",
             "kind": "observation"},
            {"role": "user", "content": "second", "kind": "user"},
            {"role": "assistant", "content": "two", "kind": "assistant"},
        ]
        agent.pending_source = "print(1)"
        agent.pending_question = {"header": "Q", "question": "?", "options": []}
        agent.last_result = object()
        index, removed = agent.rewind_last(1)
        self.assertEqual(3, index)
        self.assertEqual(2, removed)
        self.assertEqual(
            ["first", "one", "Local ESP32 execution result.\nstatus=ok"],
            [message["content"] for message in agent.history],
        )
        self.assertIsNone(agent.pending_source)
        self.assertIsNone(agent.pending_question)
        self.assertIsNone(agent.last_result)

    def test_rewind_ignores_legacy_observations_and_assistant_targets(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        agent.history = [
            {"role": "user", "content": "real task"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "The previous turn did not finish."},
        ]
        self.assertEqual(0, agent.find_user_index(1))
        with self.assertRaises(AgentError):
            agent.rewind_to(1)
        removed = agent.rewind_to(0)
        self.assertEqual(3, removed)
        self.assertEqual([], agent.history)

    def test_rewind_persists_truncated_history(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        from harness.conversation import ConversationStore
        config = DummyConfig(persist_conversation=True)
        store = ConversationStore(directory.name, config)
        agent = Agent(config, FakeLLM(), DummyBoard(), conversation=store)
        agent.history = [
            {"role": "user", "content": "keep", "kind": "user"},
            {"role": "assistant", "content": "kept", "kind": "assistant"},
            {"role": "user", "content": "drop", "kind": "user"},
            {"role": "assistant", "content": "dropped", "kind": "assistant"},
        ]
        agent.rewind_last(1)
        state = store.load()
        self.assertEqual(["keep", "kept"],
                         [message["content"] for message in state["messages"]])

    def test_background_result_marker_deduplicates_resume(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        result = ExecutionResult(True, "42", "", 5)
        agent.record_execution_result(
            result, background=True, job_id=9)
        self.assertIn("(job=9)", agent.history[-1]["content"])
        self.assertIn("Local ESP32 background execution result",
                      agent.history[-1]["content"])
        self.assertEqual("observation", agent.history[-1]["kind"])
        self.assertTrue(agent.has_background_result(9))
        self.assertFalse(agent.has_background_result(10))
        self.assertIs(agent.last_result, result)

    def test_background_marker_token_does_not_match_old_numeric_job(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        agent.history = [{
            "role": "user", "content": "old result (job=1)",
            "kind": "observation",
        }]
        self.assertTrue(agent.has_background_result(1))
        self.assertFalse(agent.has_background_result("1@12345"))

    def test_background_observation_identifies_the_original_task(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        result = ExecutionResult(True, "ok", "", 5)
        agent.record_execution_result(
            result, background=True, job_id=12,
            task="draw the LCD\ndashboard for one minute")
        content = agent.history[-1]["content"]
        self.assertIn("draw the LCD dashboard for one minute", content)
        self.assertIn("earlier user task", content)
        self.assertIn("Later user messages may be unrelated", content)
        self.assertNotIn("continues this result automatically", content)

    def test_auto_feedback_off_does_not_claim_automatic_continuation(self):
        agent = Agent(
            DummyConfig(auto_run=True, auto_feedback=False),
            FakeLLM(), DummyBoard())
        result = ExecutionResult(True, "ok", "", 5)
        agent.record_execution_result(
            result, background=True, job_id=13)
        self.assertNotIn("continues this result automatically",
                         agent.history[-1]["content"])
        prompt = system_prompt(
            agent.config, DummyBoard(), workspace=True)
        self.assertNotIn("continues a completed background result",
                         prompt)

    def test_private_message_metadata_is_not_sent_to_the_model(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        agent.history = [
            {"role": "user", "content": "task", "kind": "user"},
            {"role": "assistant", "content": "answer", "kind": "assistant"},
        ]
        messages = agent._build_messages([])
        for message in messages[1:]:
            self.assertEqual({"role", "content"}, set(message.keys()))

    def test_code_timeout_fence_is_parsed_and_passed_to_executor(self):
        llm = FakeLLM(
            "```python timeout=45\nprint(1)\n```", "done")
        agent = Agent(DummyConfig(), llm, DummyBoard())
        _, has_code = agent.ask("run it")
        self.assertTrue(has_code)
        self.assertEqual(45, agent.pending_timeout)
        executor = RecordingExecutor()
        agent.execute_pending(executor)
        self.assertEqual(45, executor.timeout_s)
        self.assertIsNone(agent.pending_timeout)

    def test_code_timeout_above_maximum_is_rejected(self):
        agent = Agent(
            DummyConfig(max_format_retries=0, code_timeout_max_s=10),
            FakeLLM("```python timeout=20\nprint(1)\n```"),
            DummyBoard(),
        )
        with self.assertRaises(AgentError):
            agent.ask("too long")
        self.assertEqual("too long", agent.history[0]["content"])
        self.assertIn("did not finish", agent.history[-1]["content"])

    def test_invalid_code_timeout_is_rejected(self):
        agent = Agent(
            DummyConfig(max_format_retries=0),
            FakeLLM("```python timeout=abc\nprint(1)\n```"),
            DummyBoard(),
        )
        with self.assertRaises(AgentError):
            agent.ask("bad timeout")

    def test_code_timeout_accepts_spacing_suffix_and_punctuation(self):
        for info in (
            "python timeout=9",
            "python timeout = 9",
            "python timeout= 9",
            "python timeout =9",
            "python timeout=9s",
            "python timeout=9,",
        ):
            explanation, source, timeout = parse_response_ex(
                "```%s\nprint(1)\n```" % info)
            self.assertEqual(9, timeout, info)
            self.assertEqual("print(1)", source)
            self.assertEqual("", explanation)

    def test_timeout_after_closing_fence_is_recognized(self):
        explanation, source, timeout = parse_response_ex(
            "Before\n```python\nprint(1)\n```\ntimeout=45\nAfter")
        self.assertEqual(45, timeout)
        self.assertEqual("print(1)", source)
        self.assertNotIn("timeout=45", explanation)
        self.assertIn("Before", explanation)
        self.assertIn("After", explanation)

    def test_opening_fence_timeout_wins_over_trailing_text(self):
        _, _, timeout = parse_response_ex(
            "```python timeout=9\nprint(1)\n```\ntimeout=45")
        self.assertEqual(9, timeout)

    def test_leading_timeout_line_is_consumed_when_unused(self):
        _, source, timeout = parse_response_ex(
            "```python\ntimeout = 45\nprint(1)\n```")
        self.assertEqual(45, timeout)
        self.assertEqual("print(1)", source)

    def test_leading_timeout_line_is_kept_when_code_uses_it(self):
        _, source, timeout = parse_response_ex(
            "```python\ntimeout = 45\nprint(timeout)\n```")
        self.assertIsNone(timeout)
        self.assertIn("timeout = 45", source)

    def test_trailing_timeout_prose_is_not_misread(self):
        _, _, timeout = parse_response_ex(
            "```python\nprint(1)\n```\ntimeout=45 was ignored before")
        self.assertIsNone(timeout)

    def test_infinite_timeout_forms(self):
        for info in (
            "python timeout=0",
            "python timeout=infinite",
            "python timeout=inf",
            "python timeout=unlimited",
        ):
            _, _, timeout = parse_response_ex(
                "```%s\nprint(1)\n```" % info)
            self.assertEqual(0, timeout, info)
        _, _, timeout = parse_response_ex(
            "```python\nprint(1)\n```\ntimeout=infinite")
        self.assertEqual(0, timeout)
        _, _, timeout = parse_response_ex(
            "```python\ntimeout=0\nprint(1)\n```")
        self.assertEqual(0, timeout)

    def test_infinite_timeout_is_rejected_for_synchronous_execution(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        agent.accept_response("```python timeout=0\nprint(1)\n```")
        self.assertEqual(0, agent.pending_timeout)
        with self.assertRaises(AgentError):
            agent.execute_pending(RecordingExecutor())
        self.assertEqual("print(1)", agent.pending_source)
        self.assertEqual(0, agent.pending_timeout)

    def test_same_line_code_block_still_parses(self):
        explanation, source, timeout = parse_response_ex(
            '```python print("x")```')
        self.assertEqual('print("x")', source)
        self.assertIsNone(timeout)
        self.assertEqual("", explanation)

    def test_timeout_observation_is_sent_back_to_model(self):
        timeout_result = ExecutionResult(
            False, "partial output", "ExecutionTimeout: Execution exceeded 1 second(s)",
            1000, timed_out=True,
        )
        llm = FakeLLM("The program timed out; use a shorter loop.")
        agent = Agent(DummyConfig(), llm, DummyBoard())
        agent.accept_response("```python timeout=1\nwhile True: pass\n```")
        agent.execute_pending(RecordingExecutor(timeout_result))
        observation = agent.history[-1]["content"]
        self.assertIn("status=timeout", observation)
        self.assertIn("did not reboot", observation)
        explanation, has_code = agent.continue_feedback()
        self.assertEqual("The program timed out; use a shorter loop.", explanation)
        self.assertFalse(has_code)

    def test_executes_pending_code_and_sends_observation(self):
        llm = FakeLLM("```python\nprint(6 * 7)\n```", "The answer is 42.")
        agent = Agent(DummyConfig(), llm, DummyBoard())
        _, has_code = agent.ask("Calculate the answer")
        self.assertTrue(has_code)
        result = agent.execute_pending(Executor(DummyBoard()))
        self.assertTrue(result.ok)
        self.assertIn("42", result.output)
        self.assertIn("Local ESP32 execution result", agent.history[-1]["content"])
        explanation, has_code = agent.continue_feedback()
        self.assertEqual("The answer is 42.", explanation)
        self.assertFalse(has_code)
        self.assertEqual("print(6 * 7)", agent.last_source)

    def test_new_task_cannot_drop_pending_code(self):
        agent = Agent(DummyConfig(), FakeLLM("```python\nprint(1)\n```"), DummyBoard())
        agent.ask("first")
        with self.assertRaises(AgentError):
            agent.ask("second")

    def test_mcp_tool_args_use_mcp_limit(self):
        text = (
            "```tool\n"
            '{"name":"mcp__demo__echo","args":{"text":"%s"}}\n'
            "```" % ("x" * 40)
        )
        _, call = parse_tool_call(
            text, args_limit=10, mcp_args_limit=100)
        self.assertEqual("mcp__demo__echo", call["name"])

    def test_mcp_tool_call_is_routed_to_manager(self):
        response = (
            "```tool\n"
            '{"name":"mcp__demo__echo","args":{"text":"hi"}}\n'
            "```"
        )
        mcp = FakeMCP()
        llm = FakeLLM(response, "MCP answered.")
        agent = Agent(DummyConfig(stream=False), llm, DummyBoard(), mcp=mcp)
        explanation, has_code = agent.ask("use the remote tool")
        self.assertEqual("MCP answered.", explanation)
        self.assertFalse(has_code)
        self.assertEqual([("mcp__demo__echo", {"text": "hi"})], mcp.calls)
        self.assertIn("remote result", agent.history[-2]["content"])

    def test_unknown_custom_tool_is_reported_as_builtin_error(self):
        tool_response = (
            "```tool\n"
            '{"name":"camera_capture","args":{}}\n'
            "```"
        )
        llm = FakeLLM(tool_response, "No such tool.")
        agent = Agent(DummyConfig(stream=False), llm, DummyBoard())
        explanation, has_code = agent.ask("Use the camera")
        self.assertEqual("No such tool.", explanation)
        self.assertFalse(has_code)
        joined = "\n".join(message["content"] for message in agent.history)
        self.assertIn("Unknown built-in tool: camera_capture", joined)

    def test_tooldef_is_rejected_and_retried(self):
        tooldef = (
            "```tooldef\n"
            '{"name":"note_tool","description":"A note tool","args":{}}\n'
            "---\n"
            "def run(args, api):\n"
            '    return "ok"\n'
            "```"
        )
        llm = FakeLLM(tooldef, "No custom tools.")
        notices = []
        agent = Agent(
            DummyConfig(stream=False, max_format_retries=1),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("Create a tool")
        self.assertEqual("No custom tools.", explanation)
        self.assertFalse(has_code)
        self.assertEqual(2, len(llm.calls))
        self.assertTrue(any("Invalid action protocol" in text for text in notices))
        joined = "\n".join(message["content"] for message in agent.history)
        self.assertIn("no longer supported", joined)

    def test_builtin_skill_list_and_read(self):
        list_response = (
            "```tool\n"
            '{"name":"__skill","args":{"action":"list"}}\n'
            "```"
        )
        read_response = (
            "```tool\n"
            '{"name":"__skill","args":{"action":"read","name":"board-notes"}}\n'
            "```"
        )
        llm = FakeLLM(list_response, read_response, "Skills inspected.")
        agent = Agent(
            DummyConfig(stream=False), llm, DummyBoard(),
            skills=FakeSkills(),
        )
        explanation, has_code = agent.ask("Inspect skills")
        self.assertEqual("Skills inspected.", explanation)
        self.assertFalse(has_code)
        joined = "\n".join(message["content"] for message in agent.history)
        self.assertIn("board-notes", joined)
        self.assertIn("# Board notes", joined)
        self.assertEqual(3, len(llm.calls))

    def test_builtin_skill_delete_removes_skill(self):
        delete_response = (
            "```tool\n"
            '{"name":"__skill","args":{"action":"delete","name":"board-notes"}}\n'
            "```"
        )
        llm = FakeLLM(delete_response, "Skill deleted.")
        skills = FakeSkills()
        agent = Agent(
            DummyConfig(stream=False), llm, DummyBoard(), skills=skills,
        )
        explanation, has_code = agent.ask("Delete the board-notes skill")
        self.assertEqual("Skill deleted.", explanation)
        self.assertFalse(has_code)
        self.assertEqual(["board-notes"], skills.deleted)
        joined = "\n".join(message["content"] for message in agent.history)
        self.assertIn("Deleted skill: board-notes", joined)

    def test_stream_timeout_retries_without_lowering_thinking(self):
        llm = FallbackLLM()
        notices = []
        agent = Agent(
            DummyConfig(
                stream=True, thinking_level="max", thinking_fallback="low",
                api_retry_base_ms=0, api_retry_max_ms=0,
                api_retry_jitter_pct=0,
            ),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("Answer directly")
        self.assertEqual("OK", explanation)
        self.assertFalse(has_code)
        # Both attempts run at the configured level: thinking_level=None means
        # the client uses config["thinking_level"] (max) unchanged.
        self.assertEqual([None, None], llm.calls)
        self.assertFalse(any("retrying with low" in text for text in notices))
        self.assertTrue(any("retrying" in text for text in notices))

    def test_retired_middle_level_still_retries_at_high(self):
        llm = FallbackLLM()
        agent = Agent(
            DummyConfig(
                stream=True, thinking_level="middle", thinking_fallback="low",
                api_retry_base_ms=0, api_retry_max_ms=0,
                api_retry_jitter_pct=0,
            ),
            llm, DummyBoard(),
        )
        explanation, has_code = agent.ask("Answer directly")
        self.assertEqual("OK", explanation)
        self.assertFalse(has_code)
        self.assertEqual([None, None], llm.calls)

    def test_truncation_retries_with_larger_max_tokens(self):
        llm = TruncationLLM(failures=1)
        notices = []
        agent = Agent(
            DummyConfig(
                stream=True, thinking_level="high", thinking_fallback="low",
                max_tokens=4096, truncation_retry_max_tokens=8192,
            ),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("Answer directly")
        self.assertEqual("OK", explanation)
        self.assertFalse(has_code)
        self.assertEqual([(None, None), (None, 8192)], llm.calls)
        self.assertTrue(any("max_tokens=8192" in text for text in notices))

    def test_truncation_falls_back_after_token_retry(self):
        llm = TruncationLLM(failures=2)
        notices = []
        agent = Agent(
            DummyConfig(
                stream=True, thinking_level="high", thinking_fallback="low",
                max_tokens=4096, truncation_retry_max_tokens=8192,
            ),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("Answer directly")
        self.assertEqual("OK", explanation)
        self.assertFalse(has_code)
        self.assertEqual([(None, None), (None, 8192), ("low", 8192)], llm.calls)
        self.assertTrue(any("retrying with low thinking" in text for text in notices))

    def test_builtin_workspace_write_and_read(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            write_response = (
                "```tool\n"
                '{"name":"__workspace","args":{"action":"write",'
                '"path":"tmp/note.txt","content":"hello"}}\n'
                "```"
            )
            read_response = (
                "```tool\n"
                '{"name":"__workspace","args":{"action":"read",'
                '"path":"tmp/note.txt"}}\n'
                "```"
            )
            llm = FakeLLM(write_response, read_response, "Workspace updated.")
            agent = Agent(
                DummyConfig(stream=False), llm, DummyBoard(),
                workspace=workspace,
            )
            explanation, has_code = agent.ask("Save a note")
            self.assertEqual("Workspace updated.", explanation)
            self.assertFalse(has_code)
            self.assertEqual("hello", workspace.read("tmp/note.txt")["text"])
            joined = "\n".join(message["content"] for message in agent.history)
            self.assertIn("Wrote /workspace/tmp/note.txt", joined)
            self.assertIn("hello", joined)

    def test_builtin_workspace_append_and_replace(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            responses = [
                "```tool\n"
                '{"name":"__workspace","args":{"action":"write",'
                '"path":"tmp/script.py","content":"a = 1\\nb = 2\\n"}}\n'
                "```",
                "```tool\n"
                '{"name":"__workspace","args":{"action":"replace",'
                '"path":"tmp/script.py","match":"b = 2","content":"b = 20"}}\n'
                "```",
                "```tool\n"
                '{"name":"__workspace","args":{"action":"append",'
                '"path":"tmp/script.py","content":"c = 3\\n"}}\n'
                "```",
                "Updated in place.",
            ]
            llm = FakeLLM(*responses)
            agent = Agent(
                DummyConfig(stream=False), llm, DummyBoard(),
                workspace=workspace,
            )
            explanation, has_code = agent.ask("Debug the script")
            self.assertEqual("Updated in place.", explanation)
            self.assertFalse(has_code)
            self.assertEqual(
                "a = 1\nb = 20\nc = 3\n",
                workspace.read("tmp/script.py")["text"])
            joined = "\n".join(
                message["content"] for message in agent.history)
            self.assertIn("Replaced 1 occurrence(s)", joined)
            self.assertIn("Appended to /workspace/tmp/script.py", joined)

    def test_ask_tool_ends_turn_and_answers_next_message(self):
        ask_response = (
            "```tool\n"
            '{"name":"__ask","args":{"question":"Which one?",'
            '"header":"Choice","options":["A","B"]}}\n'
            "```"
        )
        llm = FakeLLM(ask_response, "Done")
        agent = Agent(DummyConfig(stream=False), llm, DummyBoard())

        explanation, has_code = agent.ask("Choose an approach")
        self.assertFalse(has_code)
        self.assertEqual("", explanation)
        self.assertEqual("question", agent.pending_kind())
        self.assertEqual({
            "header": "Choice",
            "question": "Which one?",
            "options": [
                {"label": "A", "description": ""},
                {"label": "B", "description": ""},
            ],
            "allow_custom": True,
        }, agent.pending_question)
        joined = "\n".join(message["content"] for message in agent.history)
        self.assertIn("Which one?", joined)
        self.assertIn("1. A", joined)

        explanation, has_code = agent.ask("2")
        self.assertEqual("Done", explanation)
        self.assertFalse(has_code)
        self.assertIsNone(agent.pending_question)
        self.assertEqual("B", llm.calls[-1][-1]["content"])
        self.assertTrue(any(
            message["role"] == "assistant" and "Which one?" in message["content"]
            for message in llm.calls[-1]
        ))

    def test_ask_tool_rejects_empty_question_and_continues(self):
        bad = (
            "```tool\n"
            '{"name":"__ask","args":{"question":""}}\n'
            "```"
        )
        llm = FakeLLM(bad, "OK")
        agent = Agent(DummyConfig(stream=False), llm, DummyBoard())
        explanation, has_code = agent.ask("Ask")
        self.assertEqual("OK", explanation)
        self.assertFalse(has_code)
        self.assertIsNone(agent.pending_question)
        self.assertIn("question is required", llm.calls[-1][-1]["content"])

    def test_hand_written_question_block_becomes_pending_question(self):
        response = (
            "--- question for you: 屏幕画面 ---\n"
            "重新确认一下刚才那张图，屏幕中央的彩色图案细节如何？\n"
            "  1. 图案清晰、颜色正确（红绿蓝色块分明）\n"
            "  2. 有图案，但颜色偏色或串色（比如红显示成蓝）\n"
            "  3. 图案上下颠倒或左右镜像\n"
            "  4. 图案模糊、有错位或横向条纹\n"
            "Reply with a number, an option label, or your own answer."
        )
        llm = FakeLLM(response, "Done")
        agent = Agent(DummyConfig(stream=False), llm, DummyBoard())
        explanation, has_code = agent.ask("what did you see?")
        self.assertFalse(has_code)
        self.assertIsNotNone(agent.pending_question)
        self.assertEqual("屏幕画面", agent.pending_question["header"])
        self.assertEqual(4, len(agent.pending_question["options"]))
        self.assertIn("彩色图案", agent.history[-1]["content"])

        explanation, has_code = agent.ask("2")
        self.assertEqual("Done", explanation)
        self.assertEqual(
            "有图案，但颜色偏色或串色（比如红显示成蓝）",
            llm.calls[-1][-1]["content"],
        )

    def test_question_marker_inside_code_fence_is_not_a_question(self):
        response = (
            "Example format:\n"
            "```text\n"
            "--- question for you: demo ---\n"
            "Continue?\n"
            "  1. Yes\n"
            "```\n"
        )
        agent = Agent(
            DummyConfig(stream=False), FakeLLM(response), DummyBoard())
        explanation, has_code = agent.ask("show the format")
        self.assertFalse(has_code)
        self.assertIsNone(agent.pending_question)

    def test_ask_accepts_eight_options(self):
        agent = Agent(DummyConfig(stream=False), FakeLLM(), DummyBoard())
        labels = ["option-%s" % index for index in range(8)]
        result = agent._ask_user({
            "question": "Pick one",
            "header": "Choice",
            "options": labels,
        })
        self.assertTrue(result.ok)
        self.assertTrue(result.end_turn)
        self.assertEqual(
            labels,
            [option["label"] for option in agent.pending_question["options"]],
        )

    def test_ask_rejects_more_than_eight_options(self):
        agent = Agent(DummyConfig(stream=False), FakeLLM(), DummyBoard())
        result = agent._ask_user({
            "question": "Pick one",
            "options": ["option-%s" % index for index in range(9)],
        })
        self.assertFalse(result.ok)
        self.assertIn("at most 8 options", result.error)
        self.assertIsNone(agent.pending_question)

    def test_format_question_renders_options_and_custom_hint(self):
        text = format_question({
            "header": "Approach",
            "question": "Which approach?",
            "options": [
                {"label": "I2C", "description": "fewer pins"},
                {"label": "SPI", "description": ""},
            ],
            "allow_custom": True,
        })
        self.assertIn("question for you: Approach", text)
        self.assertIn("1. I2C - fewer pins", text)
        self.assertIn("2. SPI", text)
        self.assertIn("your own answer", text)

    def test_background_tool_status_and_stop(self):
        class Runner:
            def __init__(self):
                self.stopped = None

            def background_active(self):
                return True

            def background_view(self):
                return {
                    "id": 5, "phase": "running", "task": "draw dashboard",
                    "elapsed_ms": 1234, "timeout_s": 0,
                    "stop_requested": False, "result": None, "error": "",
                }

            def stop_background(self, reason=""):
                self.stopped = reason
                return True

        runner = Runner()
        executor = Executor(DummyBoard())
        executor.main_runner = runner
        agent = Agent(
            DummyConfig(), FakeLLM(), DummyBoard(), executor=executor)
        result = agent._run_background_tool({"action": "status"})
        self.assertTrue(result.ok)
        self.assertIn("draw dashboard", result.output)
        self.assertIn("timeout_s: infinite", result.output)
        result = agent._run_background_tool({"action": "stop"})
        self.assertTrue(result.ok)
        self.assertIn("cooperative", result.output)
        self.assertEqual("requested by model", runner.stopped)

        no_runner = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        result = no_runner._run_background_tool({"action": "status"})
        self.assertFalse(result.ok)
        self.assertIn("unavailable", result.error)

    def test_background_tool_stop_discard_drops_result(self):
        class Runner:
            def __init__(self):
                self.calls = []

            def background_active(self):
                return True

            def background_view(self, job_id=None):
                return {
                    "id": 5, "phase": "running", "task": "draw",
                    "note": "dashboard", "elapsed_ms": 10, "timeout_s": 0,
                    "stop_requested": False, "discard_on_finish": False,
                    "result": None, "error": "",
                }

            def stop_background(self, reason="", job_id=None, discard=False):
                self.calls.append((reason, job_id, discard))
                return 1

        runner = Runner()
        executor = Executor(DummyBoard())
        executor.main_runner = runner
        agent = Agent(
            DummyConfig(), FakeLLM(), DummyBoard(), executor=executor)
        result = agent._run_background_tool(
            {"action": "stop_discard", "id": 5})
        self.assertTrue(result.ok, result.error)
        self.assertIn("Stop and discard", result.output)
        self.assertIn("will be dropped", result.output)
        self.assertEqual([("requested by model", 5, True)], runner.calls)

    def test_background_tool_lists_manages_multiple_jobs(self):
        class Runner:
            def __init__(self):
                self.stopped = []
                self.notes = []
                self.discarded = []
                self.jobs = [
                    {"id": 2, "phase": "running", "note": "dashboard",
                     "task": "draw", "elapsed_ms": 1200, "timeout_s": 0,
                     "source_chars": 20, "driver": "web",
                     "stop_requested": False, "result": None, "error": ""},
                    {"id": 3, "phase": "ready", "note": "sensor",
                     "task": "read", "elapsed_ms": 40, "timeout_s": 20,
                     "source_chars": 10, "driver": "web",
                     "stop_requested": False, "error": "",
                     "result": {"ok": True, "timed_out": False,
                                "cancelled": False, "output": "sensor=1",
                                "error": "", "elapsed_ms": 40}},
                ]

            def background_active(self):
                return any(job["phase"] in ("queued", "running")
                           for job in self.jobs)

            def background_summary_lines(self, include_output=False):
                return ["Background jobs: 2."] + [
                    "#%s %s note=%s elapsed=%sms" % (
                        job["id"], job["phase"], job["note"],
                        job["elapsed_ms"]) for job in self.jobs]

            def background_view(self, job_id=None):
                for job in self.jobs:
                    if job_id is None or job["id"] == job_id:
                        return job
                return {"id": 0, "phase": "none"}

            def stop_background(self, reason="", job_id=None):
                self.stopped.append((reason, job_id))
                return 1

            def set_background_note(self, job_id, text):
                self.notes.append((job_id, text))
                for job in self.jobs:
                    if job["id"] == job_id:
                        job["note"] = text
                        return job
                raise RuntimeError("No background job")

            def discard_background(self, record=False, job_id=None):
                self.discarded.append((record, job_id))
                return True

        runner = Runner()
        executor = Executor(DummyBoard())
        executor.main_runner = runner
        agent = Agent(
            DummyConfig(), FakeLLM(), DummyBoard(), executor=executor)
        result = agent._run_background_tool({"action": "list"})
        self.assertTrue(result.ok)
        self.assertIn("#2", result.output)
        self.assertIn("#3", result.output)
        self.assertIn("dashboard", result.output)
        self.assertIn("sensor", result.output)
        result = agent._run_background_tool(
            {"action": "status", "id": 3})
        self.assertTrue(result.ok)
        self.assertIn("sensor=1", result.output)
        result = agent._run_background_tool(
            {"action": "stop", "id": 2})
        self.assertTrue(result.ok)
        self.assertEqual([("requested by model", 2)], runner.stopped)
        result = agent._run_background_tool(
            {"action": "note", "id": 2, "text": "menu"})
        self.assertTrue(result.ok)
        self.assertEqual([(2, "menu")], runner.notes)
        result = agent._run_background_tool(
            {"action": "discard", "id": 3})
        self.assertTrue(result.ok)
        self.assertEqual([(True, 3)], runner.discarded)

    def test_background_tool_can_start_only_in_auto_mode(self):
        class Runner:
            def __init__(self):
                self.started = []

            def background_view(self, job_id=None):
                return {"id": 0, "phase": "none"}

            def start_background_source(self, source, timeout=None, auto=False,
                                        driver="agent", note=""):
                self.started.append((source, timeout, auto, driver, note))
                return {
                    "id": 7, "phase": "running", "timeout": timeout,
                    "note": note or "task", "auto": auto,
                }

        runner = Runner()
        executor = Executor(DummyBoard())
        executor.main_runner = runner
        config = DummyConfig(auto_run=True, background_execution=True)
        agent = Agent(config, FakeLLM(), DummyBoard(), executor=executor)
        result = agent._run_background_tool({
            "action": "start", "source": "print(1)", "note": "menu",
            "timeout": 45,
        })
        self.assertTrue(result.ok, result.error)
        self.assertIn("#7", result.output)
        self.assertEqual(
            [("print(1)", 45, True, "agent", "menu")], runner.started)

        config.values["auto_run"] = False
        result = agent._run_background_tool({
            "action": "start", "source": "print(1)"})
        self.assertFalse(result.ok)
        self.assertIn("AUTO", result.error)
        self.assertEqual(1, len(runner.started))

        config.values["auto_run"] = True
        result = agent._run_background_tool({
            "action": "start", "source": "while True:\n    pass"})
        self.assertFalse(result.ok)
        self.assertIn("timeout=0", result.error)
        result = agent._run_background_tool({
            "action": "start", "source": "while True:\n    pass",
            "timeout": 0})
        self.assertTrue(result.ok, result.error)
        self.assertEqual(0, runner.started[-1][1])

        result = agent._run_background_tool({
            "action": "start", "source": "if True print(1)"})
        self.assertFalse(result.ok)
        self.assertIn("syntax", result.error)

    def test_background_tool_is_listed_in_prompt(self):
        prompt = builtin_tool_prompt(DummyConfig())
        self.assertIn("__background", prompt)
        self.assertIn('"action":"stop"', prompt.replace(" ", ""))
        self.assertIn('"action":"list"', prompt.replace(" ", ""))
        self.assertNotIn('"action":"start"', prompt.replace(" ", ""))
        auto_prompt = builtin_tool_prompt(
            DummyConfig(auto_run=True, background_execution=True))
        self.assertIn('"action":"start"', auto_prompt.replace(" ", ""))
        self.assertIn("timeout=0", auto_prompt)

    def test_response_asks_question_detection(self):
        self.assertTrue(response_asks_question("选逗号还是空格？"))
        self.assertTrue(response_asks_question("Which approach should I use?"))
        self.assertTrue(response_asks_question("输出是什么?"))
        self.assertTrue(response_asks_question("请告诉我选择 A 还是 B"))
        self.assertFalse(response_asks_question("结果是 42。"))
        self.assertFalse(response_asks_question("Why? Because the loop ran once."))
        self.assertFalse(response_asks_question(""))

    def test_auto_run_rejects_question_plus_code(self):
        bad = "选 A 还是 B？\n\n```python\nprint('A')\n```"
        corrected = (
            "```tool\n"
            '{"name":"__ask","args":{"question":"选 A 还是 B？",'
            '"options":["A","B"]}}\n'
            "```"
        )
        llm = FakeLLM(bad, corrected)
        agent = Agent(DummyConfig(stream=False, auto_run=True), llm, DummyBoard())
        explanation, has_code = agent.ask("Do it")
        self.assertFalse(has_code)
        self.assertIsNone(agent.pending_source)
        self.assertEqual("选 A 还是 B？", agent.pending_question["question"])
        self.assertTrue(any(
            message["role"] == "user"
            and "asked the user a question" in message["content"]
            for message in llm.calls[1]
        ))

    def test_auto_run_question_with_code_is_rejected_after_retries(self):
        bad = "选 A 还是 B？\n\n```python\nprint('A')\n```"
        llm = FakeLLM(bad, bad, bad)
        agent = Agent(
            DummyConfig(stream=False, auto_run=True, max_format_retries=1),
            llm, DummyBoard(),
        )
        with self.assertRaises(AgentError):
            agent.ask("Do it")
        self.assertIsNone(agent.pending_source)

    def test_manual_mode_allows_question_and_code(self):
        response = "选 A 还是 B？\n\n```python\nprint('A')\n```"
        agent = Agent(
            DummyConfig(stream=False, auto_run=False),
            FakeLLM(response), DummyBoard(),
        )
        explanation, has_code = agent.ask("Do it")
        self.assertTrue(has_code)
        self.assertIn("选 A", explanation)

    def test_system_prompt_reflects_auto_mode_and_ask_tool(self):
        auto = system_prompt(DummyConfig(auto_run=True), DummyBoard())
        self.assertIn("executed automatically", auto)
        self.assertIn("Do not ask the user to run the code", auto)
        self.assertIn("__ask", auto)
        self.assertIn("opening fence line", auto)
        self.assertIn("after the closing fence", auto)
        self.assertIn("Local ESP32 background execution result", auto)
        self.assertIn("rewind", auto)
        manual = system_prompt(DummyConfig(auto_run=False), DummyBoard())
        self.assertIn("only after confirmation", manual)
        no_background = system_prompt(
            DummyConfig(auto_run=False, background_execution=False),
            DummyBoard())
        self.assertNotIn("Local ESP32 background execution result",
                         no_background)

    def test_system_prompt_prefers_workspace_tool_for_file_inspection(self):
        prompt = system_prompt(
            DummyConfig(auto_run=True), DummyBoard(), workspace=True)
        self.assertIn("__workspace", prompt)
        self.assertIn("listdir/open/stat", prompt)
        self.assertIn("do not ask the user whether to continue or discard",
                      prompt.lower())
        without_workspace = system_prompt(
            DummyConfig(auto_run=True), DummyBoard(), workspace=False)
        self.assertNotIn("listdir/open/stat", without_workspace)

    def test_board_capabilities_advertise_native_micropython_hardware(self):
        text = Board().capabilities()
        self.assertIn("import machine", text)
        self.assertIn("machine.Pin", text)
        self.assertIn("machine.SPI", text)
        self.assertIn("no api object", text)
        self.assertIn("no peripheral allowlist", text)
        self.assertIn("CS/DC/RST/BL are ordinary GPIO pins", text)
        self.assertIn("device.info()", text)
        self.assertNotIn("Do not import machine", text)
        self.assertNotIn("api.workspace", text)
        self.assertNotIn("Tools may use", text)

    def test_native_tool_prompt_and_definitions(self):
        class NativeLLM:
            supports_native_tools = True

        agent = Agent(
            DummyConfig(native_tools=True), NativeLLM(), DummyBoard(),
            workspace=object())
        prompt = agent._system_content()
        self.assertIn("Native function tools are provided", prompt)
        self.assertIn("Prefer foreground execution", prompt)
        self.assertIn("import machine", prompt)
        self.assertIn("machine.SPI", prompt)
        self.assertIn("no api object", prompt)
        self.assertIn("no /peripherals", prompt)
        self.assertIn("machine.PWM", prompt)
        self.assertIn("Do not use __ask to ask permission for a read-only probe",
                      prompt)
        definitions = agent._native_tool_definitions()
        workspace = [
            tool for tool in definitions if tool["name"] == "__workspace"
        ][0]
        self.assertIn("background", workspace["parameters"]["properties"])
        self.assertIn("timeout", workspace["parameters"]["properties"])
        self.assertIn("match", workspace["parameters"]["properties"])
        self.assertIn("all", workspace["parameters"]["properties"])
        actions = workspace["parameters"]["properties"]["action"]["enum"]
        self.assertIn("append", actions)
        self.assertIn("replace", actions)
        self.assertIn("several debug cycles", prompt)
        self.assertIn("/workspace/tmp", prompt)

        textual = Agent(
            DummyConfig(native_tools=False), NativeLLM(), DummyBoard(),
            workspace=object())
        self.assertIn("Built-in tool protocol", textual._system_content())
        self.assertEqual([], textual._native_tool_definitions())

    def test_workspace_tool_prompt_forbids_os_only_browsing(self):
        prompt = builtin_tool_prompt(DummyConfig(), workspace=object())
        self.assertIn("Do not return a python block", prompt)
        self.assertIn("listdir/open/stat", prompt)
        self.assertIn("__workspace", prompt)
        self.assertIn('"action":"append"', prompt)
        self.assertIn('"action":"replace"', prompt)
        self.assertIn("write it once to /workspace/tmp", prompt)

    def test_native_tool_calls_execute_and_feed_tool_messages(self):
        class NativeLLM:
            supports_native_tools = True

            def __init__(self):
                self.responses = ["", "done"]
                self.calls_with_tools = []
                self.calls = []
                self.last_tool_calls = []

            def complete(self, messages, max_tokens=None, thinking_level=None,
                         allow_truncated=False, timeout=None, tools=None):
                self.calls.append(messages)
                self.calls_with_tools.append(tools)
                if len(self.calls) == 1:
                    self.last_tool_calls = [{
                        "id": "call_1",
                        "name": "__workspace",
                        "args": {"action": "list"},
                        "error": "",
                    }]
                else:
                    self.last_tool_calls = []
                return self.responses.pop(0)

        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("note.txt", "hello")
            llm = NativeLLM()
            agent = Agent(
                DummyConfig(stream=False, native_tools=True),
                llm, DummyBoard(), workspace=workspace)
            explanation, has_code = agent.ask("list workspace files")
            self.assertEqual("done", explanation)
            self.assertFalse(has_code)
            self.assertTrue(llm.calls_with_tools[0])
            self.assertTrue(any(
                tool["name"] == "__workspace"
                for tool in llm.calls_with_tools[0]))
            second = llm.calls[1]
            assistant = [
                message for message in second
                if message.get("role") == "assistant"
                and message.get("tool_calls")
            ]
            tools = [
                message for message in second if message.get("role") == "tool"
            ]
            self.assertEqual(1, len(assistant))
            self.assertEqual("call_1", assistant[0]["tool_calls"][0]["id"])
            self.assertEqual(1, len(tools))
            self.assertEqual("call_1", tools[0]["tool_call_id"])
            self.assertIn("note.txt", tools[0]["content"])
            self.assertTrue(any(
                message.get("content", "").startswith(
                    "Local tool result for '__workspace'")
                for message in agent.history))
            self.assertFalse(any(
                message.get("role") == "tool" for message in agent.history))

    def test_native_tools_fall_back_when_endpoint_rejects_them(self):
        from harness.http_client import HTTPResponse
        from harness.llm import OpenAIClient

        class SequenceHttp:
            def __init__(self):
                self.calls = []

            def post(self, url, body, headers, **kwargs):
                self.calls.append(json.loads(body))
                if len(self.calls) == 1:
                    return HTTPResponse(
                        400, {}, b'{"error":{"message":"tools are not supported"}}')
                return HTTPResponse(200, {}, json.dumps({
                    "choices": [{"message": {"content": "done"},
                                 "finish_reason": "stop"}]
                }).encode())

        http = SequenceHttp()
        config = DummyConfig(stream=False, api_retries=0)
        agent = Agent(config, OpenAIClient(config, http), DummyBoard())
        explanation, has_code = agent.ask("hello")
        self.assertEqual("done", explanation)
        self.assertFalse(has_code)
        self.assertIn("tools", http.calls[0])
        self.assertNotIn("tools", http.calls[1])
        self.assertTrue(agent._native_tools_failed)
        self.assertIn("Built-in tool protocol", agent._system_content())

    def test_workspace_run_can_start_in_background(self):
        class Runner:
            def __init__(self):
                self.started = []

            def start_background_source(self, source, timeout=None, auto=False,
                                        driver="agent", note=""):
                self.started.append({
                    "source": source, "timeout": timeout, "auto": auto,
                    "driver": driver, "note": note,
                })
                return {"id": 7, "phase": "running", "timeout": timeout}

        class Executor:
            def __init__(self, runner):
                self.main_runner = runner

        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("script.py", "print('background')")
            runner = Runner()
            agent = Agent(
                DummyConfig(stream=False, auto_run=False),
                FakeLLM(), DummyBoard(), workspace=workspace,
                executor=Executor(runner))
            agent.on_workspace_run_confirm = lambda path, source: True
            result = agent._run_workspace_tool({
                "action": "run", "path": "script.py",
                "background": True, "timeout": 0,
            })
            self.assertTrue(result.ok)
            self.assertIn("background job #7", result.output)
            self.assertEqual(1, len(runner.started))
            self.assertEqual("print('background')",
                             runner.started[0]["source"])
            self.assertEqual(0, runner.started[0]["timeout"])
            self.assertFalse(runner.started[0]["auto"])
            self.assertEqual("workspace", runner.started[0]["driver"])

    def test_workspace_background_defaults_to_max_timeout(self):
        class Runner:
            def __init__(self):
                self.timeouts = []

            def start_background_source(self, source, timeout=None, auto=False,
                                        driver="agent", note=""):
                self.timeouts.append(timeout)
                return {"id": 8, "phase": "running", "timeout": timeout}

        class Executor:
            def __init__(self, runner):
                self.main_runner = runner

        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("script.py", "print('long')")
            runner = Runner()
            agent = Agent(
                DummyConfig(stream=False, auto_run=False,
                            code_timeout_max_s=300),
                FakeLLM(), DummyBoard(), workspace=workspace,
                executor=Executor(runner))
            agent.on_workspace_run_confirm = lambda path, source: True
            result = agent._run_workspace_tool({
                "action": "run", "path": "script.py", "background": True,
            })
            self.assertTrue(result.ok, result.error)
            self.assertEqual([300], runner.timeouts)

    def test_workspace_run_with_content_is_rejected_before_file_lookup(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            agent = Agent(
                DummyConfig(stream=False, auto_run=True), FakeLLM("x"),
                DummyBoard(), workspace=workspace,
                executor=Executor(DummyBoard()),
            )
            result = agent._run_workspace_tool({
                "action": "run", "path": "tmp/new.py",
                "content": "print('not written')",
            })
            self.assertFalse(result.ok)
            self.assertIn("action=write first", result.error)

    def test_workspace_run_without_confirmation_callback_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("script.py", "print('workspace-run')")
            agent = Agent(
                DummyConfig(stream=False, auto_run=False), FakeLLM("x"),
                DummyBoard(), workspace=workspace,
                executor=Executor(DummyBoard()),
            )
            result = agent._run_workspace_tool(
                {"action": "run", "path": "script.py"}
            )
            self.assertFalse(result.ok)
            self.assertIn("requires user confirmation", result.error)

    def test_workspace_run_waits_for_user_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("script.py", "print('workspace-run')")
            agent = Agent(
                DummyConfig(stream=False, auto_run=False), FakeLLM("x"),
                DummyBoard(), workspace=workspace,
                executor=Executor(DummyBoard()),
            )
            prompts = []
            agent.on_workspace_run_confirm = (
                lambda path, source: prompts.append((path, source)) or False
            )
            result = agent._run_workspace_tool(
                {"action": "run", "path": "script.py"}
            )
            self.assertFalse(result.ok)
            self.assertIn("cancelled by user", result.error)
            self.assertEqual(1, len(prompts))
            self.assertEqual("/workspace/script.py", prompts[0][0])
            self.assertIn("workspace-run", prompts[0][1])

    def test_workspace_run_executes_after_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("script.py", "print('workspace-run')")
            agent = Agent(
                DummyConfig(stream=False, auto_run=False), FakeLLM("x"),
                DummyBoard(), workspace=workspace,
                executor=Executor(DummyBoard()),
            )
            agent.on_workspace_run_confirm = lambda path, source: True
            result = agent._run_workspace_tool(
                {"action": "run", "path": "script.py"}
            )
            self.assertTrue(result.ok, result.error)
            self.assertIn("workspace-run", result.output)

    def test_workspace_run_executes_with_auto_run(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("script.py", "print('workspace-run')")
            agent = Agent(
                DummyConfig(stream=False, auto_run=True), FakeLLM("x"),
                DummyBoard(), workspace=workspace,
                executor=Executor(DummyBoard()),
            )
            result = agent._run_workspace_tool(
                {"action": "run", "path": "script.py"}
            )
            self.assertTrue(result.ok, result.error)
            self.assertIn("workspace-run", result.output)

    def test_workspace_run_allows_files_above_code_limit(self):
        with tempfile.TemporaryDirectory() as root:
            config = DummyConfig(
                stream=False, auto_run=True, code_limit=32,
                workspace_file_limit=4096,
            )
            workspace = Workspace(root, config, clean_tmp=False)
            source = "value = 1\n" * 40 + "print('above-code-limit')\n"
            workspace.write("script.py", source)
            agent = Agent(
                config, FakeLLM("x"), DummyBoard(), workspace=workspace,
                executor=Executor(DummyBoard()),
            )
            result = agent._run_workspace_tool(
                {"action": "run", "path": "script.py"}
            )
            self.assertTrue(result.ok, result.error)
            self.assertIn("above-code-limit", result.output)

    def test_workspace_missing_action_is_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            agent = Agent(
                DummyConfig(stream=False), FakeLLM("x"), DummyBoard(),
                workspace=workspace,
            )
            result = agent._run_workspace_tool({"path": "script.py"})
            self.assertFalse(result.ok)
            self.assertIn("action is required", result.error)
            self.assertIn("action=write", result.error)

    def test_workspace_run_blocks_obvious_loop(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            workspace.write("loop.py", "while True:\n    pass\n")
            agent = Agent(
                DummyConfig(stream=False, auto_run=True), FakeLLM("x"),
                DummyBoard(), workspace=workspace,
                executor=Executor(DummyBoard()),
            )
            result = agent._run_workspace_tool(
                {"action": "run", "path": "loop.py"}
            )
            self.assertFalse(result.ok)
            self.assertIn("indefinite loop", result.error)

    def test_workspace_action_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Workspace(root, DummyConfig(), clean_tmp=False)
            agent = Agent(
                DummyConfig(stream=False), FakeLLM("x"), DummyBoard(),
                workspace=workspace,
            )
            result = agent._run_workspace_tool({"action": "SUMMARY"})
            self.assertTrue(result.ok, result.error)
            self.assertIn("workspace:", result.output)

    def test_invalid_action_gets_one_protocol_retry(self):
        malformed = (
            "```tool\n"
            '{"name":"__workspace","args":{"action":"read"}\n'
            "```"
        )
        llm = FakeLLM(malformed, "Recovered without a tool.")
        notices = []
        agent = Agent(
            DummyConfig(stream=False, max_format_retries=1),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("Read a note")
        self.assertEqual("Recovered without a tool.", explanation)
        self.assertFalse(has_code)
        self.assertEqual(2, len(llm.calls))
        self.assertTrue(any("Invalid action protocol" in text for text in notices))
        joined = "\n".join(message["content"] for message in agent.history)
        self.assertIn("Local harness protocol error", joined)

    def test_invalid_action_fails_after_retry_budget(self):
        malformed = (
            "```tool\n"
            '{"name":"__workspace","args":{"action":"read"}\n'
            "```"
        )
        agent = Agent(
            DummyConfig(stream=False, max_format_retries=1),
            FakeLLM(malformed, malformed), DummyBoard(),
        )
        with self.assertRaises(AgentError) as caught:
            agent.ask("Read a note")
        self.assertIn("invalid action after retry", str(caught.exception))
        self.assertEqual("Read a note", agent.history[0]["content"])
        self.assertIn("did not finish", agent.history[-1]["content"])

    def test_mixed_tool_call_and_code_gets_one_protocol_retry(self):
        mixed = (
            "I will inspect the workspace.\n"
            "```tool\n"
            '{"name":"__workspace","args":{"action":"summary"}}\n'
            "```\n"
            "```python\n"
            "print(1)\n"
            "```"
        )
        llm = FakeLLM(mixed, "Recovered without an action.")
        notices = []
        agent = Agent(
            DummyConfig(stream=False, max_format_retries=1),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("Inspect the workspace")
        self.assertEqual("Recovered without an action.", explanation)
        self.assertFalse(has_code)
        self.assertEqual(2, len(llm.calls))
        self.assertTrue(any(
            "Invalid action protocol" in text and "mixed a tool call" in text
            for text in notices
        ))
        joined = "\n".join(message["content"] for message in agent.history)
        self.assertIn("Local harness protocol error", joined)
        self.assertIn("must not be combined with a python code block", joined)
        self.assertIsNone(agent.pending_source)

    def test_mixed_skill_proposal_and_code_gets_protocol_retry(self):
        mixed = (
            "```skill\n"
            '{"name":"note","description":"A note"}\n'
            "---\n"
            "# Note\n"
            "```\n"
            "```python\n"
            "print(1)\n"
            "```"
        )
        llm = FakeLLM(mixed, "Recovered without an action.")
        notices = []
        agent = Agent(
            DummyConfig(stream=False, max_format_retries=1),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("Save a note")
        self.assertEqual("Recovered without an action.", explanation)
        self.assertFalse(has_code)
        self.assertEqual(2, len(llm.calls))
        self.assertTrue(any(
            "mixed a skill proposal" in text for text in notices
        ))
        self.assertIsNone(agent.pending_skill)

    def test_mixed_action_fails_after_retry_budget(self):
        mixed = (
            "```tool\n"
            '{"name":"__workspace","args":{"action":"summary"}}\n'
            "```\n"
            "```python\n"
            "print(1)\n"
            "```"
        )
        agent = Agent(
            DummyConfig(stream=False, max_format_retries=1),
            FakeLLM(mixed, mixed), DummyBoard(),
        )
        with self.assertRaises(AgentError) as caught:
            agent.ask("Inspect the workspace")
        self.assertIn("invalid action after retry", str(caught.exception))
        self.assertIn("mixed a tool call", str(caught.exception))
        self.assertEqual("Inspect the workspace", agent.history[0]["content"])
        self.assertIn("did not finish", agent.history[-1]["content"])

    def test_protocol_retry_budget_resets_after_a_valid_action(self):
        mixed = (
            "```tool\n"
            '{"name":"__workspace","args":{"action":"summary"}}\n'
            "```\n"
            "```python\n"
            "print(1)\n"
            "```"
        )
        valid_tool = (
            "```tool\n"
            '{"name":"__workspace","args":{"action":"summary"}}\n'
            "```"
        )
        llm = FakeLLM(mixed, valid_tool, mixed, "Recovered.")
        agent = Agent(
            DummyConfig(stream=False, max_format_retries=1),
            llm, DummyBoard(),
        )
        explanation, has_code = agent.ask("Inspect the workspace")
        self.assertEqual("Recovered.", explanation)
        self.assertFalse(has_code)
        self.assertEqual(4, len(llm.calls))

    def test_empty_code_block_gets_protocol_retry(self):
        empty = "```python\n   \n```"
        llm = FakeLLM(empty, "Recovered without code.")
        notices = []
        agent = Agent(
            DummyConfig(stream=False, max_format_retries=1),
            llm, DummyBoard(),
        )
        agent.on_notice = notices.append
        explanation, has_code = agent.ask("Print something")
        self.assertEqual("Recovered without code.", explanation)
        self.assertFalse(has_code)
        self.assertEqual(2, len(llm.calls))
        self.assertTrue(any("empty code block" in text for text in notices))

    def test_bing_search_parses_html_without_api_key(self):
        html = (
            '<html><li class="b_algo"><h2><a href="https://example.com/a">'
            'Example <strong>A</strong></a></h2><p>First &amp; snippet.</p></li>'
            '<li class="b_algo"><h2><a href="/local">Local</a></h2>'
            '<p>Second snippet.</p></li></html>'
        ).encode()
        http = FakeSearchHttp(html=html)
        agent = Agent(
            DummyConfig(
                stream=False, search_provider="bing", search_bing_rss=False,
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool(
            {"query": "ESP32 MicroPython", "count": 5}
        )
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: bing", result.output)
        self.assertIn("Example A", result.output)
        self.assertIn("First & snippet.", result.output)
        self.assertIn("https://cn.bing.com/local", result.output)
        call = http.request_calls[0]
        self.assertEqual("GET", call["method"])
        self.assertIn("ESP32+MicroPython", call["url"])
        self.assertNotIn("format=rss", call["url"])
        self.assertIn("Mozilla/5.0", call["headers"]["User-Agent"])
        self.assertEqual([], http.calls)

    def test_bing_search_prefers_keyless_rss(self):
        rss = (
            '<?xml version="1.0" encoding="utf-8"?><rss><channel>'
            '<item><title>ESP32 &amp; MicroPython</title>'
            '<link>https://example.com/esp32</link>'
            '<description>First &lt;b&gt;snippet&lt;/b&gt;. '
            'Keep a &lt; b &gt; c.</description>'
            '<pubDate>Fri, 21 Aug 2026 15:44:00 GMT</pubDate></item>'
            '</channel></rss>'
        ).encode()
        http = FakeSearchHttp(rss=rss)
        agent = Agent(
            DummyConfig(stream=False, search_provider="bing"),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "ESP32 MicroPython"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: bing", result.output)
        self.assertIn("ESP32 & MicroPython", result.output)
        self.assertIn("https://example.com/esp32", result.output)
        self.assertIn("First snippet. Keep a < b > c.", result.output)
        self.assertIn("2026", result.output)
        self.assertEqual(1, len(http.request_calls))
        self.assertIn("format=rss", http.request_calls[0]["url"])
        self.assertEqual([], http.calls)

    def test_bing_search_falls_back_from_empty_rss_to_html(self):
        html = (
            b'<li class="b_algo"><h2><a href="https://example.com">'
            b'Result</a></h2><p>Snippet.</p></li>'
        )
        http = FakeSearchHttp(html=html, rss=b"<rss><channel></channel></rss>")
        agent = Agent(
            DummyConfig(stream=False, search_provider="bing"),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "ESP32"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("parsed HTML instead", result.output)
        self.assertEqual(2, len(http.request_calls))
        self.assertIn("format=rss", http.request_calls[0]["url"])
        self.assertNotIn("format=rss", http.request_calls[1]["url"])

    def test_auto_search_uses_anthropic_native_web_search(self):
        payload = {
            "content": [
                {
                    "type": "web_search_tool_result",
                    "content": [{
                        "type": "web_search_result",
                        "title": "ESP32 documentation",
                        "url": "https://example.com/esp32",
                        "snippet": "Docs snippet.",
                        "page_age": "2026-01-01T00:00:00+00:00",
                    }],
                },
                {
                    "type": "text",
                    "text": "ESP32 is a microcontroller.",
                    "citations": [{
                        "type": "web_search_result_location",
                        "url": "https://example.com/esp32",
                        "title": "ESP32 documentation",
                        "cited_text": "Cited text.",
                    }],
                },
            ],
        }
        http = FakeSearchHttp(payload=payload)
        agent = Agent(
            DummyConfig(
                stream=False, api_provider="anthropic",
                base_url="https://api.anthropic.com",
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "ESP32"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: anthropic", result.output)
        self.assertIn("answer: ESP32 is a microcontroller.", result.output)
        self.assertIn("ESP32 documentation", result.output)
        self.assertIn("https://example.com/esp32", result.output)
        call = http.calls[0]
        self.assertEqual("https://api.anthropic.com/v1/messages", call["url"])
        self.assertEqual("web_search_20250305",
                         call["payload"]["tools"][0]["type"])
        self.assertEqual("test-api-key", call["headers"]["x-api-key"])
        self.assertEqual("2023-06-01", call["headers"]["anthropic-version"])

    def test_auto_search_uses_openai_responses_web_search(self):
        payload = {
            "status": "completed",
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "type": "search",
                        "query": "ESP32",
                        "sources": [{
                            "type": "url",
                            "title": "ESP32 docs",
                            "url": "https://example.com/esp32",
                            "snippet": "Source snippet.",
                        }],
                    },
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": "ESP32 has Wi-Fi.",
                        "annotations": [{
                            "type": "url_citation",
                            "url": "https://example.com/esp32",
                            "title": "ESP32 docs",
                        }],
                    }],
                },
            ],
        }
        http = FakeSearchHttp(payload=payload)
        agent = Agent(
            DummyConfig(
                stream=False, api_provider="openai",
                base_url="https://api.openai.com/v1",
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "ESP32"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: openai", result.output)
        self.assertIn("answer: ESP32 has Wi-Fi.", result.output)
        self.assertIn("ESP32 docs", result.output)
        call = http.calls[0]
        self.assertEqual("https://api.openai.com/v1/responses", call["url"])
        self.assertEqual("web_search", call["payload"]["tools"][0]["type"])
        self.assertEqual(
            "Bearer test-api-key", call["headers"]["Authorization"]
        )

    def test_auto_search_uses_bing_for_opencode_go(self):
        rss = (
            b'<rss><channel><item><title>OpenCode result</title>'
            b'<link>https://example.com/opencode</link>'
            b'<description>Snippet.</description></item></channel></rss>'
        )
        http = FakeSearchHttp(rss=rss)
        agent = Agent(
            DummyConfig(
                stream=False, api_provider="openai",
                base_url="https://opencode.ai/inference/go/openai/v1",
                model="deepseek-v4.1-flash",
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "OpenCode search"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: bing", result.output)
        self.assertEqual([], http.calls)
        self.assertEqual(1, len(http.request_calls))

    def test_auto_search_uses_bing_for_unknown_gateway(self):
        rss = (
            b'<rss><channel><item><title>Result</title>'
            b'<link>https://example.com</link><description>Snippet.</description>'
            b'</item></channel></rss>'
        )
        http = FakeSearchHttp(rss=rss)
        agent = Agent(
            DummyConfig(
                stream=False, base_url="https://gateway.example/v1",
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "ESP32"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: bing", result.output)
        self.assertEqual([], http.calls)
        self.assertEqual(1, len(http.request_calls))

    def test_auto_search_falls_back_to_bing_on_native_provider_error(self):
        rss = (
            b'<rss><channel><item><title>Result</title>'
            b'<link>https://example.com</link><description>Snippet.</description>'
            b'</item></channel></rss>'
        )
        http = FakeSearchHttp(
            status=400,
            payload={"error": {"message": "web_search tool is not supported"}},
            rss=rss,
        )
        agent = Agent(
            DummyConfig(
                stream=False, api_provider="anthropic",
                base_url="https://api.anthropic.com",
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "ESP32"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: bing", result.output)
        self.assertIn("Anthropic web search HTTP 400", result.output)
        self.assertEqual(1, len(http.calls))
        self.assertEqual(1, len(http.request_calls))

    def test_auto_search_uses_deepseek_anthropic_server_search(self):
        payload = {
            "content": [
                {
                    "type": "web_search_tool_result",
                    "content": [{
                        "type": "web_search_result",
                        "title": "DeepSeek native result",
                        "url": "https://example.com/deepseek",
                        "snippet": "DeepSeek snippet.",
                        "site_name": "Example",
                        "page_age": "2026-01-01T00:00:00+00:00",
                    }],
                },
                {
                    "type": "text",
                    "text": "DeepSeek searched the web.",
                    "citations": [{
                        "type": "web_search_result_location",
                        "url": "https://example.com/deepseek",
                        "title": "DeepSeek native result",
                        "cited_text": "DeepSeek cited text.",
                    }],
                },
            ],
        }
        http = FakeSearchHttp(payload=payload)
        agent = Agent(
            DummyConfig(
                stream=False, api_provider="anthropic",
                base_url="https://api.deepseek.com/anthropic",
                model="deepseek-flash",
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "神舟二十四号"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: deepseek", result.output)
        self.assertIn("answer: DeepSeek searched the web.", result.output)
        self.assertIn("DeepSeek native result", result.output)
        call = http.calls[0]
        self.assertEqual(
            "https://api.deepseek.com/anthropic/v1/messages", call["url"]
        )
        self.assertEqual(
            "deepseek_web_search", call["payload"]["tools"][0]["type"]
        )
        self.assertEqual(
            "deepseek_web_search", call["payload"]["tools"][0]["name"]
        )
        self.assertEqual(
            {"type": "disabled"}, call["payload"]["thinking"]
        )
        self.assertEqual("test-api-key", call["headers"]["x-api-key"])

    def test_auto_search_uses_deepseek_chat_server_search(self):
        payload = {
            "choices": [{
                "message": {
                    "content": "DeepSeek chat searched the web.",
                    "server_tool_calls": [{
                        "calls": [{
                            "sources": [{
                                "title": "Chat native result",
                                "url": "https://example.com/chat",
                                "snippet": "Chat snippet.",
                                "site_name": "Example",
                                "published_at": "2026-01-01",
                            }],
                        }],
                    }],
                    "annotations": [{
                        "type": "url_citation",
                        "url_citation": {
                            "url": "https://example.com/chat",
                            "title": "Chat native result",
                        },
                    }],
                },
            }],
        }
        http = FakeSearchHttp(payload=payload)
        agent = Agent(
            DummyConfig(
                stream=False, api_provider="openai",
                base_url="https://api.deepseek.com/",
                model="deepseek-flash",
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "ESP32"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: deepseek", result.output)
        self.assertIn("answer: DeepSeek chat searched the web.", result.output)
        self.assertIn("Chat native result", result.output)
        call = http.calls[0]
        self.assertEqual(
            "https://api.deepseek.com/chat/completions", call["url"]
        )
        self.assertEqual(
            "deepseek_web_search", call["payload"]["tools"][0]["type"]
        )
        self.assertEqual(
            {"type": "disabled"}, call["payload"]["thinking"]
        )

    def test_auto_search_falls_back_to_bing_on_deepseek_server_error(self):
        rss = (
            b'<rss><channel><item><title>Fallback result</title>'
            b'<link>https://example.com/fallback</link>'
            b'<description>Fallback snippet.</description></item></channel></rss>'
        )
        http = FakeSearchHttp(
            status=400,
            payload={"error": {"message": "tool not supported"}},
            rss=rss,
        )
        agent = Agent(
            DummyConfig(
                stream=False, api_provider="anthropic",
                base_url="https://api.deepseek.com/anthropic",
                model="deepseek-flash",
            ),
            FakeLLM("x"), DummyBoard(), http=http,
        )
        result = agent._run_search_tool({"query": "ESP32"})
        self.assertTrue(result.ok, result.error)
        self.assertIn("provider: bing", result.output)
        self.assertIn("DeepSeek web search HTTP 400", result.output)
        self.assertEqual(1, len(http.calls))
        self.assertEqual(1, len(http.request_calls))

    def test_search_status_auto_follows_provider(self):
        deepseek = Agent(
            DummyConfig(
                stream=False, api_provider="anthropic",
                base_url="https://api.deepseek.com/anthropic",
                model="deepseek-flash",
            ),
            FakeLLM("x"), DummyBoard(),
        )
        status = deepseek.search_status()
        self.assertEqual("auto", status["configured"])
        self.assertEqual("deepseek", status["effective"])
        self.assertTrue(status["fallback"])

        anthropic = Agent(
            DummyConfig(
                stream=False, api_provider="anthropic",
                base_url="https://api.anthropic.com",
            ),
            FakeLLM("x"), DummyBoard(),
        )
        status = anthropic.search_status()
        self.assertEqual("anthropic", status["effective"])
        self.assertTrue(status["fallback"])

        bing = Agent(
            DummyConfig(stream=False, search_provider="bing"),
            FakeLLM("x"), DummyBoard(),
        )
        status = bing.search_status()
        self.assertEqual("bing", status["effective"])
        self.assertFalse(status["fallback"])

    def test_builtin_search_tool_call_returns_final_answer(self):
        tool_response = (
            "```tool\n"
            '{"name":"__search","args":{"query":"ESP32","count":1}}\n'
            "```"
        )
        rss = (
            b'<rss><channel><item><title>Search result</title>'
            b'<link>https://example.com/result</link>'
            b'<description>Result summary.</description></item></channel></rss>'
        )
        http = FakeSearchHttp(rss=rss)
        llm = FakeLLM(tool_response, "Search complete.")
        agent = Agent(
            DummyConfig(stream=False, search_provider="bing"),
            llm, DummyBoard(), http=http
        )
        explanation, has_code = agent.ask("Search ESP32")
        self.assertEqual("Search complete.", explanation)
        self.assertFalse(has_code)
        self.assertIn("Search result", agent.history[-2]["content"])
        self.assertEqual([], http.calls)
        self.assertEqual(1, len(http.request_calls))

    def test_tool_call_limit_is_bounded(self):
        tool_response = (
            "```tool\n"
            '{"name":"__skill","args":{"action":"list"}}\n'
            "```"
        )
        llm = FakeLLM(tool_response, tool_response)
        agent = Agent(
            DummyConfig(stream=False, max_tool_calls=1), llm, DummyBoard(),
            skills=FakeSkills(),
        )
        with self.assertRaises(AgentError):
            agent.ask("List skills repeatedly")
        self.assertEqual(2, len(llm.calls))
        self.assertEqual("List skills repeatedly", agent.history[0]["content"])
        self.assertIn("did not finish", agent.history[-1]["content"])

    def test_skill_proposal_requires_user_commit(self):
        proposal = (
            "```skill\n"
            '{"name":"board-notes","description":"Board inspection notes"}\n'
            "---\n"
            "# Board notes\n"
            "Use device.info().\n"
            "```"
        )
        skills = FakeSkills()
        agent = Agent(DummyConfig(stream=False), FakeLLM(proposal), DummyBoard(), skills=skills)
        explanation, has_code = agent.ask("Create a skill")
        self.assertFalse(has_code)
        self.assertIsNotNone(agent.pending_skill)
        self.assertEqual([], skills.saved)
        with self.assertRaises(AgentError):
            agent.ask("Another task while a pending skill exists")
        metadata = agent.commit_pending_skill()
        self.assertEqual("board-notes", metadata["name"])
        self.assertIsNone(agent.pending_skill)
        self.assertEqual(1, len(skills.saved))
        self.assertIn("saved skill", agent.history[-1]["content"])

    def test_builtin_memory_read_and_append(self):
        with tempfile.TemporaryDirectory() as root:
            config = DummyConfig(stream=False)
            memory = MemoryStore(root, config)
            read_response = (
                "```tool\n"
                '{"name":"__memory","args":{"action":"read"}}\n'
                "```"
            )
            append_response = (
                "```tool\n"
                '{"name":"__memory","args":{"action":"append","content":"Use 115200 baud."}}\n'
                "```"
            )
            llm = FakeLLM(read_response, append_response, "Memory updated.")
            agent = Agent(
                config, llm, DummyBoard(), memory=memory
            )
            explanation, has_code = agent.ask("Remember the baud rate")
            self.assertEqual("Memory updated.", explanation)
            self.assertFalse(has_code)
            self.assertIn("Use 115200 baud.", memory.load())

    def test_builtin_memory_delete_and_replace(self):
        with tempfile.TemporaryDirectory() as root:
            config = DummyConfig(stream=False)
            memory = MemoryStore(root, config)
            memory.append("Board serial uses 115200 baud.")
            delete_response = (
                "```tool\n"
                '{"name":"__memory","args":{"action":"delete","match":"115200"}}\n'
                "```"
            )
            replace_response = (
                "```tool\n"
                '{"name":"__memory","args":{"action":"replace",'
                '"match":"Display","content":"Display is an SSD1306."}}\n'
                "```"
            )
            llm = FakeLLM(delete_response, "Deleted.", replace_response, "Replaced.")
            agent = Agent(config, llm, DummyBoard(), memory=memory)
            explanation, has_code = agent.ask("Remove the serial note")
            self.assertEqual("Deleted.", explanation)
            self.assertFalse(has_code)
            self.assertNotIn("115200", memory.load())
            memory.append("Display uses address 0x3C.")
            explanation, has_code = agent.ask("Update the display note")
            self.assertEqual("Replaced.", explanation)
            self.assertFalse(has_code)
            self.assertIn("SSD1306", memory.load())
            self.assertNotIn("0x3C", memory.load())

    def test_discard_records_local_event_for_follow_up(self):
        agent = Agent(DummyConfig(), FakeLLM("```python\nprint(1)\n```"), DummyBoard())
        agent.ask("first")
        agent.discard()
        self.assertIsNone(agent.pending_source)
        self.assertIn("discarded the pending code", agent.history[-1]["content"])
        agent.discard()
        self.assertEqual(
            1,
            sum("discarded the pending code" in message["content"]
                for message in agent.history),
        )

    def test_history_is_bounded(self):
        config = DummyConfig(history_messages=2, history_chars=20)
        agent = Agent(config, FakeLLM(), DummyBoard())
        agent.history = [
            {"role": "user", "content": "a" * 10},
            {"role": "assistant", "content": "b" * 10},
            {"role": "user", "content": "c" * 10},
        ]
        agent._trim_history()
        self.assertEqual(2, len(agent.history))
        self.assertLessEqual(agent._history_chars(), 20)

    def test_invalid_response_keeps_request_but_not_response(self):
        agent = Agent(
            DummyConfig(max_format_retries=0),
            FakeLLM("```python\nprint(1)"), DummyBoard(),
        )
        with self.assertRaises(AgentError):
            agent.ask("invalid")
        self.assertEqual("invalid", agent.history[0]["content"])
        self.assertEqual("assistant", agent.history[-1]["role"])
        self.assertIn("did not finish", agent.history[-1]["content"])
        self.assertNotIn("print(1)", agent.history[-1]["content"])
        self.assertIsNone(agent.pending_source)
        self.assertEqual(0, agent.turns)

    def test_interrupted_request_keeps_user_message(self):
        agent = Agent(DummyConfig(), RaisingLLM(KeyboardInterrupt()), DummyBoard())
        with self.assertRaises(KeyboardInterrupt):
            agent.ask("interrupt")
        self.assertEqual("interrupt", agent.history[0]["content"])
        self.assertEqual("assistant", agent.history[-1]["role"])
        self.assertIn("did not finish", agent.history[-1]["content"])

    def test_oversized_input_is_rejected(self):
        agent = Agent(DummyConfig(input_limit=5), FakeLLM(), DummyBoard())
        with self.assertRaises(AgentError):
            agent.ask("123456")
        self.assertEqual([], agent.history)

    def test_current_task_source_and_observation_are_retained(self):
        config = DummyConfig(history_messages=3, history_chars=500)
        agent = Agent(config, FakeLLM("```python\nprint(42)\n```"), DummyBoard())
        agent.ask("remember this task")
        agent.execute_pending(Executor(DummyBoard()))
        contents = [message["content"] for message in agent.history]
        self.assertIn("remember this task", contents)
        self.assertTrue(any("print(42)" in content for content in contents))
        self.assertIn("Local ESP32 execution result", contents[-1])

    def test_feedback_keeps_source_when_it_exceeds_history_budget(self):
        source = "print('" + ("x" * 100) + "')"
        llm = FakeLLM("```python\n%s\n```" % source, "Done")
        config = DummyConfig(history_chars=30, code_limit=500)
        agent = Agent(config, llm, DummyBoard())
        agent.ask("long source")
        agent.execute_pending(Executor(DummyBoard()))
        agent.continue_feedback()
        feedback_context = "\n".join(message["content"] for message in llm.calls[1])
        self.assertIn("print('", feedback_context)
        self.assertIn("Local ESP32 execution result", feedback_context)

    def test_recovered_code_disables_automatic_feedback(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        agent.restore_pending("print('recovered')")
        result = agent.execute_pending(Executor(DummyBoard()))
        self.assertTrue(result.ok)
        self.assertFalse(agent.can_continue())
        with self.assertRaises(AgentError):
            agent.continue_feedback()
        self.assertIn("original task", agent.history[-1]["content"])

    def test_response_can_be_committed_without_another_request(self):
        agent = Agent(DummyConfig(), FakeLLM(), DummyBoard())
        extra = [{"role": "user", "content": "worker task"}]
        explanation, has_code = agent.accept_response("```python\nprint(9)\n```", extra)
        self.assertTrue(has_code)
        self.assertEqual("worker task", agent.history[0]["content"])
        self.assertIn("print(9)", agent.history[1]["content"])
        self.assertEqual("print(9)", agent.pending_source)

    def test_second_task_uses_new_context(self):
        llm = FakeLLM("```python\nprint(1)\n```", "No code needed")
        agent = Agent(DummyConfig(), llm, DummyBoard())
        agent.ask("first task")
        agent.execute_pending(Executor(DummyBoard()))
        agent.ask("second task")
        self.assertEqual("second", agent.last_task[:6])


if __name__ == "__main__":
    unittest.main()
