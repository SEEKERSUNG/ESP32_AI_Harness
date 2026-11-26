"""Application adapter, JSON/NDJSON routes and turn runner for the web console."""

import gc
import json
import os

from . import __version__
from .agent import (
    AgentError,
    SEARCH_PROVIDERS,
    auto_source_is_safe,
    message_kind,
)
from .config import write_text
from .llm import (
    LLMStreamCancelled,
    default_base_url,
    normalize_api_provider,
    normalize_thinking_level,
)
from .web import WebError
from .webauth import (
    WebAuthError,
    cookie_header,
    expired_cookie_header,
    parse_cookie,
)
from .webui import APP_CSS, APP_JS, INDEX_HTML


def _error(message, status=400, code="invalid"):
    return WebError(message, status, code)


def _boolean(body, key, default=None):
    if key not in body:
        return default
    value = body[key]
    if not isinstance(value, bool):
        raise _error("%s must be a JSON boolean" % key, 400, "invalid")
    return value


def _search_status(app, key, default):
    try:
        return app.agent.search_status().get(key, default)
    except Exception:
        return default


def _truncate(value, limit):
    value = value or ""
    if len(value) <= limit:
        return value, False
    if limit <= 0:
        return "", True
    marker = "\n...[truncated]"
    if limit <= len(marker):
        return value[:limit], True
    return value[:limit - len(marker)] + marker, True


class WebCommandInteractive(Exception):
    pass


class WebCommandConfirm(Exception):
    pass


class CommandCaptureSink:
    def __init__(self, limit=16384, confirmed=False):
        self.limit = limit
        self.confirmed = confirmed
        self.parts = []
        self.size = 0
        self.closed = False

    def _append(self, text):
        text = str(text)
        remaining = self.limit - self.size
        if remaining > 0:
            part = text[:remaining]
            self.parts.append(part)
            self.size += len(part)
        if len(text) > remaining:
            self.closed = True

    def write(self, text):
        self._append(text)

    def line(self, text=""):
        self._append(str(text) + "\n")

    def readline(self, prompt="", secret=False):
        raise WebCommandInteractive(
            "This command needs interactive input; use the corresponding web "
            "form or the serial console")

    def ask_yes_no(self, prompt, default=False):
        if self.confirmed:
            self._append("%s yes (confirmed)\n" % prompt)
            return True
        raise WebCommandConfirm(prompt)

    def model_start(self):
        self._append("--- model request ---\n")

    def model_result(self, error, llm):
        if error:
            self._append("model error: %s\n" % error)

    def reasoning(self, text):
        self._append(text)

    def content(self, text):
        self._append(text)

    def notice(self, text):
        self._append(str(text) + "\n")

    def tool_start(self, name, args):
        self._append("tool %s\n" % name)

    def tool_result(self, name, result):
        self._append("tool %s %s\n" % (name, "OK" if result.ok else "ERROR"))
        if result.output:
            self._append(result.output + "\n")
        if result.error:
            self._append(result.error + "\n")

    def execution(self, result):
        self._append("execution %s %sms\n" % ("OK" if result.ok else "ERROR", result.elapsed_ms))

    def getvalue(self):
        value = "".join(self.parts)
        if self.closed:
            value += "\n...[command output truncated]"
        return value


class WebTurnSink:
    def __init__(self, writer, app):
        self.writer = writer
        self.app = app
        self.pending_kind = None
        self.pending_text = ""
        self.closed = False
        self.cancelled = False
        try:
            batch_chars = int(app.config.get("web_stream_batch_chars", 32))
        except (TypeError, ValueError):
            batch_chars = 32
        self.batch_chars = max(1, batch_chars)
        self.size = 0
        self.reasoning_size = 0

    def _send(self, value):
        if self.cancelled:
            return False
        if self.closed or self.writer.closed:
            self.closed = True
            raise LLMStreamCancelled()
        if not self.writer.event(value):
            self.closed = True
            raise LLMStreamCancelled()
        return True

    def stop(self):
        # The client is gone or the user pressed Stop: drop pending text and
        # make the remaining cleanup calls no-ops.
        self.cancelled = True
        self.pending_text = ""
        self.pending_kind = None
        self.closed = True

    def check_cancel(self):
        if self.cancelled or self.closed:
            raise LLMStreamCancelled()
        if self.writer.client_closed():
            self.stop()
            raise LLMStreamCancelled()

    def _append(self, kind, text):
        if not text or self.cancelled:
            return
        if self.pending_kind != kind:
            self.flush()
            self.pending_kind = kind
        # Reasoning and answer text are streamed only and never stored on the
        # device, so do not cap either here. The browser appends text
        # incrementally; content must remain complete even for long answers.
        part = text
        if kind == "reasoning":
            self.reasoning_size += len(part)
        else:
            self.size += len(part)
        self.pending_text += part
        if len(self.pending_text) >= self.batch_chars:
            self.flush()

    def flush(self):
        if not self.pending_text:
            return
        kind = self.pending_kind
        text = self.pending_text
        self.pending_text = ""
        self.pending_kind = None
        if kind == "reasoning":
            self._send({"t": "reasoning", "d": text})
        else:
            self._send({"t": "content", "d": text})

    def start(self):
        self._send({"t": "start"})

    def write(self, text):
        self.notice(text)

    def line(self, text=""):
        self.notice(str(text))

    def readline(self, prompt="", secret=False):
        raise WebCommandInteractive("This action requires the serial console")

    def ask_yes_no(self, prompt, default=False):
        raise WebCommandInteractive("This action requires serial confirmation")

    def model_start(self):
        self.flush()
        self._send({"t": "model_start"})

    def model_result(self, error, llm):
        self.flush()
        value = {
            "t": "model", "ok": not bool(error),
            "finish": getattr(llm, "last_finish_reason", None),
            "elapsed_ms": getattr(llm, "last_elapsed_ms", 0),
            "timeout_ms": getattr(llm, "last_timeout_ms", 0),
            "header_timeout_ms": getattr(llm, "last_header_timeout_ms", 0),
            "idle_timeout_ms": getattr(llm, "last_idle_timeout_ms", 0),
            "event_idle_timeout_ms": getattr(llm, "last_event_idle_timeout_ms", 0),
            "headers_ms": getattr(llm, "last_headers_ms", 0),
            "first_token_ms": getattr(llm, "last_first_token_ms", 0),
            "token_count": getattr(llm, "last_token_count", 0),
            "max_gap_ms": getattr(llm, "last_max_gap_ms", 0),
            "tail_gap_ms": getattr(llm, "last_tail_gap_ms", 0),
            "reasoning_chars": getattr(llm, "last_reasoning_chars", 0),
            "content_chars": getattr(llm, "last_content_chars", 0),
            "streamed": bool(getattr(llm, "last_streamed", False)),
        }
        if error:
            value["error"] = str(error)[:600]
        self._send(value)

    def reasoning(self, text):
        self._append("reasoning", text)

    def content(self, text):
        self._append("content", text)

    def notice(self, text):
        self.flush()
        self._send({"t": "notice", "d": str(text)[:2000]})

    def question(self, question):
        self.flush()
        value = {
            "t": "question",
            "header": str(question.get("header", "Question"))[:40],
            "question": str(question.get("question", ""))[:1000],
            "options": question.get("options") or [],
            "allow_custom": bool(question.get("allow_custom", True)),
        }
        self._send(value)

    def tool_start(self, name, args):
        self.flush()
        self._send({"t": "tool", "phase": "start", "name": name, "args": args})

    def tool_result(self, name, result):
        self.flush()
        output, _ = _truncate(result.output, 2048)
        error, _ = _truncate(result.error, 512)
        self._send({
            "t": "tool", "phase": "result", "name": name,
            "ok": bool(result.ok), "output": output, "error": error,
            "elapsed_ms": result.elapsed_ms,
        })

    def response(self, explanation, has_code, streamed):
        self.flush()
        if not streamed and explanation:
            text = explanation
            self.size += len(text)
            while text:
                chunk = text[:2048]
                text = text[2048:]
                self._send({"t": "content", "d": chunk})
        self._send({
            "t": "response", "text": "",
            "has_code": bool(has_code),
            "streamed": bool(streamed),
        })

    def code(self, source, auto, safe, timeout_s=None, background=False,
             runnable=True):
        self.flush()
        self._send({
            "t": "code", "source": source or "", "chars": len(source or ""),
            "auto": bool(auto), "safe": bool(safe),
            "timeout_s": timeout_s, "background": bool(background),
            "runnable": bool(runnable),
        })

    def skill(self, proposal):
        self.flush()
        self._send({
            "t": "skill", "name": proposal.get("name", ""),
            "description": proposal.get("description", ""),
            "body": proposal.get("body", ""),
        })

    def execution(self, result, auto=False):
        self.flush()
        self._send({
            "t": "execution", "ok": bool(result.ok),
            "timed_out": bool(getattr(result, "timed_out", False)),
            "cancelled": bool(getattr(result, "cancelled", False)),
            "output": result.output or "", "error": result.error or "",
            "elapsed_ms": result.elapsed_ms, "auto": bool(auto),
            "recovered": bool(getattr(self.app.agent, "recovered_execution", False)),
        })

    def feedback(self, turn):
        self.flush()
        self._send({"t": "feedback", "turn": turn})

    def background(self, job, phase="queued"):
        self.flush()
        self._send({
            "t": "background", "phase": job.get("phase", phase),
            "id": job.get("id", 0),
            "auto": bool(job.get("auto")),
            "recovered": bool(job.get("recovered")),
            "timeout_s": job.get("timeout"),
            "source_chars": int(job.get("source_chars", 0) or 0),
            "task": str(job.get("task", ""))[:200],
            "note": str(job.get("note", ""))[:200],
        })

    def done(self, pending, turns, user_index=None):
        self.flush()
        self._send({
            "t": "done", "pending": pending, "turns": turns,
            "user_index": user_index,
        })

    def error(self, message, code="internal"):
        self.flush()
        self._send({"t": "error", "code": code, "message": str(message)[:600]})


