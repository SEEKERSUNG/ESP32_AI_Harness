import json
import time

from .config import THINKING_LEVELS, normalize_thinking_level
from .http_client import HTTPCancelled, HttpClient


RETRYABLE_STATUS = (408, 429, 500, 502, 503, 504, 529)

_NON_RETRYABLE_MARKERS = (
    "certificate",
    "cert verify",
    "ca file",
    "not valid",
    "expired",
    "hostname mismatch",
    "url must start",
    "only ipv4",
    "url host",
    "invalid url port",
    "compressed",
    "not supported",
    "exceeds configured limit",
    "line exceeds limit",
    "too large",
    "invalid content-length",
    "invalid chunk size",
    "invalid http status",
    "truncated at max_tokens",
    "empty content",
    "no text content",
    "invalid chat completion response",
    "invalid sse json",
)

_RETRYABLE_MARKERS = (
    "timeout",
    "timed out",
    "etimedout",
    "connection",
    "connect",
    "network",
    "reset",
    "refused",
    "unreachable",
    "unreach",
    "broken pipe",
    "epipe",
    "closed",
    "stream ended",
    "ended before content-length",
    "ended inside chunked",
    "invalid chunk terminator",
    "eof",
    "sse",
    "recv",
    "send",
    "resolve",
    "dns",
    "getaddrinfo",
    "name or service",
)

_STREAM_ERROR_MARKERS = (
    "overload",
    "capacity",
    "rate limit",
    "rate_limit",
    "timeout",
    "temporarily",
    "unavailable",
    "server error",
    "internal error",
    "busy",
)


class LLMError(Exception):
    status = None
    retryable = False
    retry_after = None


class LLMStreamCancelled(HTTPCancelled):
    """Raised by a streaming callback to abort the current model request."""


ANTHROPIC_VERSION = "2023-06-01"
API_PROVIDERS = ("openai", "anthropic")


def llm_error(message, status=None, retryable=False, retry_after=None):
    """Attach retry metadata without overriding MicroPython's Exception."""
    error = LLMError(message)
    error.status = status
    error.retryable = retryable
    error.retry_after = retry_after
    return error


def _retry_after_seconds(headers):
    if not headers:
        return None
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return seconds


def _network_retryable(exc):
    message = str(exc).lower()
    for marker in _NON_RETRYABLE_MARKERS:
        if marker in message:
            return False
    for marker in _RETRYABLE_MARKERS:
        if marker in message:
            return True
    return isinstance(exc, OSError)


def _stream_error_retryable(error):
    message = str(error).lower()
    for marker in _STREAM_ERROR_MARKERS:
        if marker in message:
            return True
    return False


def _hex(data):
    parts = []
    for value in data:
        parts.append("%02x" % value)
    return "".join(parts)


def _fallback_session_id():
    parts = []
    try:
        import machine
        parts.append(_hex(machine.unique_id()))
    except Exception:
        pass
    try:
        if hasattr(time, "ticks_ms"):
            parts.append("%08x" % (time.ticks_ms() & 0xFFFFFFFF))
        else:
            parts.append("%08x" % (int(time.monotonic() * 1000) & 0xFFFFFFFF))
    except Exception:
        pass
    return "ses_" + ("".join(parts) or "esp32_harness")


def new_session_id():
    try:
        import os
        return "ses_" + _hex(os.urandom(16))
    except Exception:
        return _fallback_session_id()


def normalize_api_provider(value, default="openai"):
    value = str(value or "").strip().lower()
    if value in ("anthropic", "claude", "messages"):
        return "anthropic"
    if value in ("openai", "openai-compatible", "compatible", "chat", "chat-completions"):
        return "openai"
    return default


def default_base_url(provider):
    if normalize_api_provider(provider) == "anthropic":
        return "https://api.anthropic.com"
    return "https://api.openai.com/v1"


def create_llm(config, http_client=None):
    if normalize_api_provider(config.get("api_provider", "openai")) == "anthropic":
        # Deferred import keeps the OpenAI path free of Anthropic-only code
        # and avoids a circular import at module load time.
        from .llm_anthropic import AnthropicClient
        return AnthropicClient(config, http_client)
    return OpenAIClient(config, http_client)


