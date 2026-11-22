"""Run on the board: mpremote connect <PORT> run tools/device_smoke.py"""

import builtins
import gc
import os
import socket
import sys
import time

try:
    import _thread
except ImportError:
    _thread = None

from harness.agent import Agent, parse_response
from harness.config import Config
from harness.console import Application, SerialUI, factory_reset_files
from harness.conversation import ConversationStore
from harness.device import Board
from harness.executor import Executor, execution_runtime
from harness.http_client import decode_chunked, split_url
from harness.http_tool import HttpToolError, is_public_ipv4, validate_url
from harness.llm import OpenAIClient, _network_retryable, llm_error, normalize_thinking_level
from harness.llm_anthropic import AnthropicClient
from harness.mcp import MCPStore
from harness.memory import MemoryStore
from harness.recovery import Recovery
from harness.skills import SkillStore
from harness.web import WebError, WebServer, read_request, unquote
from harness import web as web_module
from harness.webauth import WebAuth, cookie_header, parse_cookie
from harness.webui import APP_CSS, APP_JS, INDEX_HTML
from harness.workspace import Workspace


class FakeLLM:
    def __init__(self):
        self.responses = ["```python\nprint(6 * 7)\n```", "Done"]

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False):
        return self.responses.pop(0)


class DeleteSkillLLM:
    def __init__(self):
        self.responses = [
            '```tool\n{"name":"__skill","args":{"action":"delete","name":"_smoke"}}\n```',
            "Deleted",
        ]

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False):
        return self.responses.pop(0)


class FallbackLLM:
    def __init__(self):
        self.calls = []

    def complete_stream(self, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, thinking_level=None):
        self.calls.append((thinking_level, max_tokens))
        if len(self.calls) == 1:
            raise llm_error("SSE idle timeout", retryable=True)
        return "OK"


class TruncationLLM:
    def __init__(self):
        self.calls = []

    def complete_stream(self, messages, on_reasoning=None, on_content=None,
                        max_tokens=None, thinking_level=None):
        self.calls.append((thinking_level, max_tokens))
        if len(self.calls) == 1:
            raise RuntimeError("Model output was truncated at max_tokens=4096")
        return "OK"


