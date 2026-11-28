"""Anthropic Messages API client for MicroPython.

The Messages API differs from OpenAI chat completions in several ways:
the system prompt is a top-level field, max_tokens is required, thinking
uses adaptive mode plus output_config.effort on current models, and SSE
events are typed content-block deltas instead of choices/deltas.

This client always streams. A non-streaming Messages request waits for the
whole completion before the first byte, and a long answer can leave the
ESP32 socket/TLS read blocking past the hardware watchdog. ``complete()``
is kept for callers that only need the final text, but it drives the same
SSE path internally.
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
_THINKING_TYPES = ("thinking", "redacted_thinking")


class AnthropicClient:
    supports_native_tools = True
    # The harness must never issue a non-streaming Messages request: a long
    # generation would block the main-thread network read past the watchdog.
    force_stream = True

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
        self.last_stop_details = None
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
        # Assistant content blocks (thinking/text/tool_use) in their original
        # order, kept only for turns that contain tool calls. They must be
        # replayed verbatim, including thinking signatures.
        self.last_thinking_blocks = []
        self.last_assistant_blocks = []
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.last_cache_read_tokens = 0
        self.last_cache_creation_tokens = 0
        self.last_usage = {}

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False, timeout=None, tools=None):
        # Keep the public non-stream API, but always use SSE underneath.
        return self.complete_stream(
            messages,
            None,
            None,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
            allow_truncated=allow_truncated,
            timeout=timeout,
            tools=tools,
        )

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
        # Indexed content-block state. Tool blocks accumulate input_json_delta
        # fragments and are parsed only at content_block_stop; partial JSON is
        # never valid before then.
        blocks = {}
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

        def emit_text(text):
            nonlocal first_token_at, last_token_at
            nonlocal token_events, max_gap_ms, last_event
            if not isinstance(text, str) or not text:
                return
            content_parts.append(text)
            self.last_content_chars += len(text)
            self._emit(on_content, text)
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

        def emit_reasoning(text):
            nonlocal reasoning_chars, first_token_at, last_token_at
            nonlocal token_events, max_gap_ms, last_event
            if not isinstance(text, str) or not text:
                return
            reasoning_chars += len(text)
            self.last_reasoning_chars = reasoning_chars
            self._emit(on_reasoning, text)
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

        def block_state(index, block_type):
            state = blocks.get(index)
            if state is not None:
                return state
            block = {"type": block_type}
            if block_type == "text":
                block["text"] = ""
            elif block_type == "thinking":
                block["thinking"] = ""
            state = {"block": block, "json": "", "error": ""}
            if block_type == "text":
                # Keep references to the emitted chunks; joining happens only
                # when a tool call needs the assistant content replayed.
                state["text_parts"] = []
            blocks[index] = state
            if block_type == "tool_use":
                tool_states[index] = state
            return state

        def finalize_tool(state):
            if state.get("done"):
                return
            state["done"] = True
            raw = state.get("json", "")
            error = ""
            if raw:
                try:
                    arguments = json.loads(raw)
                except ValueError:
                    arguments = state["block"].get("input")
                    error = "Invalid JSON tool arguments"
            else:
                # A zero-argument tool has no input_json_delta at all; its
                # input is the empty object from content_block_start.
                arguments = state["block"].get("input")
            if not isinstance(arguments, dict):
                arguments = {}
                error = "Tool arguments must be a JSON object"
            state["block"]["input"] = arguments
            state["error"] = error
            # Free the raw JSON fragments; the parsed object is authoritative.
            state["json"] = ""

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
                        index = event.get("index")
                        if not isinstance(index, int):
                            index = -1
                        if etype == "content_block_start":
                            block = event.get("content_block") or {}
                            if not isinstance(block, dict):
                                continue
                            block_type = block.get("type")
                            state = {"block": dict(block), "json": "",
                                     "error": ""}
                            if block_type == "text":
                                state["text_parts"] = []
                                text = state["block"].get("text")
                                if isinstance(text, str) and text:
                                    state["text_parts"].append(text)
                                    emit_text(text)
                            elif block_type == "thinking":
                                text = state["block"].get("thinking")
                                if not isinstance(text, str):
                                    text = ""
                                state["block"]["thinking"] = text
                                emit_reasoning(text)
                            elif block_type == "tool_use":
                                if not isinstance(
                                        state["block"].get("input"), dict):
                                    state["block"]["input"] = {}
                                tool_states[index] = state
                            if state is not None:
                                blocks[index] = state
                            last_event = self._ticks()
                        elif etype == "content_block_delta":
                            delta = event.get("delta") or {}
                            if not isinstance(delta, dict):
                                continue
                            dtype = delta.get("type")
                            if dtype == "thinking_delta":
                                text = delta.get("thinking")
                                state = block_state(index, "thinking")
                                if isinstance(text, str):
                                    state["block"]["thinking"] = (
                                        state["block"].get("thinking") or ""
                                    ) + text
                                    emit_reasoning(text)
                            elif dtype == "text_delta":
                                text = delta.get("text")
                                if isinstance(text, str):
                                    state = block_state(index, "text")
                                    parts = state.get("text_parts")
                                    if parts is None:
                                        parts = []
                                        state["text_parts"] = parts
                                    parts.append(text)
                                    emit_text(text)
                            elif dtype == "signature_delta":
                                signature = delta.get("signature")
                                if isinstance(signature, str) and signature:
                                    state = block_state(index, "thinking")
                                    state["block"]["signature"] = (
                                        state["block"].get("signature") or ""
                                    ) + signature
                                    last_event = self._ticks()
                            elif dtype == "input_json_delta":
                                state = blocks.get(index)
                                if state is None:
                                    state = block_state(index, "tool_use")
                                state["json"] += str(
                                    delta.get("partial_json") or "")
                                last_event = self._ticks()
                        elif etype == "content_block_stop":
                            state = blocks.get(index)
                            if state is not None and state["block"].get(
                                    "type") == "tool_use":
                                finalize_tool(state)
                            last_event = self._ticks()
                        elif etype == "message_delta":
                            delta = event.get("delta") or {}
                            if isinstance(delta, dict):
                                stop_reason = delta.get("stop_reason")
                                if stop_reason:
                                    finish_reason = stop_reason
                                details = delta.get("stop_details")
                                if not isinstance(details, dict):
                                    details = event.get("stop_details")
                                if isinstance(details, dict):
                                    self.last_stop_details = details
                            self._capture_usage(event.get("usage"))
                            last_event = self._ticks()
                        elif etype == "message_stop":
                            done = True
                            break
                        elif etype == "message_start":
                            message = event.get("message") or {}
                            if isinstance(message, dict):
                                self._capture_usage(message.get("usage"))
                            last_event = self._ticks()
                        elif etype == "ping":
                            pass
                    elif line.startswith(b"data:"):
                        data_lines.append(line[5:].lstrip())
                    elif line.startswith(b":") or line.startswith(
                            (b"event:", b"id:", b"retry:")):
                        continue

                for index in list(tool_states):
                    finalize_tool(tool_states[index])
                self.last_reasoning_chars = reasoning_chars
                self.last_finish_reason = self._stop_reason(finish_reason)
                self.last_response_chars = reader.bytes_read
                self.last_token_count = token_events
                self.last_max_gap_ms = max_gap_ms
                self.last_thinking_blocks = self._thinking_blocks(blocks)
                self.last_assistant_blocks = (
                    self._assistant_blocks(blocks) if tool_states else [])
                self.last_tool_calls = self._finalize_tool_calls(blocks)
                result = "".join(content_parts)
                self.last_content_chars = len(result)
                if not done and finish_reason is None:
                    raise llm_error(
                        "SSE stream ended before message_stop", retryable=True
                    )
                if len(result) > self.config.get("response_limit"):
                    raise LLMError("Streamed content exceeds configured limit")
                if finish_reason == "refusal":
                    raise self._refusal_error()
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
        thinking_config = thinking.get("thinking") if isinstance(thinking, dict) else None
        include_thinking = (
            isinstance(thinking_config, dict)
            and thinking_config.get("type") != "disabled"
        )
        system_text, converted = self._convert_messages(
            messages, include_thinking)
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
        self.last_thinking_configured = normalize_thinking_level(
            self.config.get("thinking_level", "disabled"), "disabled")
        self.last_thinking_overridden = (
            self.last_thinking_configured != thinking_level)
        self.last_header_timeout_ms = timeout * 1000
        self.last_timeout_ms = timeout * 1000
        prompt_cache = self._prompt_cache_enabled()
        if prompt_cache:
            self._apply_cache_breakpoint(converted)
        payload = {
            "model": self.config.get("model"),
            "messages": converted,
            "stream": stream,
            "max_tokens": int(max_tokens or self.config.get("max_tokens") or 4096),
        }
        if system_text:
            if prompt_cache:
                # Cache tools + system as the stable prefix. The second
                # breakpoint is the latest tool_result below.
                payload["system"] = [{
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
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

    def _prompt_cache_enabled(self):
        if self._is_deepseek():
            # DeepSeek's Anthropic-compatible endpoint has its own automatic
            # caching and does not need (or accept) Anthropic cache_control.
            return False
        return bool(self.config.get("prompt_cache", True))

    @staticmethod
    def _apply_cache_breakpoint(messages):
        """Mark the latest tool_result, so the growing prefix is cached."""
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                return
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    block["cache_control"] = {"type": "ephemeral"}
                    return

    def _capture_usage(self, usage):
        if not isinstance(usage, dict):
            return
        fields = (
            ("input_tokens", "last_input_tokens"),
            ("output_tokens", "last_output_tokens"),
            ("cache_read_input_tokens", "last_cache_read_tokens"),
            ("cache_creation_input_tokens", "last_cache_creation_tokens"),
        )
        changed = False
        for key, attr in fields:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(self, attr, value)
                changed = True
        if changed:
            self.last_usage = {
                "input_tokens": self.last_input_tokens,
                "output_tokens": self.last_output_tokens,
                "cache_read_input_tokens": self.last_cache_read_tokens,
                "cache_creation_input_tokens":
                    self.last_cache_creation_tokens,
            }

    @staticmethod
    def _thinking_blocks(blocks):
        selected = []
        for index in sorted(blocks):
            block = blocks[index].get("block") or {}
            block_type = block.get("type")
            if block_type == "thinking":
                # A thinking block without its signature is not replayable.
                if not block.get("signature"):
                    continue
                selected.append(block)
            elif block_type == "redacted_thinking":
                if block.get("data"):
                    selected.append(block)
        return selected

    @staticmethod
    def _assistant_blocks(blocks):
        """Return the assistant content in original order for tool replay."""
        selected = []
        for index in sorted(blocks):
            state = blocks[index]
            block = state.get("block") or {}
            block_type = block.get("type")
            if block_type == "text":
                text = "".join(state.get("text_parts") or [])
                if not text:
                    continue
                block["text"] = text
                selected.append(block)
            elif block_type == "thinking":
                if block.get("signature"):
                    selected.append(block)
            elif block_type == "redacted_thinking":
                if block.get("data"):
                    selected.append(block)
            elif block_type == "tool_use":
                selected.append(block)
        return selected

    def _refusal_error(self):
        category = ""
        details = self.last_stop_details
        if isinstance(details, dict):
            category = details.get("category") or ""
        return LLMError(
            "Model refused the request%s"
            % ((" (%s)" % category) if category else "")
        )

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
    def _convert_messages(messages, include_thinking=True):
        """Translate provider-neutral messages to Messages API content blocks.

        Anthropic requires every tool_use id from one assistant message to be
        answered by one tool_result block in the immediately following user
        message. Local events or observations that arrive between the tool
        call and its result are therefore buffered and appended after the
        tool_result blocks in that same user message instead of forming a
        separate message.
        """
        system_parts = []
        converted = []
        expected = []
        received = set()
        tool_results = []
        pending_text = []

        def flush_tools():
            if not expected and not tool_results and not pending_text:
                return
            blocks = []
            for call_id in expected:
                if call_id not in received:
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": "(no result was recorded for this tool call)",
                        "is_error": True,
                    })
            blocks.extend(tool_results)
            blocks.extend(pending_text)
            del tool_results[:]
            del pending_text[:]
            expected[:] = []
            received.clear()
            if blocks:
                converted.append({"role": "user", "content": blocks})

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
                call_id = str(call_id)
                # Only answer tool_use ids from the batch we are currently
                # collecting; orphan results would be rejected by the API.
                if (not expected or call_id not in expected
                        or call_id in received):
                    continue
                content = message.get("content", "")
                if not isinstance(content, str):
                    content = str(content)
                if not content.strip():
                    content = "(no output)"
                block = {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": content,
                }
                if message.get("is_error"):
                    block["is_error"] = True
                tool_results.append(block)
                received.add(call_id)
                continue
            if expected:
                if role == "user":
                    # Keep the tool_result message adjacent to its assistant
                    # tool_use turn; merge this text into that user message.
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        pending_text.append({"type": "text", "text": content})
                    continue
                flush_tools()
            if role == "assistant":
                calls = message.get("tool_calls")
                content = message.get("content", "")
                if not isinstance(content, str):
                    content = str(content)
                if calls:
                    blocks = []
                    ids = []
                    raw_blocks = message.get("assistant_blocks")
                    if isinstance(raw_blocks, list) and raw_blocks:
                        # Replay the streamed assistant content verbatim, in
                        # its original order: interleaved thinking blocks must
                        # stay next to the tool_use they precede.
                        for block in raw_blocks:
                            if not isinstance(block, dict):
                                continue
                            block_type = block.get("type")
                            if block_type == "text":
                                text = block.get("text")
                                if isinstance(text, str) and text:
                                    blocks.append({
                                        "type": "text", "text": text,
                                    })
                            elif block_type in _THINKING_TYPES:
                                if not include_thinking:
                                    continue
                                if block_type == "redacted_thinking":
                                    if block.get("data"):
                                        blocks.append(block)
                                elif block.get("signature"):
                                    blocks.append(block)
                            elif block_type == "tool_use":
                                name = block.get("name")
                                if not isinstance(name, str) or not name:
                                    continue
                                call_id = str(
                                    block.get("id")
                                    or "call_%s" % (len(ids) + 1))
                                arguments = block.get("input")
                                if not isinstance(arguments, dict):
                                    arguments = {}
                                blocks.append({
                                    "type": "tool_use",
                                    "id": call_id,
                                    "name": name,
                                    "input": arguments,
                                })
                                ids.append(call_id)
                    else:
                        thinking = message.get("thinking_blocks")
                        if include_thinking and isinstance(thinking, list):
                            for block in thinking:
                                if (isinstance(block, dict)
                                        and block.get("type") in _THINKING_TYPES):
                                    blocks.append(block)
                        if content.strip():
                            blocks.append({"type": "text", "text": content})
                        for index, call in enumerate(calls):
                            if not isinstance(call, dict):
                                continue
                            call_id = str(
                                call.get("id") or "call_%s" % (index + 1))
                            arguments = call.get("args")
                            if not isinstance(arguments, dict):
                                arguments = {}
                            blocks.append({
                                "type": "tool_use",
                                "id": call_id,
                                "name": str(call.get("name", "")),
                                "input": arguments,
                            })
                            ids.append(call_id)
                    if blocks:
                        converted.append({
                            "role": "assistant",
                            "content": blocks,
                        })
                        expected[:] = ids
                        received.clear()
                    continue
                if not content.strip():
                    continue
                if not converted:
                    # The API requires the first message to be from the user.
                    continue
                converted.append({"role": "assistant", "content": content})
                continue
            if role == "user":
                content = message.get("content", "")
                if not isinstance(content, str):
                    content = str(content)
                if not content.strip():
                    continue
                converted.append({"role": "user", "content": content})
        flush_tools()
        while converted and converted[0].get("role") == "assistant":
            converted.pop(0)
            if converted and AnthropicClient._has_block_type(
                    converted[0].get("content"), ("tool_result",)):
                converted.pop(0)
        converted = AnthropicClient._merge_same_role(converted)
        if not converted:
            converted.append({"role": "user", "content": "(empty)"})
        if converted[-1].get("role") == "assistant":
            converted.append({"role": "user", "content": "(continue)"})
        return "\n\n".join(system_parts), converted

    @staticmethod
    def _has_block_type(content, block_types):
        if not isinstance(content, list):
            return False
        for block in content:
            if isinstance(block, dict) and block.get("type") in block_types:
                return True
        return False

    @staticmethod
    def _merge_same_role(messages):
        merged = []
        for message in messages:
            if merged and message.get("role") == merged[-1].get("role"):
                previous = merged[-1]
                left = previous.get("content")
                right = message.get("content")
                if (not AnthropicClient._has_block_type(
                        left, ("tool_result", "tool_use"))
                        and not AnthropicClient._has_block_type(
                            right, ("tool_result", "tool_use"))):
                    if isinstance(left, str) and isinstance(right, str):
                        previous["content"] = left + "\n\n" + right
                        continue
                    blocks = []
                    if isinstance(left, str):
                        if left:
                            blocks.append({"type": "text", "text": left})
                    elif isinstance(left, list):
                        blocks.extend(left)
                    if isinstance(right, str):
                        if right:
                            blocks.append({"type": "text", "text": right})
                    elif isinstance(right, list):
                        blocks.extend(right)
                    previous["content"] = blocks
                    continue
            merged.append(message)
        return merged

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
    def _finalize_tool_calls(blocks):
        calls = []
        for index in sorted(blocks):
            state = blocks[index]
            block = state.get("block") or {}
            if block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = block.get("input")
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append({
                "id": str(block.get("id") or "call_%s" % (len(calls) + 1)),
                "name": name,
                "args": arguments,
                "error": state.get("error", ""),
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
            if tool_calls:
                # The tool input JSON is necessarily incomplete. Never let a
                # truncated tool_use reach the executor.
                self.last_tool_calls = []
                raise LLMError(
                    "Model output was truncated at max_tokens=%s while "
                    "streaming a tool call; tool arguments are incomplete and "
                    "no tool was executed (reasoning=%s chars, content=%s "
                    "chars); increase max_tokens or lower thinking"
                    % (
                        self.config.get("max_tokens"),
                        self.last_reasoning_chars,
                        self.last_content_chars,
                    )
                )
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
        if self.last_finish_reason == "pause":
            raise LLMError(
                "Model paused before finishing (stop_reason=pause_turn); "
                "the response was not executed"
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
