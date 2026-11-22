"""Anthropic Messages API client for MicroPython.

The Messages API differs from OpenAI chat completions in several ways:
the system prompt is a top-level field, max_tokens is required, thinking
uses adaptive mode plus output_config.effort on current models, and SSE
events are typed content-block deltas instead of choices/deltas.
"""

import json
import time

from .http_client import HttpClient
from .llm import (
    ANTHROPIC_VERSION,
    LLMError,
    LLMStreamCancelled,
    RETRYABLE_STATUS,
    _network_retryable,
    _retry_after_seconds,
    llm_error,
    new_session_id,
    normalize_thinking_level,
)


_ANTHROPIC_RETRYABLE_ERRORS = (
    "overloaded_error",
    "rate_limit_error",
    "api_error",
    "timeout_error",
    "server_error",
)
_ANTHROPIC_FATAL_ERRORS = (
    "authentication_error",
    "permission_error",
    "not_found_error",
    "invalid_request_error",
    "request_too_large",
)


class AnthropicClient:
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
                raise self._http_error(
                    response.status, text, response.headers
                )
            try:
                data = json.loads(text)
            except (ValueError, TypeError) as exc:
                raise LLMError("Invalid Anthropic response: %s" % exc)
            if not isinstance(data, dict):
                raise LLMError("Invalid Anthropic response: expected an object")
            if data.get("type") == "error":
                raise self._api_error(data)
            stop_reason = data.get("stop_reason")
            self.last_finish_reason = self._stop_reason(stop_reason)
            reasoning, result, tool_calls = self._content_blocks(data.get("content"))
            self.last_tool_calls = tool_calls
            self.last_reasoning = reasoning
            self.last_reasoning_chars = len(reasoning)
            self.last_content_chars = len(result)
            if stop_reason == "refusal":
                details = data.get("stop_details") or {}
                category = details.get("category") if isinstance(details, dict) else ""
                raise LLMError(
                    "Model refused the request%s"
                    % ((" (%s)" % category) if category else "")
                )
            self._validate_result(result, allow_truncated, tool_calls)
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
                    raise self._http_error(
                        reader.status, body, reader.headers
                    )

                data_lines = []
                while True:
                    if event_idle_timeout > 0:
                        buffered = getattr(reader, "has_buffered_line", None)
                        event_limit = event_idle_timeout
                        if first_token_at is None and first_token_timeout > event_limit:
                            # Before the first token, allow the longer prefill
                            # budget instead of the inter-token gap.
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
                        if not isinstance(event, dict):
                            continue
                        etype = event.get("type")
                        if etype == "error":
                            raise self._api_error(event)
                        if etype == "content_block_start":
                            block = event.get("content_block") or {}
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "tool_use":
                                index = event.get("index")
                                if not isinstance(index, int):
                                    index = len(tool_states)
                                tool_states[index] = {
                                    "id": block.get("id") or "",
                                    "name": block.get("name") or "",
                                    "input": block.get("input")
                                    if isinstance(block.get("input"), dict) else {},
                                    "json": "",
                                }
                                last_event = self._ticks()
                                continue
                            text = block.get("text")
                            thinking = block.get("thinking")
                            if isinstance(thinking, str) and thinking:
                                reasoning_chars += len(thinking)
                                self.last_reasoning_chars = reasoning_chars
                                self._emit(on_reasoning, thinking)
                            if isinstance(text, str) and text:
                                content_parts.append(text)
                                self.last_content_chars += len(text)
                                self._emit(on_content, text)
                                now = self._ticks()
                                if first_token_at is None:
                                    first_token_at = now
                                    self.last_first_token_ms = self._elapsed(started)
                                last_token_at = now
                                last_event = now
                                token_events += 1
                        elif etype == "content_block_delta":
                            delta = event.get("delta") or {}
                            if not isinstance(delta, dict):
                                continue
                            dtype = delta.get("type")
                            thinking = None
                            text = None
                            if dtype == "thinking_delta":
                                thinking = delta.get("thinking")
                            elif dtype == "text_delta":
                                text = delta.get("text")
                            elif dtype == "input_json_delta":
                                index = event.get("index")
                                state = tool_states.get(index)
                                if state is not None:
                                    state["json"] += str(
                                        delta.get("partial_json") or "")
                                    last_event = self._ticks()
                            if isinstance(thinking, str) and thinking:
                                reasoning_chars += len(thinking)
                                self.last_reasoning_chars = reasoning_chars
                                self._emit(on_reasoning, thinking)
                            if isinstance(text, str) and text:
                                content_parts.append(text)
                                self.last_content_chars += len(text)
                                self._emit(on_content, text)
                            if thinking or text:
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
                        elif etype == "message_delta":
                            delta = event.get("delta") or {}
                            if isinstance(delta, dict) and delta.get("stop_reason"):
                                finish_reason = delta["stop_reason"]
                                last_event = self._ticks()
                        elif etype == "message_stop":
                            done = True
                            break
                        elif etype == "message_start":
                            last_event = self._ticks()
                        elif etype == "ping":
                            pass
                    elif line.startswith(b"data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line.startswith(b":") or line.startswith(
                            (b"event:", b"id:", b"retry:")):
                        continue

                self.last_reasoning_chars = reasoning_chars
                self.last_finish_reason = self._stop_reason(finish_reason)
                self.last_response_chars = reader.bytes_read
                self.last_token_count = token_events
                self.last_max_gap_ms = max_gap_ms
                self.last_tool_calls = self._finalize_tool_calls(tool_states)
                result = "".join(content_parts)
                self.last_content_chars = len(result)
                if not done and finish_reason is None:
                    raise llm_error(
                        "SSE stream ended before message_stop", retryable=True
                    )
                if len(result) > self.config.get("response_limit"):
                    raise LLMError("Streamed content exceeds configured limit")
                if finish_reason == "refusal":
                    raise LLMError("Model refused the request")
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
        system_text, converted = self._convert_messages(messages)
        thinking, thinking_level, timeout = self._thinking_settings(thinking_level)
        if stream:
            try:
                header_timeout = int(self.config.get("stream_header_timeout", 300))
            except (TypeError, ValueError):
                header_timeout = 300
            if header_timeout > 0:
                # Never shorten the thinking budget: high/max thinking already
                # allows 600/900 seconds for the first response byte.
                timeout = max(timeout, header_timeout)
        if timeout_override is not None:
            timeout = max(timeout, int(timeout_override))
        self.last_thinking_level = thinking_level
        self.last_header_timeout_ms = timeout * 1000
        self.last_timeout_ms = timeout * 1000
        payload = {
            "model": self.config.get("model"),
            "messages": converted,
            "stream": stream,
            "max_tokens": int(max_tokens or self.config.get("max_tokens") or 4096),
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters")
                    or {"type": "object", "properties": {}},
                }
                for tool in tools
            ]
        payload.update(thinking)
        endpoint = self._endpoint()
        headers = {
            "x-api-key": self.config.secret("api_key"),
            "anthropic-version": ANTHROPIC_VERSION,
            # OpenCode Go's Anthropic endpoint uses this for routing/caching,
            # just like its OpenAI-compatible endpoint. Official Anthropic
            # ignores unknown request headers.
            "x-opencode-session": self.session_id,
        }
        return payload, endpoint, headers, timeout

    def _endpoint(self):
        base = str(self.config.get("base_url") or "").rstrip("/")
        if base.endswith("/messages"):
            return base
        if base.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"

    def _is_deepseek(self):
        model = str(self.config.get("model", "")).lower()
        base = str(self.config.get("base_url", "")).lower()
        return model.startswith("deepseek") or "deepseek.com" in base

    def _thinking_settings(self, override=None):
        timeout = self.config.get("request_timeout")
        level = normalize_thinking_level(
            self.config.get("thinking_level", "disabled") if override is None else override,
            "disabled",
        )
        if level == "disabled":
            if self._is_deepseek():
                # DeepSeek's Anthropic endpoint defaults to thinking on; make
                # the disabled setting explicit instead of omitting it.
                return {"thinking": {"type": "disabled"}}, level, timeout
            return {}, level, timeout
        minimum_timeout = 900 if level == "max" else 600
        if self._is_deepseek():
            # DeepSeek's Anthropic-compatible endpoint expects its own
            # thinking shape; Anthropic's adaptive/output_config is ignored.
            return {
                "thinking": {"type": "enabled"},
            }, level, max(timeout, minimum_timeout)
        return {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": level},
        }, level, max(timeout, minimum_timeout)

    @staticmethod
    def _convert_messages(messages):
        system_parts = []
        converted = []
        tool_results = []

        def flush_tool_results():
            if tool_results:
                converted.append({
                    "role": "user",
                    "content": list(tool_results),
                })
                del tool_results[:]

        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "system":
                content = message.get("content", "")
                if isinstance(content, str) and content.strip():
                    system_parts.append(content)
                continue
            if role == "tool":
                call_id = message.get("tool_call_id")
                if not call_id:
                    continue
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": str(call_id),
                    "content": message.get("content", ""),
                })
                continue
            flush_tool_results()
            content = message.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            calls = message.get("tool_calls")
            if role == "assistant" and calls:
                blocks = []
                if content.strip():
                    blocks.append({"type": "text", "text": content})
                for index, call in enumerate(calls):
                    blocks.append({
                        "type": "tool_use",
                        "id": call.get("id") or "call_%s" % (index + 1),
                        "name": call.get("name", ""),
                        "input": call.get("args") or {},
                    })
                if blocks:
                    converted.append({"role": "assistant", "content": blocks})
                continue
            if role not in ("user", "assistant"):
                continue
            if not content.strip():
                continue
            if not converted and role == "assistant":
                continue
            converted.append({"role": role, "content": content})
        flush_tool_results()
        if not converted:
            converted.append({"role": "user", "content": "(empty)"})
        if converted[-1]["role"] == "assistant":
            converted.append({"role": "user", "content": "(continue)"})
        return "\n\n".join(system_parts), converted

    @staticmethod
    def _content_blocks(content):
        reasoning_parts = []
        text_parts = []
        tool_calls = []
        if isinstance(content, str):
            return "", content, tool_calls
        if not isinstance(content, list):
            raise LLMError("Anthropic response contains no content")
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                value = block.get("text")
                if isinstance(value, str):
                    text_parts.append(value)
            elif btype == "thinking":
                value = block.get("thinking")
                if isinstance(value, str):
                    reasoning_parts.append(value)
            elif btype == "tool_use":
                name = block.get("name")
                if not isinstance(name, str) or not name:
                    continue
                arguments = block.get("input")
                if not isinstance(arguments, dict):
                    arguments = {}
                tool_calls.append({
                    "id": str(block.get("id") or "call_%s" % (len(tool_calls) + 1)),
                    "name": name,
                    "args": arguments,
                    "error": "",
                })
        return "".join(reasoning_parts), "".join(text_parts), tool_calls

    @staticmethod
    def _finalize_tool_calls(states):
        calls = []
        for index in sorted(states):
            state = states[index]
            name = state.get("name", "")
            if not name:
                continue
            raw = state.get("json", "")
            error = ""
            if raw:
                try:
                    arguments = json.loads(raw)
                except ValueError:
                    arguments = state.get("input") or {}
                    error = "Invalid JSON tool arguments"
            else:
                arguments = state.get("input") or {}
            if not isinstance(arguments, dict):
                arguments = {}
                error = "Tool arguments must be a JSON object"
            calls.append({
                "id": state.get("id") or "call_%s" % (index + 1),
                "name": name,
                "args": arguments,
                "error": error,
            })
        return calls

    @staticmethod
    def _stop_reason(reason):
        mapping = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
            "pause_turn": "pause",
            "refusal": "refusal",
        }
        return mapping.get(reason, reason)

    @staticmethod
    def _emit(callback, text):
        if callback is None:
            return
        try:
            callback(text)
        except LLMStreamCancelled:
            raise
        except Exception:
            pass

    def _http_error(self, status, text, headers):
        message = text[:600]
        error_type = ""
        try:
            data = json.loads(text)
            error = data.get("error") if isinstance(data, dict) else None
            if isinstance(error, dict):
                if error.get("message"):
                    message = str(error["message"])[:600]
                error_type = str(error.get("type") or "")
        except (ValueError, TypeError):
            pass
        retryable = (
            status in RETRYABLE_STATUS
            or error_type in _ANTHROPIC_RETRYABLE_ERRORS
        )
        if error_type in _ANTHROPIC_FATAL_ERRORS:
            retryable = False
        return llm_error(
            "Anthropic API HTTP %s: %s" % (status, message),
            status=status,
            retryable=retryable,
            retry_after=_retry_after_seconds(headers),
        )

    def _api_error(self, data):
        error = data.get("error") if isinstance(data, dict) else None
        if not isinstance(error, dict):
            error = {}
        message = str(error.get("message") or data.get("message")
                      or "Anthropic stream error")[:600]
        error_type = str(error.get("type") or "")
        return llm_error(
            "Anthropic API error: %s" % message,
            retryable=error_type in _ANTHROPIC_RETRYABLE_ERRORS,
        )

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
                "Model returned empty content (stop_reason=%s); increase max_tokens"
                % self.last_finish_reason
            )

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