class OpenAIClient:
    supports_native_tools = True

    def __init__(self, config, http_client=None):
        self.config = config
        self.http = http_client or HttpClient()
        self.session_id = new_session_id()
        self._reset_meta()

    def new_session(self):
        self.session_id = new_session_id()
        return self.session_id

    def _reset_meta(self):
        self.last_reasoning = ""
        self.last_reasoning_chars = 0
        self.last_finish_reason = None
        self.last_elapsed_ms = 0
        self.last_content_chars = 0
        self.last_response_chars = 0
        self.last_thinking_level = "disabled"
        self.last_thinking_configured = "disabled"
        self.last_thinking_overridden = False
        self.last_timeout_ms = 0
        self.last_header_timeout_ms = 0
        self.last_idle_timeout_ms = 0
        self.last_event_idle_timeout_ms = 0
        self.last_headers_ms = 0
        self.last_first_token_ms = 0
        self.last_token_count = 0
        self.last_max_gap_ms = 0
        self.last_tail_gap_ms = 0
        self.last_streamed = False
        self.last_tool_calls = []

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False, timeout=None, tools=None):
        self._reset_meta()
        payload, endpoint, headers, timeout = self._build_request(
            messages, max_tokens, False, thinking_level, timeout, tools
        )
        started = self._ticks()
        try:
            try:
                response = self.http.post(
                    endpoint,
                    json.dumps(payload),
                    headers,
                    timeout=timeout,
                    max_response=self.config.get("response_limit"),
                    tls_verify=self.config.get("tls_verify"),
                    ca_file=self.config.get("ca_file"),
                )
            except LLMStreamCancelled:
                raise
            except Exception as exc:
                raise llm_error(str(exc), retryable=_network_retryable(exc))
            try:
                text = response.body.decode()
            except Exception as exc:
                raise LLMError("Invalid model response encoding: %s" % exc)
            self.last_response_chars = len(text)
            if response.status < 200 or response.status >= 300:
                raise llm_error(
                    "Model API HTTP %s: %s" % (response.status, text[:600]),
                    status=response.status,
                    retryable=response.status in RETRYABLE_STATUS,
                    retry_after=_retry_after_seconds(response.headers),
                )
            try:
                data = json.loads(text)
                choice = data["choices"][0]
                message = choice["message"]
                content = message.get("content")
                reasoning = message.get("reasoning_content")
                self.last_finish_reason = choice.get("finish_reason")
                self.last_tool_calls = self._normalize_tool_calls(
                    message.get("tool_calls"))
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LLMError("Invalid chat completion response: %s" % exc)

            if reasoning:
                try:
                    self.last_reasoning = self._content_text(reasoning)
                except LLMError:
                    self.last_reasoning = ""
            self.last_reasoning_chars = len(self.last_reasoning)
            try:
                result = self._content_text(content)
            except LLMError:
                result = ""
            self.last_content_chars = len(result)
            self._validate_result(result, allow_truncated, self.last_tool_calls)
            return result
        finally:
            self.last_elapsed_ms = self._elapsed(started)

    def complete_stream(self, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, thinking_level=None,
                        allow_truncated=False, timeout=None, tools=None):
        self._reset_meta()
        payload, endpoint, headers, timeout = self._build_request(
            messages, max_tokens, True, thinking_level, timeout, tools
        )
        try:
            idle_timeout = max(1, int(self.config.get("stream_idle_timeout", 300)))
        except (TypeError, ValueError):
            idle_timeout = 300
        try:
            event_idle_timeout = int(self.config.get("stream_event_idle_timeout", 300))
        except (TypeError, ValueError):
            event_idle_timeout = 300
        try:
            first_token_timeout = int(
                self.config.get("stream_first_token_timeout", 600)
            )
        except (TypeError, ValueError):
            first_token_timeout = 600
        self.last_idle_timeout_ms = idle_timeout * 1000
        self.last_event_idle_timeout_ms = max(0, event_idle_timeout) * 1000
        started = self._ticks()
        reader = None
        content_parts = []
        reasoning_chars = 0
        finish_reason = None
        done = False
        first_token_at = None
        last_token_at = None
        token_events = 0
        max_gap_ms = 0
        last_event = started
        tool_states = {}
        self.last_streamed = True

        def mark_partial(error):
            # A timeout or connection loss after real answer text is a partial
            # response, not a safe retry: the UI has already shown this text.
            # Reasoning-only stalls may still be retried, matching Claude Code.
            if content_parts:
                error.partial = True
                error.retryable = False
                error.content_chars = self.last_content_chars
                error.reasoning_chars = reasoning_chars
            elif reasoning_chars:
                error.reasoning_only = True
            return error

        try:
            try:
                reader = self.http.post_stream(
                    endpoint,
                    json.dumps(payload),
                    headers,
                    timeout=timeout,
                    max_response=self.config.get("stream_response_limit", 1048576),
                    tls_verify=self.config.get("tls_verify"),
                    ca_file=self.config.get("ca_file"),
                    idle_timeout=idle_timeout,
                    first_byte_timeout=first_token_timeout,
                )
                headers_at = self._ticks()
                self.last_headers_ms = self._elapsed(started)
                last_event = headers_at
                self.last_response_chars = reader.bytes_read
                if reader.status < 200 or reader.status >= 300:
                    body = reader.read_all(600).decode()
                    raise llm_error(
                        "Model API HTTP %s: %s" % (reader.status, body),
                        status=reader.status,
                        retryable=reader.status in RETRYABLE_STATUS,
                        retry_after=_retry_after_seconds(reader.headers),
                    )

                data_lines = []
                while True:
                    if event_idle_timeout > 0:
                        buffered = getattr(reader, "has_buffered_line", None)
                        event_limit = event_idle_timeout
                        if first_token_at is None and first_token_timeout > event_limit:
                            # Prefill may legitimately take longer than the gap
                            # allowed between later tokens.
                            event_limit = first_token_timeout
                        if (buffered is None or not buffered()):
                            if self._elapsed(last_event) > event_limit * 1000:
                                raise llm_error(
                                    "SSE event idle timeout", retryable=True
                                )
                    line = reader.readline()
                    if not line:
                        break
                    line = line.rstrip(b"\r\n")
                    if not line:
                        if not data_lines:
                            continue
                        data = b"\n".join(data_lines)
                        data_lines = []
                        if data.strip() == b"[DONE]":
                            done = True
                            break
                        try:
                            event = json.loads(data)
                        except ValueError:
                            raise LLMError("Invalid SSE JSON event")
                        if "error" in event:
                            raise llm_error(
                                "Model stream error: %s" % event["error"],
                                retryable=_stream_error_retryable(event["error"]),
                            )
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content")
                        content = delta.get("content")
                        tool_delta = delta.get("tool_calls")
                        if tool_delta:
                            self._accumulate_tool_calls(tool_states, tool_delta)
                            last_event = self._ticks()
                        if reasoning or content:
                            now = self._ticks()
                            if first_token_at is None:
                                first_token_at = now
                                self.last_first_token_ms = self._elapsed(started)
                            elif last_token_at is not None:
                                gap = self._elapsed(last_token_at)
                                if gap > max_gap_ms:
                                    max_gap_ms = gap
                            last_token_at = now
                            last_event = now
                            token_events += 1
                        if reasoning:
                            reasoning_chars += len(reasoning)
                            self.last_reasoning_chars = reasoning_chars
                            if on_reasoning is not None:
                                try:
                                    on_reasoning(reasoning)
                                except LLMStreamCancelled:
                                    raise
                                except Exception:
                                    pass
                        if content:
                            content_parts.append(content)
                            self.last_content_chars += len(content)
                            if on_content is not None:
                                try:
                                    on_content(content)
                                except LLMStreamCancelled:
                                    raise
                                except Exception:
                                    pass
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                            last_event = self._ticks()
                            break
                    elif line.startswith(b"data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line.startswith(b":") or line.startswith((b"event:", b"id:", b"retry:")):
                        continue

                self.last_reasoning_chars = reasoning_chars
                self.last_finish_reason = finish_reason
                self.last_response_chars = reader.bytes_read
                self.last_token_count = token_events
                self.last_max_gap_ms = max_gap_ms
                self.last_tool_calls = self._finalize_tool_calls(tool_states)
                result = "".join(content_parts)
                self.last_content_chars = len(result)
                if not done and finish_reason is None:
                    raise llm_error(
                        "SSE stream ended before [DONE]", retryable=True
                    )
                if len(result) > self.config.get("response_limit"):
                    raise LLMError("Streamed content exceeds configured limit")
                self._validate_result(result, allow_truncated, self.last_tool_calls)
                return result
            except LLMStreamCancelled:
                raise
            except LLMError as exc:
                if getattr(exc, "retryable", False):
                    mark_partial(exc)
                raise
            except Exception as exc:
                raise mark_partial(
                    llm_error(str(exc), retryable=_network_retryable(exc))
                )
        finally:
            self.last_elapsed_ms = self._elapsed(started)
            if first_token_at is not None:
                self.last_token_count = token_events
                self.last_max_gap_ms = max_gap_ms
            if last_token_at is not None:
                self.last_tail_gap_ms = self._elapsed(last_token_at)
            if reader is not None:
                self.last_response_chars = reader.bytes_read
                reader.close()

    def _build_request(self, messages, max_tokens, stream, thinking_level=None,
                       timeout_override=None, tools=None):
        self.config.validate_model()
        thinking, thinking_level, timeout = self._thinking_settings(thinking_level)
        if stream:
            # Headers arrive before the first token; this is a separate budget
            # from the body idle watchdog and should not grow with thinking.
            try:
                header_timeout = int(self.config.get("stream_header_timeout", 300))
            except (TypeError, ValueError):
                header_timeout = 300
            if header_timeout > 0:
                timeout = header_timeout
        if timeout_override is not None:
            timeout = max(timeout, int(timeout_override))
        self.last_thinking_level = thinking_level
        self.last_thinking_configured = normalize_thinking_level(
            self.config.get("thinking_level", "disabled"), "disabled")
        self.last_thinking_overridden = (
            self.last_thinking_configured != thinking_level)
        self.last_header_timeout_ms = timeout * 1000
        self.last_timeout_ms = timeout * 1000
        payload = {
            "model": self.config.get("model"),
            "messages": self._convert_messages(messages),
            "stream": stream,
            "max_tokens": max_tokens or self.config.get("max_tokens"),
            "temperature": self.config.get("temperature"),
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters")
                        or {"type": "object", "properties": {}},
                    },
                }
                for tool in tools
            ]
        payload.update(thinking)
        endpoint = self.config.get("base_url").rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": "Bearer " + self.config.secret("api_key"),
            "x-opencode-session": self.session_id,
        }
        return payload, endpoint, headers, timeout

    @staticmethod
    def _convert_messages(messages):
        """Translate provider-neutral tool messages to chat-completions."""
        converted = []
        for message in messages:
            role = message.get("role")
            if role == "tool" and message.get("tool_call_id"):
                converted.append({
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "content": message.get("content", ""),
                })
                continue
            calls = message.get("tool_calls")
            if role == "assistant" and calls:
                normalized = []
                for index, call in enumerate(calls):
                    normalized.append({
                        "id": call.get("id") or "call_%s" % (index + 1),
                        "type": "function",
                        "function": {
                            "name": call.get("name", ""),
                            "arguments": json.dumps(call.get("args") or {}),
                        },
                    })
                converted.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": normalized,
                })
                continue
            if role in ("system", "user", "assistant"):
                converted.append({
                    "role": role,
                    "content": message.get("content", ""),
                })
        return converted

    @staticmethod
    def _normalize_tool_calls(raw):
        calls = []
        if not isinstance(raw, list):
            return calls
        for index, item in enumerate(raw[:16]):
            if not isinstance(item, dict):
                continue
            function = item.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = function.get("arguments")
            error = ""
            if isinstance(arguments, dict):
                args = arguments
            elif isinstance(arguments, str):
                try:
                    args = json.loads(arguments or "{}")
                except ValueError:
                    args = {}
                    error = "Invalid JSON tool arguments"
            else:
                args = {}
            if not isinstance(args, dict):
                args = {}
                error = "Tool arguments must be a JSON object"
            calls.append({
                "id": str(item.get("id") or "call_%s" % (index + 1)),
                "name": name,
                "args": args,
                "error": error,
            })
        return calls

    @staticmethod
    def _accumulate_tool_calls(states, deltas):
        if not isinstance(deltas, list):
            return
        for item in deltas:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            if not isinstance(index, int):
                index = len(states)
            state = states.get(index)
            if state is None:
                state = {"id": "", "name": "", "arguments": ""}
                states[index] = state
            if item.get("id"):
                state["id"] = str(item["id"])
            function = item.get("function") or {}
            if not isinstance(function, dict):
                continue
            if function.get("name"):
                state["name"] += str(function["name"])
            if function.get("arguments"):
                state["arguments"] += str(function["arguments"])

    @staticmethod
    def _finalize_tool_calls(states):
        calls = []
        for index in sorted(states):
            state = states[index]
            name = state.get("name", "")
            if not name:
                continue
            raw = state.get("arguments", "")
            error = ""
            if raw:
                try:
                    args = json.loads(raw)
                except ValueError:
                    args = {}
                    error = "Invalid JSON tool arguments"
            else:
                args = {}
            if not isinstance(args, dict):
                args = {}
                error = "Tool arguments must be a JSON object"
            calls.append({
                "id": state.get("id") or "call_%s" % (index + 1),
                "name": name,
                "args": args,
                "error": error,
            })
        return calls

    def _validate_result(self, result, allow_truncated=False, tool_calls=None):
        if self.last_finish_reason == "length" and not allow_truncated:
            raise LLMError(
                "Model output was truncated at max_tokens=%s "
                "(reasoning=%s chars, content=%s chars); increase max_tokens "
                "or lower thinking"
                % (
                    self.config.get("max_tokens"),
                    self.last_reasoning_chars,
                    self.last_content_chars,
                )
            )
        if not result.strip() and not tool_calls:
            raise LLMError(
                "Model returned empty content (finish_reason=%s); increase max_tokens"
                % self.last_finish_reason
            )

    def _thinking_settings(self, override=None):
        model = str(self.config.get("model", "")).lower()
        timeout = self.config.get("request_timeout")
        level = normalize_thinking_level(
            self.config.get("thinking_level", "disabled") if override is None else override,
            "disabled",
        )
        if not model.startswith("deepseek"):
            # Non-DeepSeek gateways may still perform long reasoning even
            # though they do not accept DeepSeek's thinking parameters. The
            # timeout promise applies to every non-disabled thinking level,
            # not only models whose name starts with "deepseek".
            if level != "disabled":
                minimum_timeout = 900 if level == "max" else 600
                timeout = max(timeout, minimum_timeout)
            return {}, level, timeout

        if level == "disabled":
            return {"thinking": {"type": "disabled"}}, level, timeout
        thinking = {
            "thinking": {"type": "enabled"},
            "reasoning_effort": level,
        }
        minimum_timeout = 900 if level == "max" else 600
        return thinking, level, max(timeout, minimum_timeout)

    @staticmethod
    def _content_text(content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "".join(parts)
        raise LLMError("Model response contains no text content")

    @staticmethod
    def _ticks():
        if hasattr(time, "ticks_ms"):
            return time.ticks_ms()
        return int(time.monotonic() * 1000)

    @staticmethod
    def _elapsed(started):
        if hasattr(time, "ticks_diff"):
            return time.ticks_diff(time.ticks_ms(), started)
        return int(time.monotonic() * 1000) - started