class FallbackConfig:
    def __init__(self):
        self.values = {
            "stream": True,
            "thinking_level": "high",
            "thinking_fallback": "low",
            "max_tokens": 4096,
            "truncation_retry_max_tokens": 8192,
            "api_retries": 3,
            "api_retry_base_ms": 0,
            "api_retry_max_ms": 0,
            "api_retry_jitter_pct": 0,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


class RetryConfig:
    def __init__(self):
        self.values = {
            "stream": False,
            "thinking_level": "disabled",
            "thinking_fallback": "",
            "max_tokens": 4096,
            "truncation_retry_max_tokens": 8192,
            "api_retries": 2,
            "api_retry_base_ms": 0,
            "api_retry_max_ms": 0,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


class RetryOnceLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, max_tokens=None, thinking_level=None,
                 allow_truncated=False):
        self.calls += 1
        if self.calls == 1:
            raise llm_error(
                "Model API HTTP 503: busy", status=503, retryable=True
            )
        return "OK"


class AuthConfig:
    def __init__(self):
        self.values = {
            "web_max_sessions": 4,
            "web_session_ttl_s": 3600,
            "web_login_fail_limit": 3,
            "web_login_lock_ms": 1000,
        }
        self.secrets = {"web_password_hash": "", "web_password_salt": ""}

    def get(self, name, default=None):
        return self.values.get(name, default)

    def secret(self, name, default=""):
        return self.secrets.get(name, default)

    def set_secret(self, name, value, save=True):
        self.secrets[name] = value

    def save_secrets(self):
        pass


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def smoke_factory_reset():
    root = "/_factory_smoke"
    try:
        for name in os.listdir(root):
            path = root + "/" + name
            try:
                os.remove(path)
            except OSError:
                for child in os.listdir(path):
                    os.remove(path + "/" + child)
                os.rmdir(path)
        os.rmdir(root)
    except OSError:
        pass
    try:
        os.mkdir(root)
    except OSError:
        pass

    def write(name):
        with open(root + "/" + name, "w") as handle:
            handle.write("x")

    for name in (
        "config.json", "secrets.json", "mcp.json", "MEMORY.md",
        "recovery.json", "recovery.py", "maintenance.flag",
        "boot.py", "main.py", "ca.pem",
    ):
        write(name)
    for name in ("skills", "tools", "workspace", "state"):
        os.mkdir(root + "/" + name)
        write(name + "/entry.txt")

    removed = factory_reset_files(root)
    for name in (
        "config.json", "secrets.json", "mcp.json", "MEMORY.md",
        "recovery.json", "recovery.py", "maintenance.flag",
    ):
        assert not _exists(root + "/" + name), name
    for name in ("skills", "tools", "workspace", "state"):
        assert not _exists(root + "/" + name), name
    for name in ("boot.py", "main.py", "ca.pem"):
        assert _exists(root + "/" + name), name
    assert "/config.json" in removed and "/skills" in removed

    config = Config(root)
    config.values["model"] = "demo"
    config.secrets["api_key"] = "secret"
    config.reset_memory()
    assert config.get("model") == "" and config.secret("api_key") == ""

    for name in ("boot.py", "main.py", "ca.pem"):
        os.remove(root + "/" + name)
    os.rmdir(root)


def smoke_execution_timeout(board):
    result = Executor(board, timeout_s=1).execute(
        "for i in range(10 ** 9):\n    pass")
    assert result.timed_out, result.error
    assert "ExecutionTimeout" in result.error
    result = Executor(board, timeout_s=1).execute(
        "while True:\n    pass")
    assert result.timed_out, result.error


def smoke_conversation():
    config = Config()
    config.values["conversation_file"] = "/_smoke_conversation.json"
    store = ConversationStore("/", config)
    store.save([
        {"role": "user", "content": "smoke user"},
        {"role": "assistant", "content": "smoke answer"},
    ], "smoke summary")
    state = store.load()
    assert state["messages"][0]["content"] == "smoke user"
    assert state["summary"] == "smoke summary"
    store.clear()
    assert not store.exists()


def smoke_mcp_store():
    config = Config()
    config.values["mcp_file"] = "/_smoke_mcp.json"
    store = MCPStore("/", config)
    saved = store.save([{
        "name": "smoke",
        "url": "https://example.com/mcp",
        "headers": {"X-Key": "smoke-secret"},
    }])
    public = store.public(saved)
    assert public[0]["headers_set"]
    assert "smoke-secret" not in str(public)
    os.remove(store.path)


def smoke_native_hardware(board):
    result = Executor(board).execute(
        "import machine\n"
        "print(machine.freq() > 0)\n"
        "print(len(machine.unique_id()) > 0)")
    assert result.ok, result.output
    assert result.output.count("True") >= 2, result.output


class TimeoutConn:
    def settimeout(self, _value):
        pass

    def recv(self, _size):
        raise OSError(11, "try again")


def smoke_request_timeout():
    original_ticks = web_module._ticks_ms
    times = [0, 0, 31000]
    web_module._ticks_ms = lambda: times.pop(0)
    try:
        try:
            read_request(TimeoutConn(), "127.0.0.1", timeout=30)
        except WebError as exc:
            assert exc.status == 408, exc.status
            assert exc.code == "timeout", exc.code
        else:
            raise AssertionError("request total timeout did not fire")
    finally:
        web_module._ticks_ms = original_ticks


class WebSmokeApp:
    def __init__(self):
        self.ticks = 0
        self.notices = []

    def body_limit(self):
        return 1024

    def tick(self):
        self.ticks += 1

    def queue_notice(self, text):
        self.notices.append(text)

    def handle(self, request, writer):
        if request.path == "/smoke":
            writer.send_json(200, {"ok": True, "path": request.path})
        elif request.path == "/stream":
            writer.start_stream()
            writer.event({"t": "smoke"})
            writer.close()
        else:
            writer.send_response(200, "ok")


def http_get(port, path):
    client = socket.socket()
    client.settimeout(3)
    try:
        client.connect(("127.0.0.1", port))
        client.send(
            ("GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n" % path).encode()
        )
        chunks = []
        while True:
            chunk = client.recv(512)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        client.close()


def smoke_web():
    assert unquote("a+b%E4%B8%AD") == "a b中"
    auth_config = AuthConfig()
    auth = WebAuth(auth_config, sleep_fn=lambda _ms: None)
    token = auth.set_password("smoke-password")
    assert auth.verify("smoke-password")
    assert auth.valid(token)
    assert "smoke-password" not in str(auth_config.secrets)
    assert parse_cookie(cookie_header(token, 60)) == token
    assert parse_cookie("harness_session=not-a-token") is None
    login_token = auth.login("smoke-password", "192.168.4.2")
    assert auth.valid(login_token)
    auth.logout(login_token)
    assert not auth.valid(login_token)
    auth.clear_password()
    assert not auth.configured()

    assert "ESP32 Harness" in INDEX_HTML
    assert "function sendChat" in APP_JS
    assert "--accent" in APP_CSS

    app = WebSmokeApp()
    server = WebServer(app, host="127.0.0.1", port=18080)
    assert server.start(), server.error
    try:
        response = http_get(server.port, "/smoke")
        assert b"200 OK" in response, response
        assert b'"ok"' in response and b"true" in response
        assert b"Content-Security-Policy" in response
        stream = http_get(server.port, "/stream")
        assert b"200 OK" in stream, stream
        assert b'"t"' in stream and b"smoke" in stream
        assert app.ticks >= 2
    finally:
        server.stop()
    assert not server.alive


class HandoffApp(Application):
    def __init__(self):
        self.main_thread_id = _thread.get_ident()
        self.main_lock = _thread.allocate_lock()
        self.main_request = None
        self.execution_active = False


def smoke_handoff(board):
    assert _thread is not None
    app = HandoffApp()
    executor = Executor(board, recovery=Recovery())
    executor.main_runner = app
    results = []
    errors = []

    def worker():
        try:
            results.append(executor.execute("print('handoff-ok')"))
        except Exception as exc:
            errors.append(exc)

    _thread.start_new_thread(worker, ())
    for _ in range(500):
        app.pump_main_request()
        if results or errors:
            break
        time.sleep_ms(10)
    assert results and not errors, errors
    assert results[0].ok, results[0].error
    assert "handoff-ok" in results[0].output
    assert not app.execution_active


def smoke_background_threads(board):
    """Two worker executions plus isolated print/time/check state."""
    assert _thread is not None
    original_print = builtins.print
    results = []
    errors = []

    def worker(name):
        try:
            result = Executor(
                board, isolated_output=True, cancel_check=lambda: False
            ).execute(
                "import time\n"
                "for i in range(3):\n"
                "    print('%s', i)\n"
                "    time.sleep_ms(10)\n" % name)
            results.append((name, result))
        except BaseException as exc:
            errors.append(exc)

    try:
        _thread.stack_size(32 * 1024)
    except Exception:
        pass
    _thread.start_new_thread(worker, ("worker-a",))
    _thread.start_new_thread(worker, ("worker-b",))
    for _ in range(500):
        if len(results) + len(errors) >= 2:
            break
        time.sleep_ms(10)
    assert len(results) == 2 and not errors, (results, errors)
    by_name = {}
    for name, result in results:
        assert result.ok, result.error
        by_name[name] = result.output
    assert "worker-a" in by_name["worker-a"]
    assert "worker-b" not in by_name["worker-a"]
    assert "worker-b" in by_name["worker-b"]
    assert "worker-a" not in by_name["worker-b"]
    assert not hasattr(builtins, "__harness_check")
    assert builtins.print is original_print
    restored_time = sys.modules.get("time")
    assert restored_time is None or restored_time is time
    runtime = execution_runtime()
    assert runtime.installed == 0
    assert not runtime.stacks


def run():
    config = Config()
    board = Board()
    client = OpenAIClient(config)
    assert client.session_id.startswith("ses_")
    assert client.new_session().startswith("ses_")
    original_base_url = config.get("base_url")
    anthropic = AnthropicClient(config)
    config.values["base_url"] = "https://api.anthropic.com"
    assert anthropic._endpoint() == "https://api.anthropic.com/v1/messages"
    config.values["base_url"] = original_base_url
    assert normalize_thinking_level("middle") == "high"
    assert normalize_thinking_level("max") == "max"
    assert normalize_thinking_level("bogus") is None
    SerialUI()
    assert split_url("https://example.com/v1")[2] == 443
    assert decode_chunked(b"2\r\nOK\r\n0\r\n\r\n", 10) == b"OK"
    assert is_public_ipv4("1.1.1.1")
    assert not is_public_ipv4("192.168.1.1")
    original_allow_http = config.get("http_tool_allow_http", False)
    config.values["http_tool_allow_http"] = False
    validate_url("https://1.1.1.1/", config)
    for blocked in ("https://192.168.1.1/", "http://1.1.1.1/"):
        try:
            validate_url(blocked, config)
            raise AssertionError("expected blocked URL: " + blocked)
        except HttpToolError:
            pass
    config.values["http_tool_allow_http"] = original_allow_http
    smoke_request_timeout()
    retry_error = llm_error("busy", status=503, retryable=True, retry_after=2)
    assert retry_error.retryable and retry_error.status == 503
    assert retry_error.retry_after == 2
    assert _network_retryable(OSError(110, "timed out"))
    assert not _network_retryable(OSError("certificate verify failed"))
    explanation, source = parse_response("Test\n```python\nprint(1)\n```")
    assert explanation == "Test"
    assert source == "print(1)"

    recovery = Recovery()
    result = Executor(board, recovery=recovery).execute("print('smoke-ok')\nprint(device.info()['implementation'])")
    assert result.ok, result.output
    assert "smoke-ok" in result.output
    assert "micropython" in result.output
    assert recovery.consume() is None
    smoke_native_hardware(board)
    smoke_web()
    smoke_handoff(board)
    smoke_background_threads(board)
    smoke_execution_timeout(board)
    smoke_conversation()
    smoke_mcp_store()

    store = SkillStore()
    store.save("_smoke", "smoke skill", "# Smoke\nbody")
    assert store.read_bundle("_smoke")["content"].find("body") >= 0
    delete_agent = Agent(config, DeleteSkillLLM(), board, skills=store)
    explanation, has_code = delete_agent.ask("Delete the _smoke skill")
    assert explanation == "Deleted" and not has_code
    assert not store.exists("_smoke")

    memory = MemoryStore()
    original_memory = memory.load()
    memory.append("_smoke memory note")
    replaced = memory.replace("_smoke memory note", "_smoke memory updated")
    assert replaced["after"] == "_smoke memory updated"
    assert memory.delete("_smoke memory updated")["removed"] == 1
    assert "_smoke memory updated" not in memory.load()
    assert original_memory.rstrip("\n") in memory.load()

    smoke_factory_reset()

    workspace = Workspace(clean_tmp=False)
    workspace.write("_smoke_run.py", "print('workspace-run-ok')")
    run_config = Config()
    run_config.set("auto_run", True, save=False)
    run_agent = Agent(
        run_config, FakeLLM(), board,
        workspace=workspace, executor=Executor(board),
    )
    run_result = run_agent._run_workspace_tool(
        {"action": "run", "path": "_smoke_run.py"}
    )
    assert run_result.ok, run_result.error
    assert "workspace-run-ok" in run_result.output
    run_config.set("auto_run", False, save=False)
    confirmations = []
    run_agent.on_workspace_run_confirm = (
        lambda path, source: confirmations.append((path, source)) or True
    )
    run_result = run_agent._run_workspace_tool(
        {"action": "run", "path": "_smoke_run.py"}
    )
    assert run_result.ok, run_result.error
    assert "workspace-run-ok" in run_result.output
    assert confirmations
    workspace.remove("_smoke_run.py")

    agent = Agent(config, FakeLLM(), board)
    _, has_code = agent.ask("Calculate")
    assert has_code
    agent_result = agent.execute_pending(Executor(board))
    assert agent_result.ok and "42" in agent_result.output
    explanation, has_code = agent.continue_feedback()
    assert explanation == "Done" and not has_code

    fallback_llm = FallbackLLM()
    fallback_agent = Agent(FallbackConfig(), fallback_llm, board)
    assert fallback_agent._complete([]) == "OK"
    assert fallback_llm.calls == [(None, None), (None, None)]

    truncation_llm = TruncationLLM()
    truncation_agent = Agent(FallbackConfig(), truncation_llm, board)
    assert truncation_agent._complete([]) == "OK"
    assert truncation_llm.calls == [(None, None), (None, 8192)]

    retry_llm = RetryOnceLLM()
    retry_agent = Agent(RetryConfig(), retry_llm, board)
    assert retry_agent._complete([]) == "OK"
    assert retry_llm.calls == 2

    gc.collect()
    print("DEVICE_SMOKE_OK")
    print("heap_free=%s" % gc.mem_free())
    print("board=%s" % board.info())


run()