class WebApp:
    def __init__(self, app, auth):
        self.app = app
        self.auth = auth

    def body_limit(self):
        return 65536

    def request_timeout(self):
        try:
            value = int(self.app.config.get("web_request_timeout_s", 30))
        except (TypeError, ValueError):
            value = 30
        return max(1, min(value, 120))

    def _start_stream(self, writer):
        try:
            return writer.start_stream()
        except BaseException:
            self.app.end_operation()
            raise

    def tick(self):
        self.app.tick()

    def queue_notice(self, text):
        self.app.queue_notice(text)

    def _status(self):
        try:
            return self.app.network.status()
        except Exception:
            return {"mode": "off", "sta_ip": "", "ap_ip": "", "ap_active": False,
                    "ap_ssid": "", "sta_connected": False, "connected": False}

    def _allowed_hosts(self):
        hosts = ["127.0.0.1", "localhost"]
        status = self._status()
        for key in ("sta_ip", "ap_ip"):
            value = status.get(key)
            if value and value not in hosts:
                hosts.append(value)
        return hosts

    def _validate(self, req):
        host = req.host_name()
        if not host or host not in self._allowed_hosts():
            raise _error("Unexpected Host header", 421, "invalid")
        if req.method == "POST":
            if req.header("x-harness") != "1":
                raise _error("Missing X-Harness header", 403, "invalid")
            origin = req.header("origin")
            if origin and origin != ("http://" + req.host):
                raise _error("Origin mismatch", 403, "invalid")
            content_type = req.header("content-type", "").lower()
            if "application/json" not in content_type:
                raise _error("Expected application/json", 415, "invalid")

    def _json(self, req, limit=None):
        if limit is None:
            limit = int(self.app.config.get("web_body_limit", 32768))
        if len(req.body) > limit:
            raise _error("Request body is too large", 413, "invalid")
        try:
            value = json.loads(req.body.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise _error("Invalid JSON body", 400, "invalid")
        if not isinstance(value, dict):
            raise _error("JSON body must be an object", 400, "invalid")
        return value

    def _token(self, req):
        return parse_cookie(req.header("cookie"))

    def _require(self, req):
        token = self._token(req)
        if not token or not self.auth.valid(token):
            raise _error("Authentication required", 401, "auth")
        return token

    def _is_ap_peer(self, req):
        if not self.app.config.get("web_setup_ap_only", True):
            return True
        if req.peer_ip in ("127.0.0.1", "::1"):
            return True
        status = self._status()
        if not status.get("ap_active"):
            return False
        ap_ip = status.get("ap_ip") or "192.168.4.1"
        prefix = ap_ip.rsplit(".", 1)[0] + "."
        return req.peer_ip.startswith(prefix)

    def _cookie(self, token):
        return cookie_header(token, int(self.app.config.get("web_session_ttl_s", 43200)))

    def _setup_view(self):
        status = self._status()
        reset_note = ""
        note_fn = getattr(self.app, "reset_note", None)
        if note_fn is not None:
            try:
                reset_note = note_fn()
            except Exception:
                reset_note = ""
        return {
            "setup_required": not self.auth.configured(),
            "mode": status.get("mode", "off"),
            "ap_active": bool(status.get("ap_active")),
            "ap_ssid": status.get("ap_ssid", ""),
            "ap_ip": status.get("ap_ip", "192.168.4.1"),
            "ap_off_in_s": status.get("ap_off_in_s", 0),
            "sta_ip": status.get("sta_ip", ""),
            "port": int(self.app.config.get("web_port", 80)),
            "version": __version__,
            "last_reset": getattr(self.app, "reset_info", None),
            "reset_note": reset_note,
        }

    def _pending_view(self):
        agent = self.app.agent
        # Capture references once instead of asking for a kind and then reading
        # it again. A concurrent serial discard may make this snapshot stale,
        # but it can no longer turn a routine status poll into an exception.
        source = agent.pending_source
        skill = agent.pending_skill
        if source is not None:
            return {
                "kind": "code", "code": source,
                "safe": auto_source_is_safe(source),
                "timeout_s": agent.pending_timeout
                if agent.pending_timeout is not None
                else self.app.config.get("code_timeout_s", 20),
                "skill": None,
            }
        if skill is not None:
            return {
                "kind": "skill", "code": None, "safe": None,
                "skill": skill,
            }
        return {
            "kind": "none", "code": None, "safe": None,
            "skill": None,
        }

    @staticmethod
    def _empty_live():
        return {
            "id": 0, "active": False, "chars": 0,
            "reasoning_chars": 0, "truncated": False, "turns": 0,
        }

    def _live_status(self):
        view = getattr(self.app, "live_status", None)
        if view is None:
            return self._empty_live()
        try:
            return view()
        except Exception:
            return self._empty_live()

    def _live_view(self):
        view = getattr(self.app, "live_view", None)
        if view is None:
            value = self._empty_live()
            value.update({"content": "", "reasoning": "",
                          "started_ms": 0, "finished_ms": 0})
            return value
        try:
            return view()
        except Exception:
            value = self._empty_live()
            value.update({"content": "", "reasoning": "",
                          "started_ms": 0, "finished_ms": 0})
            return value

    def _status_view(self):
        status = self._status()
        agent = self.app.agent
        try:
            usage = agent.context_usage()
        except Exception:
            usage = {"messages": len(agent.history), "chars": 0,
                     "estimated_tokens": 0, "percent": 0}
        pending = self._pending_view()
        try:
            heap = gc.mem_free()
        except AttributeError:
            heap = None
        return {
            "ok": True,
            "setup_required": not self.auth.configured(),
            "version": __version__,
            "mode": status.get("mode", "off"),
            "sta_ip": status.get("sta_ip", ""),
            "sta_ssid": status.get("sta_ssid", ""),
            "ap_active": bool(status.get("ap_active")),
            "ap_ip": status.get("ap_ip", ""),
            "ap_ssid": status.get("ap_ssid", ""),
            "ap_off_in_s": status.get("ap_off_in_s", 0),
            "connecting": bool(status.get("connecting")),
            "wifi_error": status.get("last_error", ""),
            "wifi_configured": bool(self.app.config.secret("wifi_ssid")),
            "heap_free": heap,
            "history": {
                "messages": usage.get("messages", 0),
                "chars": usage.get("chars", 0),
                "estimated_tokens": usage.get("estimated_tokens", 0),
                "percent": usage.get("percent", 0),
            },
            "pending": pending,
            "question": getattr(agent, "pending_question", None),
            "background": self.app.background_view(),
            "backgrounds": self.app.background_views(result_limit=4096),
            "background_active": bool(self.app.background_active()),
            "auto": {
                "run": bool(self.app.config.get("auto_run", False)),
                "feedback": bool(self.app.config.get("auto_feedback", True)),
                "background": bool(
                    self.app.config.get("background_execution", True)),
                "continue_seq": int(
                    getattr(self.app, "auto_continue_seq", 0)),
            },
            "live": self._live_status(),
            "model": {
                "provider": normalize_api_provider(
                    self.app.config.get("api_provider", "openai")
                ),
                "base_url": self.app.config.get("base_url", ""),
                "model": self.app.config.get("model", ""),
                "api_key_set": bool(self.app.config.secret("api_key")),
            },
            "thinking": {
                "level": self.app.config.get("thinking_level", "high"),
                "show": bool(self.app.config.get("show_thinking", False)),
            },
            "mcp": {
                "enabled": bool(self.app.config.get("mcp_enabled", True)),
                "servers": len(
                    self.app.mcp_store.load()
                    if getattr(self.app, "mcp_store", None) is not None else []
                ),
                "tools": len(getattr(getattr(self.app, "mcp", None), "tools", [])),
            },
            "sessions": self.auth.session_count(),
            "busy": self.app.is_busy(),
            "turn_busy": (
                self.app.is_turn_busy()
                if getattr(self.app, "is_turn_busy", None) is not None
                else self.app.is_busy()
            ),
            "busy_source": self.app.busy_source,
        }

    def _config_view(self):
        status = self._status()
        ca_file = self.app.config.get("ca_file") or ""
        try:
            os.stat(ca_file)
            ca_present = True
        except (OSError, TypeError, ValueError):
            ca_present = False
        try:
            usage = self.app.agent.context_usage()
        except Exception:
            usage = {"messages": 0, "chars": 0}
        return {
            "ok": True,
            "wifi_ssid": self.app.config.secret("wifi_ssid"),
            "wifi_password_set": bool(self.app.config.secret("wifi_password")),
            "api_provider": normalize_api_provider(
                self.app.config.get("api_provider", "openai")
            ),
            "base_url": self.app.config.get("base_url", ""),
            "model": self.app.config.get("model", ""),
            "api_key_set": bool(self.app.config.secret("api_key")),
            "search_enabled": bool(self.app.config.get("search_enabled", True)),
            "search_provider": self.app.config.get("search_provider", "auto"),
            "search_provider_effective": _search_status(
                self.app, "effective", "bing"),
            "search_provider_fallback": _search_status(
                self.app, "fallback", False),
            "search_bing_url": self.app.config.get("search_bing_url", ""),
            "ntp_server": self.app.config.get("ntp_server", "pool.ntp.org"),
            "ntp_sync_interval_s": self.app.config.get(
                "ntp_sync_interval_s", 21600),
            "timezone_offset_minutes": self.app.config.get(
                "timezone_offset_minutes", 480),
            "http_tool_enabled": bool(
                self.app.config.get("http_tool_enabled", True)),
            "http_tool_allow_http": bool(
                self.app.config.get("http_tool_allow_http", False)),
            "mcp_enabled": bool(self.app.config.get("mcp_enabled", True)),
            "persist_conversation": bool(
                self.app.config.get("persist_conversation", True)),
            "ca_file": self.app.config.get("ca_file", "/ca.pem"),
            "ca_present": ca_present,
            "tls_verify": bool(self.app.config.get("tls_verify", True)),
            "thinking_level": self.app.config.get("thinking_level", "high"),
            "show_thinking": bool(self.app.config.get("show_thinking", False)),
            "auto_run": bool(self.app.config.get("auto_run", False)),
            "auto_feedback": bool(self.app.config.get("auto_feedback", True)),
            "native_tools": bool(self.app.config.get("native_tools", True)),
            "background_execution": bool(
                self.app.config.get("background_execution", True)),
            "background_max_jobs": int(
                self.app.config.get("background_max_jobs", 4)),
            "web_port": self.app.config.get("web_port", 80),
            "web_enabled": bool(self.app.config.get("web_enabled", True)),
            "web_password_set": self.auth.configured(),
            "ap_ssid": status.get("ap_ssid", ""),
            "ap_password_set": bool(self.app.config.secret("ap_password")),
            "mode": status.get("mode", "off"),
            "sta_ip": status.get("sta_ip", ""),
            "ap_ip": status.get("ap_ip", ""),
            "ap_active": bool(status.get("ap_active")),
            "history": {"messages": usage.get("messages", 0), "chars": usage.get("chars", 0)},
            "max_tokens": self.app.config.get("max_tokens", 32768),
            "max_turns": self.app.config.get("max_turns", 32),
            "input_limit": self.app.config.get("input_limit", 2000),
            "stream_header_timeout": self.app.config.get("stream_header_timeout", 300),
            "stream_idle_timeout": self.app.config.get("stream_idle_timeout", 300),
            "stream_event_idle_timeout": self.app.config.get(
                "stream_event_idle_timeout", 300
            ),
            "stream_first_token_timeout": self.app.config.get(
                "stream_first_token_timeout", 600
            ),
            "request_timeout": self.app.config.get("request_timeout", 120),
            "api_retries": self.app.config.get("api_retries", 3),
            "api_retry_base_ms": self.app.config.get("api_retry_base_ms", 1000),
            "api_retry_max_ms": self.app.config.get("api_retry_max_ms", 30000),
            "api_retry_after_max_ms": self.app.config.get(
                "api_retry_after_max_ms", 300000
            ),
            "api_retry_jitter_pct": self.app.config.get("api_retry_jitter_pct", 25),
        }

    def _history_view(self, limit):
        messages = list(self.app.agent.history)
        limit = max(1, min(limit, int(self.app.config.get("web_history_limit", 200))))
        selected = messages[-limit:]
        window_start = len(messages) - len(selected)
        total_limit = max(0, int(self.app.config.get("web_history_chars", 131072)))
        newest_first = []
        total = 0
        truncated = len(messages) > len(selected)
        # Spend the bounded response budget on the newest messages first, then
        # restore chronological order for the browser.
        for offset in range(len(selected) - 1, -1, -1):
            message = selected[offset]
            content, cut = _truncate(message.get("content", ""), 8192)
            if total + len(content) > total_limit:
                truncated = True
                if newest_first:
                    break
                content, cut = _truncate(content, total_limit)
                if not content:
                    break
            role = message.get("role", "")
            reasoning = ""
            if role == "assistant":
                raw_reasoning = message.get("reasoning", "")
                if isinstance(raw_reasoning, str):
                    reasoning = raw_reasoning[:4096]
                    room = total_limit - total - len(content)
                    if room <= 0:
                        reasoning = ""
                    elif len(reasoning) > room:
                        reasoning = reasoning[:room]
            total += len(content) + len(reasoning)
            truncated = truncated or cut
            newest_first.append({
                "role": role, "content": content,
                "reasoning": reasoning,
                "truncated": cut,
                "index": window_start + offset,
                "kind": message_kind(message),
            })
            if total >= total_limit:
                truncated = truncated or len(newest_first) < len(selected)
                break
        newest_first.reverse()
        return {
            "ok": True,
            "messages": newest_first,
            "chars": total,
            "truncated": truncated,
            "start_index": (
                newest_first[0]["index"] if newest_first else len(messages)),
            "total_messages": len(messages),
            "shown_messages": len(newest_first),
            "summary_chars": len(getattr(self.app.agent, "summary", "")),
        }

    def handle(self, req, writer):
        try:
            self._validate(req)
            path = req.path
            if req.method == "GET" and path in ("/", "/app.css", "/app.js", "/favicon.ico"):
                if path == "/":
                    writer.send_response(200, INDEX_HTML, "text/html; charset=utf-8")
                elif path == "/app.css":
                    writer.send_response(200, APP_CSS, "text/css; charset=utf-8")
                elif path == "/app.js":
                    writer.send_response(200, APP_JS, "application/javascript; charset=utf-8")
                else:
                    writer.send_response(204, b"", "text/plain")
                return
            if path == "/api/setup":
                if req.method == "GET":
                    writer.send_json(200, self._setup_view())
                    return
                self._setup(req, writer)
                return
            if path == "/api/login" and req.method == "POST":
                self._login(req, writer)
                return
            if not path.startswith("/api/"):
                raise _error("Not found", 404, "invalid")
            token = self._require(req)
            if path == "/api/logout" and req.method == "POST":
                self.auth.logout(token)
                writer.send_json(200, {"ok": True}, extra=[("Set-Cookie", expired_cookie_header())])
                return
            if path == "/api/status" and req.method == "GET":
                writer.send_json(200, self._status_view())
                return
            if path == "/api/live" and req.method == "GET":
                writer.send_json(200, self._live_view())
                return
            if path == "/api/history" and req.method == "GET":
                try:
                    limit = int(req.query.get("limit", self.app.config.get("web_history_limit", 50)))
                except ValueError:
                    limit = 50
                writer.send_json(200, self._history_view(limit))
                return
            if path == "/api/config":
                if req.method == "GET":
                    writer.send_json(200, self._config_view())
                else:
                    self._update_config(req, writer, token)
                return
            if path == "/api/config/ca" and req.method == "POST":
                self._update_ca(req, writer)
                return
            if path == "/api/mcp":
                if req.method == "GET":
                    writer.send_json(200, self._mcp_view())
                else:
                    self._update_mcp(req, writer)
                return
            if path == "/api/mcp/refresh" and req.method == "POST":
                self._refresh_mcp(writer)
                return
            if path == "/api/auto" and req.method == "POST":
                self._update_auto(req, writer)
                return
            if path == "/api/rewind" and req.method == "POST":
                self._rewind(req, writer)
                return
            if path == "/api/chat" and req.method == "POST":
                self._chat(req, writer)
                return
            if path == "/api/run" and req.method == "POST":
                self._run(req, writer)
                return
            if path == "/api/continue" and req.method == "POST":
                self._continue(req, writer)
                return
            if path == "/api/background/discard" and req.method == "POST":
                self._discard_background(req, writer)
                return
            if path == "/api/background/stop" and req.method == "POST":
                self._stop_background(req, writer)
                return
            if path == "/api/discard" and req.method == "POST":
                self._discard(writer)
                return
            if path == "/api/save-skill" and req.method == "POST":
                self._save_skill(writer)
                return
            if path == "/api/command" and req.method == "POST":
                self._command(req, writer)
                return
            if path == "/api/wifi/connect" and req.method == "POST":
                self._wifi(req, writer)
                return
            if path == "/api/test" and req.method == "POST":
                self._test(req, writer)
                return
            if path == "/api/factory-reset" and req.method == "POST":
                self._factory_reset(req, writer)
                return
            raise _error("Not found", 404, "invalid")
        except WebAuthError as exc:
            raise WebError(str(exc), exc.status, exc.code,
                           getattr(exc, "retry_after", None))

    def _login(self, req, writer):
        body = self._json(req)
        password = body.get("password", "")
        if not isinstance(password, str):
            raise _error("Password must be text", 400, "invalid")
        token = self.auth.login(password, req.peer_ip)
        status = self._status()
        writer.send_json(200, {
            "ok": True, "setup_required": False,
            "mode": status.get("mode", "off"),
            "ip": status.get("sta_ip", ""),
            "version": __version__,
        }, extra=[("Set-Cookie", self._cookie(token))])

    def _setup(self, req, writer):
        if self.auth.configured():
            raise _error("Setup is already complete", 409, "invalid")
        if not self._is_ap_peer(req):
            raise _error("Connect to the setup AP first", 403, "invalid")
        body = self._json(req)
        password = body.get("password", "")
        confirm = body.get("confirm", "")
        if not isinstance(password, str):
            raise _error("Password must be text", 400, "invalid")
        if password != confirm:
            raise _error("Passwords do not match", 400, "invalid")
        ssid = str(body.get("wifi_ssid", "") or "").strip()
        wifi_password = str(body.get("wifi_password", "") or "")
        provider = normalize_api_provider(body.get("api_provider", "openai"), "")
        if not provider:
            raise _error("API provider must be openai or anthropic", 400, "invalid")
        base_url = str(body.get("base_url", "") or "").strip()
        if not base_url:
            base_url = default_base_url(provider)
        model = str(body.get("model", "") or "").strip()
        api_key = str(body.get("api_key", "") or "")
        if len(ssid) > 32:
            raise _error("Wi-Fi SSID is too long", 400, "invalid")
        if len(wifi_password) > 63:
            raise _error("Wi-Fi password is too long", 400, "invalid")
        if base_url and not (
                base_url.startswith("http://") or base_url.startswith("https://")):
            raise _error("base_url must start with http:// or https://", 400, "invalid")
        salt, digest = self.auth.password_credentials(password)
        if not self.app.begin_operation("web-setup"):
            raise _error("Device is busy", 409, "busy")
        values = dict(self.app.config.values)
        secrets = dict(self.app.config.secrets)
        try:
            try:
                self.app.config.set_secret("web_password_salt", salt, save=False)
                self.app.config.set_secret("web_password_hash", digest, save=False)
                if ssid:
                    self.app.config.set_secret("wifi_ssid", ssid, save=False)
                    self.app.config.set_secret("wifi_password", wifi_password, save=False)
                self.app.config.set("api_provider", provider, save=False)
                if base_url:
                    self.app.config.set("base_url", base_url.rstrip("/"), save=False)
                if model:
                    self.app.config.set("model", model, save=False)
                if api_key:
                    self.app.config.set_secret("api_key", api_key, save=False)
                self.app.config.save_all()
                self.app.reload_llm()
            except Exception:
                self.app.config.values = values
                self.app.config.secrets = secrets
                raise
            token = self.auth.password_saved()
            if ssid:
                self.app.pending_wifi = (ssid, wifi_password)
            status = self._status()
            writer.send_json(200, {
                "ok": True,
                "setup_required": False,
                "wifi_connecting": bool(ssid),
                "mode": status.get("mode", "off"),
                "ip": status.get("sta_ip", ""),
                "version": __version__,
            }, extra=[("Set-Cookie", self._cookie(token))])
        finally:
            self.app.end_operation()

    def _update_config(self, req, writer, token):
        body = self._json(req)
        if "wifi_ssid" in body or "wifi_password" in body:
            raise _error("Use /api/wifi/connect for Wi-Fi changes", 400, "invalid")
        values = dict(self.app.config.values)
        secrets = dict(self.app.config.secrets)
        if not self.app.begin_operation("web-config"):
            raise _error("Device is busy", 409, "busy")
        relogin = False
        token_out = token
        saved = False
        model_changed = False
        try:
            if "api_provider" in body:
                provider = normalize_api_provider(body["api_provider"], "")
                if not provider:
                    raise _error("API provider must be openai or anthropic", 400, "invalid")
                previous = normalize_api_provider(
                    self.app.config.get("api_provider", "openai")
                )
                self.app.config.set("api_provider", provider, save=False)
                if provider != previous:
                    model_changed = True
                    known = (
                        default_base_url("openai"),
                        default_base_url("anthropic"),
                        "https://api.openai.com",
                        "https://api.anthropic.com",
                        "https://api.deepseek.com",
                        "https://api.deepseek.com/v1",
                    )
                    known = tuple(item.rstrip("/") for item in known)
                    current_base = str(
                        self.app.config.get("base_url") or ""
                    ).rstrip("/")
                    if "base_url" not in body and current_base in known:
                        self.app.config.set(
                            "base_url", default_base_url(provider), save=False
                        )
            for key in ("base_url", "model", "ca_file", "ntp_server",
                        "search_bing_url"):
                if key in body:
                    value = str(body[key]).strip()
                    if key in ("base_url", "search_bing_url") and value and not (
                            value.startswith("http://") or value.startswith("https://")):
                        raise _error("%s must start with http:// or https://" % key)
                    if key == "ntp_server" and not value:
                        raise _error("ntp_server cannot be empty", 400, "invalid")
                    if key == "ca_file" and value:
                        if not value.startswith("/") or not value.endswith(".pem") or ".." in value:
                            raise _error("CA path must be an absolute .pem path", 400, "invalid")
                    if key in ("base_url", "model", "ca_file"):
                        model_changed = True
                    self.app.config.set(key, value, save=False)
            if "search_provider" in body:
                provider = str(body["search_provider"] or "auto").strip().lower()
                if provider not in SEARCH_PROVIDERS:
                    raise _error(
                        "search_provider must be auto or bing",
                        400, "invalid",
                    )
                self.app.config.set("search_provider", provider, save=False)
            if "tls_verify" in body:
                self.app.config.set(
                    "tls_verify", _boolean(body, "tls_verify"), save=False
                )
                model_changed = True
            if "thinking_level" in body:
                level = normalize_thinking_level(body["thinking_level"])
                if level is None:
                    raise _error("Invalid thinking level", 400, "invalid")
                self.app.config.set("thinking_level", level, save=False)
            if "show_thinking" in body:
                self.app.config.set(
                    "show_thinking", _boolean(body, "show_thinking"), save=False
                )
            for key in ("temperature", "max_tokens"):
                if key in body:
                    try:
                        value = float(body[key]) if key == "temperature" else int(body[key])
                    except (TypeError, ValueError):
                        raise _error("Invalid %s" % key, 400, "invalid")
                    if value < 0 or (key == "max_tokens" and value < 1):
                        raise _error("Invalid %s" % key, 400, "invalid")
                    self.app.config.set(key, value, save=False)
            timeout_fields = (
                ("max_turns", 1, 50),
                ("stream_header_timeout", 1, 3600),
                ("stream_idle_timeout", 1, 3600),
                ("stream_event_idle_timeout", 0, 3600),
                ("stream_first_token_timeout", 0, 3600),
                ("web_history_limit", 1, 200),
                ("web_history_chars", 1024, 262144),
                ("request_timeout", 1, 3600),
                ("api_retries", 0, 10),
                ("api_retry_base_ms", 0, 300000),
                ("api_retry_max_ms", 0, 300000),
                ("api_retry_after_max_ms", 0, 3600000),
                ("api_retry_jitter_pct", 0, 100),
                ("ntp_sync_interval_s", 0, 86400),
                ("timezone_offset_minutes", -720, 840),
            )
            for key, minimum, maximum in timeout_fields:
                if key in body:
                    try:
                        value = int(body[key])
                    except (TypeError, ValueError):
                        raise _error("Invalid %s" % key, 400, "invalid")
                    if value < minimum or value > maximum:
                        raise _error("Invalid %s" % key, 400, "invalid")
                    self.app.config.set(key, value, save=False)
            if "native_tools" in body:
                self.app.config.set(
                    "native_tools", _boolean(body, "native_tools"), save=False
                )
                self.app.agent._native_tools_failed = False
                self.app.agent._system_tokens = None
                self.app.agent._system_tokens_at = None
            if "search_enabled" in body:
                self.app.config.set(
                    "search_enabled", _boolean(body, "search_enabled"), save=False
                )
            if "http_tool_enabled" in body:
                self.app.config.set(
                    "http_tool_enabled",
                    _boolean(body, "http_tool_enabled"), save=False,
                )
            if "http_tool_allow_http" in body:
                self.app.config.set(
                    "http_tool_allow_http",
                    _boolean(body, "http_tool_allow_http"), save=False,
                )
            if "persist_conversation" in body:
                self.app.config.set(
                    "persist_conversation",
                    _boolean(body, "persist_conversation"), save=False,
                )
            if "background_execution" in body:
                self.app.config.set(
                    "background_execution",
                    _boolean(body, "background_execution"), save=False,
                )
            if "background_max_jobs" in body:
                try:
                    count = int(body["background_max_jobs"])
                except (TypeError, ValueError):
                    raise _error("Invalid background_max_jobs", 400, "invalid")
                if count < 1 or count > 4:
                    raise _error("background_max_jobs must be 1-4",
                                 400, "invalid")
                self.app.config.set(
                    "background_max_jobs", count, save=False)
            mcp_changed = False
            if "mcp_enabled" in body:
                enabled = _boolean(body, "mcp_enabled")
                mcp_changed = enabled != bool(self.app.config.get("mcp_enabled", True))
                self.app.config.set("mcp_enabled", enabled, save=False)
            if "web_port" in body:
                try:
                    port = int(body["web_port"])
                except (TypeError, ValueError):
                    raise _error("Invalid web port", 400, "invalid")
                if port < 1 or port > 65535:
                    raise _error("Invalid web port", 400, "invalid")
                self.app.config.set("web_port", port, save=False)
            if "web_enabled" in body:
                self.app.config.set(
                    "web_enabled", _boolean(body, "web_enabled"), save=False
                )
            if _boolean(body, "api_key_clear", False):
                self.app.config.set_secret("api_key", "", save=False)
                model_changed = True
            elif body.get("api_key"):
                self.app.config.set_secret("api_key", str(body["api_key"]), save=False)
                model_changed = True
            if body.get("ap_password"):
                ap_password = str(body["ap_password"])
                if len(ap_password) < 8 or len(ap_password) > 63:
                    raise _error("AP password must be 8-63 characters", 400, "invalid")
                self.app.config.set_secret("ap_password", ap_password, save=False)
            new_password = body.get("web_password")
            if new_password:
                if not isinstance(new_password, str):
                    raise _error("New password must be text", 400, "invalid")
                if not body.get("current_password") or not self.auth.verify(
                        str(body.get("current_password", ""))):
                    raise _error("Current password is incorrect", 401, "auth")
                if new_password != body.get("confirm_password"):
                    raise _error("New passwords do not match", 400, "invalid")
                salt, digest = self.auth.password_credentials(new_password)
                self.app.config.set_secret("web_password_salt", salt, save=False)
                self.app.config.set_secret("web_password_hash", digest, save=False)
            self.app.config.save_all()
            saved = True
            self.app.refresh_background_limits()
            if model_changed:
                self.app.reload_llm()
            else:
                self.app.http.close()
            if mcp_changed and getattr(self.app, "mcp", None) is not None:
                self.app.mcp.reload()
            if new_password:
                token_out = self.auth.password_saved()
                relogin = False
            writer.send_json(200, {
                "ok": True, "relogin_required": relogin,
                "restart_required": "web_port" in body or "web_enabled" in body,
                "config": self._config_view(),
            }, extra=[("Set-Cookie", self._cookie(token_out))])
        except Exception:
            if not saved:
                self.app.config.values = values
                self.app.config.secrets = secrets
            raise
        finally:
            self.app.end_operation()

    def _update_ca(self, req, writer):
        body = self._json(req, limit=65536)
        pem = body.get("pem", "")
        if not isinstance(pem, str) or len(pem) > 65536:
            raise _error("CA PEM is too large", 413, "invalid")
        if ("-----BEGIN CERTIFICATE-----" not in pem
                or "-----END CERTIFICATE-----" not in pem):
            raise _error("Invalid PEM certificate", 400, "invalid")
        ca_file = self.app.config.get("ca_file", "/ca.pem")
        if not ca_file.startswith("/") or not ca_file.endswith(".pem") or ".." in ca_file:
            raise _error("Unsafe CA path", 400, "invalid")
        if not self.app.begin_operation("web-ca"):
            raise _error("Device is busy", 409, "busy")
        try:
            write_text(ca_file, pem)
            self.app.http.close()
            writer.send_json(200, {"ok": True, "ca_file": ca_file, "chars": len(pem)})
        finally:
            self.app.end_operation()

    def _update_auto(self, req, writer):
        body = self._json(req)
        run = _boolean(body, "auto_run", False)
        feedback = _boolean(body, "auto_feedback", True)
        if run and body.get("confirm") is not True:
            raise _error("Auto mode requires confirm=true", 400, "invalid")
        if not self.app.begin_operation("web-auto"):
            raise _error("Device is busy", 409, "busy")
        try:
            self.app.config.set("auto_run", run, save=False)
            self.app.config.set("auto_feedback", feedback, save=False)
            self.app.config.save()
            writer.send_json(200, {
                "ok": True, "auto_run": run,
                "auto_feedback": feedback,
                "warning": "Auto mode executes generated code with full device permissions."
                if run else "",
            })
        finally:
            self.app.end_operation()

    def _mcp_view(self):
        manager = getattr(self.app, "mcp", None)
        if manager is None:
            return {
                "ok": True, "enabled": False, "loaded": False,
                "servers": [], "tools": 0, "tool_names": [], "errors": {},
            }
        status = manager.status()
        status["ok"] = True
        return status

    def _update_mcp(self, req, writer):
        body = self._json(req, limit=65536)
        servers = body.get("servers")
        if not isinstance(servers, list):
            raise _error("servers must be a list", 400, "invalid")
        if (any(isinstance(item, dict) and item.get("auto") for item in servers)
                and body.get("confirm") is not True):
            raise _error("MCP auto mode requires confirm=true", 400, "invalid")
        store = getattr(self.app, "mcp_store", None)
        manager = getattr(self.app, "mcp", None)
        if store is None or manager is None:
            raise _error("MCP is unavailable", 503, "internal")
        if not self.app.begin_operation("web-mcp"):
            raise _error("Device is busy", 409, "busy")
        try:
            validated = store.merge(servers)
            manager.reload()
            writer.send_json(200, {
                "ok": True,
                "servers": store.public(validated),
                "refresh_required": True,
            })
        except Exception as exc:
            raise _error(str(exc), 400, "invalid")
        finally:
            self.app.end_operation()

    def _refresh_mcp(self, writer):
        manager = getattr(self.app, "mcp", None)
        if manager is None:
            raise _error("MCP is unavailable", 503, "internal")
        if not self.app.begin_operation("web-mcp-refresh"):
            raise _error("Device is busy", 409, "busy")
        try:
            status = manager.refresh()
            status["ok"] = True
            writer.send_json(200, status)
        except Exception as exc:
            raise _error("MCP refresh failed: %s" % exc, 500, "internal")
        finally:
            self.app.end_operation()

    def _factory_reset(self, req, writer):
        body = self._json(req)
        if body.get("confirm") is not True:
            raise _error("Factory reset requires confirm=true", 400, "invalid")
        password = body.get("password", "")
        if not isinstance(password, str) or not password:
            raise _error("Current password is required", 400, "invalid")
        if not self.auth.verify(password):
            raise _error("Current password is incorrect", 401, "auth")
        if self.app.background_active():
            raise _error(
                "Stop background jobs before factory reset", 409, "background")
        if not self.app.begin_operation("web-factory-reset"):
            raise _error("Device is busy", 409, "busy")
        try:
            deleted = self.app.run_on_main(self.app.factory_reset_data)
            try:
                writer.send_json(200, {
                    "ok": True, "deleted": deleted, "reboot": True,
                    "message": "User data deleted; the device is rebooting.",
                })
            finally:
                self.app.request_reboot()
        finally:
            self.app.end_operation()

    def _discard(self, writer):
        if not self.app.begin_operation("web-discard"):
            raise _error("Device is busy", 409, "busy")
        try:
            agent = self.app.agent
            had = agent.has_pending()
            had_background = False
            if not had and not self.app.background_active():
                try:
                    had_background = self.app.discard_background(record=True)
                except Exception:
                    # Multiple finished jobs require an explicit id; the
                    # dedicated background cards handle that path.
                    had_background = False
            agent.discard()
            writer.send_json(200, {
                "ok": True, "had_pending": had,
                "had_background": bool(had_background),
            })
        finally:
            self.app.end_operation()

    def _save_skill(self, writer):
        if not self.app.begin_operation("web-save-skill"):
            raise _error("Device is busy", 409, "busy")
        try:
            metadata = self.app.agent.commit_pending_skill()
            writer.send_json(200, {
                "ok": True, "name": metadata.get("name", ""),
                "chars": metadata.get("chars", 0),
            })
        except Exception as exc:
            raise _error(str(exc), 400, "invalid")
        finally:
            self.app.end_operation()

    def _command_rejection(self, line):
        """Return a reason when a serial command has no safe one-shot web form.

        All non-interactive commands are accepted here, including file writes,
        search/HTTP calls and /run or /workspace run (confirmed by the command
        sink and executed through the main-thread handoff). Commands that need
        readline input or reboot the board must use their dedicated web action
        (or the serial console), because a one-shot web request cannot supply
        a second prompt or survive a reboot.
        """
        parts = line.split()
        if not parts:
            return "Command is empty"
        command = parts[0].lower()
        args = parts[1:]
        known = (
            "/help", "/status", "/history", "/context", "/summary", "/code",
            "/discard", "/undo", "/rewind", "/skills", "/skill-show",
            "/skill-delete", "/thinking", "/auto", "/native", "/search",
            "/test", "/compact", "/clear", "/memory", "/workspace",
            "/mcp", "/http", "/background", "/bg", "/web",
            "/setup", "/wifi", "/model", "/ca", "/repl", "/factory-reset",
            "/run", "/continue",
        )
        if command not in known:
            return "Unknown command. Type /help for the command list."
        if command in ("/setup", "/wifi", "/model", "/ca"):
            return (
                "Use the web settings form for %s; this command needs "
                "interactive input" % command
            )
        if command == "/repl":
            return (
                "Use the serial console for /repl; it restarts the device into "
                "maintenance mode"
            )
        if command == "/factory-reset":
            return (
                "Use the web factory-reset form; it requires the admin password"
            )
        if command == "/web" and args:
            return "Use the web settings form to reset the admin password"
        return ""

    def _command_allowed(self, line):
        return not self._command_rejection(line)

    @staticmethod
    def _command_needs_confirmation(line):
        parts = line.split()
        if not parts:
            return False
        command = parts[0].lower()
        args = [part.lower() for part in parts[1:]]
        if command == "/auto":
            return (
                (len(args) == 1 and args[0] == "on")
                or (len(args) == 2 and args[0] == "run" and args[1] == "on")
            )
        if command == "/mcp" and len(args) >= 3:
            return args[0] == "auto" and args[2] == "on"
        return False

    @staticmethod
    def _command_operation_source(line):
        parts = line.split()
        command = parts[0].lower() if parts else ""
        args = [part.lower() for part in parts[1:]]
        if command == "/run":
            return "web-run"
        if command == "/continue":
            return "web-continue"
        if command == "/workspace" and args and args[0] == "run":
            return "web-run"
        return "web-command"

    def _command(self, req, writer):
        body = self._json(req)
        line = str(body.get("line", "")).strip()
        confirm = _boolean(body, "confirm", False)
        if not line.startswith("/"):
            raise _error("Command must start with /", 400, "invalid")
        if len(line) > int(self.app.config.get("web_command_limit", 4096)):
            raise _error("Command is too long", 413, "invalid")
        rejection = self._command_rejection(line)
        if rejection:
            raise _error(rejection, 400, "invalid")
        if self._command_needs_confirmation(line) and not confirm:
            raise _error(
                "This command changes a risky setting; confirm it in the web UI",
                400, "confirm")
        sink = CommandCaptureSink(
            int(self.app.config.get("web_output_limit", 16384)),
            confirmed=confirm,
        )
        exclusive = self.app.is_exclusive_command(line)
        acquired = False
        if exclusive:
            acquired = self.app.begin_operation(
                self._command_operation_source(line))
            if not acquired:
                raise _error("Device is busy", 409, "busy")
        self.app.ui.bind(sink)
        try:
            self.app.command(line)
        except WebCommandConfirm as exc:
            writer.send_json(400, {"ok": False, "error": str(exc),
                                   "code": "confirm",
                                   "output": sink.getvalue()})
            return
        except WebCommandInteractive as exc:
            writer.send_json(400, {"ok": False, "error": str(exc),
                                   "code": "interactive",
                                   "output": sink.getvalue()})
            return
        except Exception as exc:
            writer.send_json(200, {"ok": False, "error": str(exc),
                                   "output": sink.getvalue()})
            return
        finally:
            self.app.ui.unbind()
            if acquired:
                self.app.end_operation()
        writer.send_json(200, {"ok": True, "output": sink.getvalue(),
                               "truncated": sink.closed})

    def _wifi(self, req, writer):
        body = self._json(req)
        ssid = str(body.get("ssid", "") or "").strip()
        if not ssid:
            raise _error("Wi-Fi SSID cannot be empty", 400, "invalid")
        if len(ssid) > 32:
            raise _error("Wi-Fi SSID is too long", 400, "invalid")
        password = str(body.get("password", "") or "")
        clear_password = _boolean(body, "clear_password", False)
        if password == "-" or clear_password:
            password = ""
        elif not password:
            if self.app.config.secret("wifi_ssid") == ssid:
                password = self.app.config.secret("wifi_password")
            else:
                password = ""
        if len(password) > 64:
            raise _error("Wi-Fi password is too long", 400, "invalid")
        if not self.app.begin_operation("web-wifi"):
            raise _error("Device is busy", 409, "busy")
        try:
            try:
                self.app.start_setup_ap()
            except Exception as exc:
                raise _error("Could not start recovery AP: %s" % exc, 503, "internal")
            self.app.config.set_secret("wifi_ssid", ssid, save=False)
            self.app.config.set_secret("wifi_password", password, save=False)
            self.app.config.save_secrets()
            self.app.http.close()
            self.app.pending_wifi = (ssid, password)
            status = self._status()
            writer.send_json(200, {
                "ok": True, "connecting": True,
                "ap_ssid": status.get("ap_ssid", ""),
                "ap_ip": status.get("ap_ip", ""),
            })
        finally:
            self.app.end_operation()

    def _test(self, req, writer):
        body = self._json(req)
        kind = body.get("kind", "wifi")
        if kind not in ("wifi", "api"):
            raise _error("Invalid test kind", 400, "invalid")
        if not self.app.begin_operation("web-test"):
            raise _error("Device is busy", 409, "busy")
        try:
            try:
                if kind == "wifi":
                    self.app.prepare_network()
                    result = {"ok": True}
                else:
                    self.app.prepare_cloud()
                    messages = [{"role": "user", "content": "Reply with OK only."}]
                    if self.app.config.get("stream", True):
                        response = self.app.agent._retry_api_call(
                            "Test API request",
                            lambda: self.app.llm.complete_stream(messages),
                        )
                    else:
                        response = self.app.agent._retry_api_call(
                            "Test API request",
                            lambda: self.app.llm.complete(messages),
                        )
                    result = {
                        "ok": True, "chars": len(response or ""),
                        "streamed": bool(getattr(self.app.llm, "last_streamed", False)),
                    }
            except Exception as exc:
                raise _error("Test failed: %s" % exc, 503, "internal")
            writer.send_json(200, result)
        finally:
            self.app.end_operation()

    def _rewind(self, req, writer):
        body = self._json(req)
        agent = self.app.agent
        if self.app.background_active():
            raise _error("Background execution is still running",
                         409, "background")
        if not self.app.begin_operation("web-rewind"):
            raise _error("Device is busy", 409, "busy")
        try:
            try:
                self.app.discard_all_backgrounds(record=False)
                if "index" in body:
                    index = body["index"]
                    if (not isinstance(index, int)
                            or isinstance(index, bool)):
                        raise _error(
                            "index must be an integer", 400, "invalid")
                    removed = agent.rewind_to(index)
                else:
                    count = body.get("count", 1)
                    if (not isinstance(count, int)
                            or isinstance(count, bool) or count < 1):
                        raise _error(
                            "count must be a positive integer",
                            400, "invalid")
                    index = agent.find_user_index(count)
                    removed = agent.rewind_to(index)
            except AgentError as exc:
                raise _error(str(exc), 409, "rewind")
            writer.send_json(200, {
                "ok": True, "index": index, "removed": removed,
                "remaining": len(agent.history),
            })
        finally:
            self.app.end_operation()

    def _stop_background(self, req, writer):
        body = self._json(req)
        job_id = body.get("id")
        if not self.app.background_active():
            writer.send_json(200, {
                "ok": True, "stop_requested": False, "count": 0,
                "message": "No background execution is running.",
            })
            return
        if job_id is not None:
            job = self.app.background_get(job_id)
            if job is None:
                raise _error("No background job #%s" % job_id,
                             404, "background")
            if job.get("phase") not in ("queued", "running"):
                writer.send_json(200, {
                    "ok": True, "stop_requested": False, "count": 0,
                    "message": "Background job #%s already finished." % job_id,
                })
                return
        requested = self.app.stop_background(
            "requested from web", job_id=job_id)
        writer.send_json(200, {
            "ok": True, "stop_requested": bool(requested),
            "count": int(requested or 0),
            "message": (
                "Stop requested; it is applied at the next cooperative checkpoint."
                if requested else "Could not request a stop."),
        })

    def _discard_background(self, req, writer):
        body = self._json(req)
        job_id = body.get("id")
        all_finished = bool(body.get("all"))
        if not self.app.begin_operation("web-background-discard"):
            raise _error("Device is busy", 409, "busy")
        try:
            try:
                if all_finished:
                    count = self.app.discard_all_backgrounds(record=True)
                    writer.send_json(200, {
                        "ok": True, "discarded": count,
                    })
                    return
                discarded = self.app.discard_background(
                    record=True, job_id=job_id)
                writer.send_json(200, {
                    "ok": True, "discarded": bool(discarded),
                })
            except Exception as exc:
                raise _error(str(exc), 409, "background")
        finally:
            self.app.end_operation()

    def _continue(self, req, writer):
        body = self._json(req)
        agent = self.app.agent
        if agent.has_pending():
            raise _error(
                "Resolve the pending code or skill before continuing the "
                "background task",
                409, "pending",
            )
        if getattr(agent, "pending_question", None) is not None:
            raise _error(
                "Answer or discard the pending question before continuing "
                "the background task",
                409, "pending",
            )
        job_id = body.get("id")
        if job_id is not None:
            job = self.app.background_get(job_id)
        else:
            ready = self.app.background_ready_jobs()
            job = ready[0] if ready else None
        if job is None:
            raise _error("No background result to continue",
                         409, "background")
        if job.get("phase") != "ready":
            raise _error("Background job #%s is not finished (phase=%s)" % (
                job.get("id", "?"), job.get("phase", "?")),
                         409, "background")
        sink = WebTurnSink(writer, self.app)
        if not self.app.begin_operation("web-continue"):
            raise _error("Device is busy", 409, "busy")
        if not self._start_stream(writer):
            self.app.end_operation()
            return
        self.app.ui.bind(sink)
        try:
            sink.start()
            try:
                job = self.app.take_background_result(job.get("id"))
            except Exception as exc:
                sink.error(str(exc), "background")
                return
            result = job.get("result")
            if result is None:
                sink.error("The background result is unavailable",
                           "background")
                return
            auto = bool(job.get("auto"))
            sink.execution(result, auto)
            if job.get("recovered"):
                sink.notice(
                    "Recovered code executed; automatic feedback is skipped.")
                sink.done(agent.pending_kind(), agent.turns)
                return
            if not self.app.config.get("auto_feedback", True):
                sink.notice(
                    "Automatic feedback is disabled; send a message to "
                    "continue the conversation.")
                sink.done(agent.pending_kind(), agent.turns)
                return
            if not agent.can_continue():
                sink.notice(
                    "Maximum automatic feedback turns reached. "
                    "Raise max_turns in settings or use /auto turns N.")
                sink.done(agent.pending_kind(), agent.turns)
                return
            try:
                self.app.prepare_cloud()
            except Exception as exc:
                sink.notice("Automatic feedback skipped: %s" % exc)
                sink.done(agent.pending_kind(), agent.turns)
                return
            sink.feedback(agent.turns + 1)
            try:
                explanation, has_code = agent.continue_feedback()
            except LLMStreamCancelled:
                raise
            except BaseException as exc:
                sink.error(
                    "Automatic feedback failed: %s. Send a new message to continue."
                    % exc,
                    "model",
                )
                return
            if self._respond(sink, explanation, has_code, auto) is True:
                self._execute_loop(sink, auto)
            sink.done(agent.pending_kind(), agent.turns)
        except LLMStreamCancelled:
            pass
        except BaseException as exc:
            try:
                sink.error(str(exc), "internal")
            except LLMStreamCancelled:
                pass
        finally:
            self.app.ui.unbind()
            self.app.end_operation()
            sink.stop()
            writer.close()

    def _chat(self, req, writer):
        body = self._json(req)
        text = str(body.get("text", body.get("message", ""))).strip()
        agent = self.app.agent
        if not text:
            raise _error("Message is empty", 400, "invalid")
        if len(text) > int(self.app.config.get("input_limit", 2000)):
            raise _error("Message exceeds input_limit", 413, "invalid")
        if agent.has_pending():
            raise _error("Pending item must be run, saved or discarded first",
                         409, "pending")
        sink = WebTurnSink(writer, self.app)
        if not self.app.begin_operation("web-chat"):
            raise _error("Device is busy", 409, "busy")
        try:
            try:
                self.app.prepare_for_new_turn()
            except Exception as exc:
                raise _error(str(exc), 409, "background")
            self.app.prepare_cloud()
        except WebError:
            self.app.end_operation()
            raise
        except Exception as exc:
            self.app.end_operation()
            raise _error("Preparation failed: %s" % exc, 503, "internal")
        if not self._start_stream(writer):
            self.app.end_operation()
            return
        self.app.ui.bind(sink)
        try:
            sink.start()
            if sink.closed:
                return
            explanation, has_code = agent.ask(text)
            auto = bool(self.app.config.get("auto_run", False))
            if self._respond(sink, explanation, has_code, auto) is True:
                self._execute_loop(sink, auto)
            sink.done(
                agent.pending_kind(), agent.turns,
                getattr(agent, "last_user_index", None))
        except LLMStreamCancelled:
            pass
        except BaseException as exc:
            try:
                sink.error(str(exc), "model")
            except LLMStreamCancelled:
                pass
        finally:
            self.app.ui.unbind()
            self.app.end_operation()
            sink.stop()
            writer.close()

    def _run(self, req, writer):
        body = self._json(req)
        agent = self.app.agent
        if agent.pending_source is None:
            raise _error("No pending code", 409, "invalid")
        background_enabled = bool(
            self.app.config.get("background_execution", True))
        mode = str(body.get("mode", "") or "").lower()
        if not mode:
            mode = "background" if background_enabled else "foreground"
        if mode not in ("background", "foreground"):
            raise _error("mode must be background or foreground",
                         400, "invalid")
        if mode == "background" and not background_enabled:
            raise _error("Background execution is disabled",
                         409, "background")
        note = str(body.get("note", "") or "")[:200]
        sink = WebTurnSink(writer, self.app)
        if not self.app.begin_operation("web-run"):
            raise _error("Device is busy", 409, "busy")
        if not self._start_stream(writer):
            self.app.end_operation()
            return
        self.app.ui.bind(sink)
        try:
            sink.start()
            if mode == "background":
                try:
                    job = self.app.start_background(
                        auto=False, driver="web", note=note)
                except Exception as exc:
                    sink.error(
                        "Could not start background execution: %s" % exc,
                        "background",
                    )
                    return
                sink.background(job, phase=job.get("phase", "queued"))
            else:
                self._execute_loop(sink, False)
            sink.done(agent.pending_kind(), agent.turns)
        except LLMStreamCancelled:
            pass
        except BaseException as exc:
            try:
                sink.error(str(exc), "internal")
            except LLMStreamCancelled:
                pass
        finally:
            self.app.ui.unbind()
            self.app.end_operation()
            sink.stop()
            writer.close()

    def _respond(self, sink, explanation, has_code, auto):
        sink.response(explanation, has_code, bool(self.app.agent.last_streamed))
        agent = self.app.agent
        question = getattr(agent, "pending_question", None)
        if question is not None:
            sink.question(question)
            return False
        if agent.pending_skill is not None:
            sink.skill(agent.pending_skill)
            return False
        if not has_code or agent.pending_source is None:
            return False
        source = agent.pending_source
        safe = auto_source_is_safe(source)
        timeout = agent.pending_timeout
        if timeout is None:
            timeout = self.app.config.get("code_timeout_s", 20)
        if auto and safe:
            if self.app.config.get("background_execution", True):
                try:
                    job = self.app.start_background(auto=True, driver="web")
                except Exception as exc:
                    sink.notice(
                        "Background start failed (%s); the code remains "
                        "pending for manual review." % exc)
                    sink.code(source, False, safe, timeout)
                    return False
                sink.code(source, True, True, timeout, background=True)
                sink.background(job, phase=job.get("phase", "queued"))
                return "background"
            sink.code(source, True, True, timeout)
            return True
        sink.code(source, False, safe, timeout)
        return False

    def _execute_loop(self, sink, auto):
        agent = self.app.agent
        while True:
            if sink.closed:
                return
            try:
                result = self.app.run_on_main(
                    lambda: agent.execute_pending(self.app.executor)
                )
            except BaseException as exc:
                sink.error("Execution handoff failed: %s" % exc, "internal")
                return
            sink.execution(result, auto)
            if agent.recovered_execution:
                sink.notice("Recovered code executed; automatic feedback is skipped.")
                return
            if not self.app.config.get("auto_feedback", True):
                return
            if not agent.can_continue():
                sink.notice(
                    "Maximum automatic feedback turns reached. "
                    "Raise max_turns in settings or use /auto turns N."
                )
                return
            if sink.closed:
                return
            try:
                self.app.prepare_cloud()
            except Exception as exc:
                sink.notice("Automatic feedback skipped: %s" % exc)
                return
            sink.feedback(agent.turns + 1)
            try:
                explanation, has_code = agent.continue_feedback()
            except BaseException as exc:
                sink.error(
                    "Automatic feedback failed: %s. Send a new message to continue."
                    % exc,
                    "model",
                )
                return
            if self._respond(sink, explanation, has_code, auto) is not True:
                return
