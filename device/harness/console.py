import gc
import json
import os
import select
import sys
import time

try:
    import _thread
except ImportError:
    _thread = None

from . import __version__
from .agent import (
    Agent,
    AgentError,
    SEARCH_PROVIDERS,
    auto_source_is_safe,
    format_question,
)
from .background import BackgroundPool
from .config import Config, join_path, write_text
from .conversation import BackgroundStore, ConversationStore
from .device import Board, Network
from .executor import ExecutionResult, Executor
from .http_client import HttpClient
from .hardware import (
    HardwareAPI,
    HardwareManager,
    PeripheralError,
    TOUCH_PINS,
    validate_pin,
)
from .llm import (
    LLMStreamCancelled,
    create_llm,
    default_base_url,
    normalize_api_provider,
    normalize_thinking_level,
)
from .mcp import MCPManager, MCPStore
from .memory import MemoryStore
from .migration import migrate_legacy_skills
from .recovery import Recovery, Supervisor
from .skills import SkillStore
from .workspace import Workspace, WorkspaceError


PROMPT = "esp32> "


HELP = """Commands:
  /help                 Show this help
  /setup                Configure Wi-Fi and model interactively
  /status               Show redacted device and agent status
  /repl                 Reset into maintenance REPL for deployment
  /wifi                 Configure and test Wi-Fi
  /model                Configure OpenAI-compatible or Anthropic API
  /thinking [level]     Show/set thinking: disable, low, high, max (saved)
  /thinking show on|off Show/hide reasoning text (default: off)
  /auto [on|off]        Auto-run generated code and auto-feedback
  /auto turns N         Set maximum automatic feedback turns (1-50)
  /native [on|off]      Show/configure native function calling
  /ca                   Paste and save the API CA certificate
  /test                 Test Wi-Fi, NTP and TLS settings
  /test api             Send a small model API request
  /clear                Clear conversation and pending code
  /undo [N]             Rewind the Nth newest user message and all later replies
  /rewind [N]           Alias for /undo
  /history              Show bounded conversation history
  /context              Show estimated context usage and compaction settings
  /compact              Summarize older context now and keep recent messages
  /summary              Show the current conversation summary
  /memory               Show always-loaded MEMORY.md
  /memory append <text> Append a line to MEMORY.md
  /memory delete <text> Delete matching MEMORY.md lines (confirm)
  /memory replace <text> Replace one matching MEMORY.md line
  /memory clear         Reset MEMORY.md after confirmation
  /search <query>       Search the web (auto/bing provider)
  /search provider ...  Set search provider: auto or bing
  /search on|off        Enable or disable web search
  /http get <url>       Fetch a public HTTPS URL (text)
  /http head <url>      Fetch response headers only
  /http download <url> [path]  Download to /workspace
  /mcp                  Show/configure MCP servers and remote tools
  /code                 Show pending generated code
  /run                  Confirm and run pending code in the foreground
  /run bg [note]        Run pending code in a background job
  /continue [id]        Resume the conversation after a background result
  /background           List background jobs (ids, notes, elapsed time)
  /background status ID Show one job including output tail
  /background stop [ID] Request a cooperative stop (all jobs without ID)
  /background note ID TEXT  Set a job's short note
  /background discard ID    Discard a finished job and its result
  /discard [ID]         Discard pending code/skill or a background result
  /skills               List saved skill packages
  /skill-show <name>    Show a skill's SKILL.md
  /skill-delete <name>  Delete a skill package after confirmation
  /peripherals          Show/configure GPIO, I2C, SPI, UART, NeoPixel, ADC, PWM, touch and RTC
  /peripherals spi 1 12 11 - 20000000 hw  Hardware SPI, no MISO pin
  /peripherals pwm 13   Allow PWM on GPIO13
  /peripherals touch 4  Allow capacitive touch on GPIO4
  /peripherals rtc write on  Allow api.rtc.set_datetime
  /peripherals transfer 4096  Raise the single-transfer byte limit
  /peripherals scan 0   Scan an I2C bus for device addresses
  /workspace            Show workspace and tmp contents
  /workspace list [path] List workspace files
  /workspace read <path> Show a workspace file
  /workspace run [bg [timeout]] <path>  Run a workspace .py after confirmation
  /workspace clean      Clear /workspace/tmp
  /web                  Show web console status and URLs
  /web reset-password   Reset web admin password and start setup AP
  /factory-reset        Delete all user data and reboot (type RESET)

Anything not starting with '/' is sent as a task to the local agent.
Ctrl-C cancels the current serial input. Secrets are never printed.
"""


def _thread_id():
    return _thread.get_ident() if _thread is not None else 0


def _sleep_ms(value):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value)
    else:
        time.sleep(value / 1000.0)


def _ticks_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.monotonic() * 1000)


def _ticks_add(value, delta):
    return time.ticks_add(value, delta) if hasattr(time, "ticks_add") else value + delta


def _ticks_diff(now, then):
    return time.ticks_diff(now, then) if hasattr(time, "ticks_diff") else now - then


FACTORY_RESET_FILES = (
    "config.json", "secrets.json", "MEMORY.md",
    "recovery.json", "recovery.py", "mcp.json",
    "config.json.tmp", "secrets.json.tmp", "mcp.json.tmp",
    "maintenance.flag",
)

FACTORY_RESET_DIRS = ("skills", "tools", "workspace", "state")


def _remove_file(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _remove_tree(path):
    try:
        names = os.listdir(path)
    except OSError:
        return False
    removed = False
    for name in names:
        child = path + "/" + name
        if _remove_file(child):
            removed = True
            continue
        if _remove_tree(child):
            removed = True
        try:
            os.rmdir(child)
            removed = True
        except OSError:
            pass
    try:
        os.rmdir(path)
        removed = True
    except OSError:
        pass
    return removed


def factory_reset_files(root="/"):
    """Delete user-defined files and return the paths that were removed."""
    removed = []
    for name in FACTORY_RESET_FILES:
        if _remove_file(join_path(root, name)):
            removed.append("/" + name)
    for name in FACTORY_RESET_DIRS:
        if _remove_tree(join_path(root, name)):
            removed.append("/" + name)
    return removed


class UIRouter:
    def __init__(self, serial):
        self.serial = serial
        self.sinks = {}

    def bind(self, sink):
        self.sinks[_thread_id()] = sink

    def unbind(self):
        self.sinks.pop(_thread_id(), None)

    def sink(self):
        return self.sinks.get(_thread_id())

    def current(self):
        return self.sink() or self.serial

    def write(self, text):
        return self.current().write(text)

    def line(self, text=""):
        return self.current().line(text)

    def readline(self, prompt="", secret=False):
        return self.current().readline(prompt, secret)

    def ask_yes_no(self, prompt, default=False):
        return self.current().ask_yes_no(prompt, default)


class SerialUI:
    def __init__(self, feed=None):
        self.feed = feed or (lambda: None)
        self.poll = select.poll()
        # TextIOWrapper applies universal-newline translation, which turns both
        # CRLF and LFCR into LF-LF on the device. Read the raw buffer instead so
        # the complementary terminator can be recognized.
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        try:
            self.poll.register(stream, select.POLLIN)
        except OSError:
            stream = sys.stdin
            self.poll.register(stream, select.POLLIN)
        self.stdin = stream
        self.skip_terminator = 0
        self.utf8_pending = bytearray()
        self.utf8_needed = 0
        # A list, not bytearray: MicroPython bytearray has no pop(index).
        self.pending_input = []

    def has_pending_line(self):
        """True when a complete command is already buffered for readline().

        The AUTO background continuation runs from the idle hook. If the user
        has just pressed Enter on a command, let readline consume it first so
        an explicit /background discard or /continue cannot be preempted.
        """
        for code in getattr(self, "pending_input", ()):
            if code in (10, 13):
                return True
        return False

    def check_cancel(self):
        """Consume available serial bytes and turn Ctrl-C into cancellation.

        The model request runs while readline() is not active, so the normal
        REPL interrupt path is unavailable. Other typed bytes are buffered for
        the next readline() instead of being lost.
        """
        while len(self.pending_input) < 256 and self.poll.poll(0):
            raw = self.stdin.read(1)
            if not raw:
                return
            code = raw[0] if isinstance(raw, bytes) else ord(raw)
            if code == 3:
                self.write("^C\n")
                raise LLMStreamCancelled()
            self.pending_input.append(code)

    def readline(self, prompt="", secret=False):
        self.write(prompt)
        chars = []
        stream = getattr(self, "stdin", None)
        if stream is None:
            stream = getattr(sys.stdin, "buffer", sys.stdin)
        self.utf8_pending = bytearray()
        self.utf8_needed = 0
        while True:
            self.feed()
            if self.pending_input:
                code = self.pending_input.pop(0)
            else:
                if not self.poll.poll(100):
                    continue
                raw = stream.read(1)
                if not raw:
                    continue
                code = raw[0] if isinstance(raw, bytes) else ord(raw)
            if self.skip_terminator:
                expected = self.skip_terminator
                self.skip_terminator = 0
                if code == expected:
                    continue
            if code in (13, 10):
                # Only swallow the complementary character of CRLF/LFCR. Two
                # consecutive LF or CR characters represent a real empty line.
                self.skip_terminator = 10 if code == 13 else 13
                self.utf8_pending = bytearray()
                self.utf8_needed = 0
                self.write("\n")
                return "".join(chars)
            if code == 3:
                self.write("^C\n")
                raise KeyboardInterrupt()
            if code in (8, 127):
                self.utf8_pending = bytearray()
                self.utf8_needed = 0
                if chars:
                    chars.pop()
                    if not secret:
                        self.write("\b \b")
                continue
            if code <= 0x7F:
                self.utf8_pending = bytearray()
                self.utf8_needed = 0
                if code >= 0x20:
                    self._append_char(chr(code), chars, secret)
                continue
            if code > 0xFF:
                self.utf8_pending = bytearray()
                self.utf8_needed = 0
                self._append_char(chr(code), chars, secret)
                continue
            if not self.utf8_pending:
                if 0xC2 <= code <= 0xDF:
                    self.utf8_needed = 2
                elif 0xE0 <= code <= 0xEF:
                    self.utf8_needed = 3
                elif 0xF0 <= code <= 0xF4:
                    self.utf8_needed = 4
                else:
                    continue
            self.utf8_pending.append(code)
            if len(self.utf8_pending) == self.utf8_needed:
                try:
                    decoded = bytes(self.utf8_pending).decode()
                except ValueError:
                    decoded = ""
                self.utf8_pending = bytearray()
                self.utf8_needed = 0
                if decoded:
                    self._append_char(decoded, chars, secret)

    def _append_char(self, char, chars, secret):
        chars.append(char)
        if not secret:
            self.write(char)

    def ask_yes_no(self, prompt, default=False):
        suffix = " [Y/n] " if default else " [y/N] "
        answer = self.readline(prompt + suffix).strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    @staticmethod
    def write(text):
        sys.stdout.write(text)
        try:
            sys.stdout.flush()
        except AttributeError:
            pass

    @staticmethod
    def line(text=""):
        SerialUI.write(str(text) + "\n")


def command_is_exclusive(line):
    parts = line.split()
    if not parts:
        return False
    command = parts[0].lower()
    args = parts[1:]
    read_only = (
        "/help", "/status", "/history", "/context", "/summary",
        "/skills", "/skill-show",
    )
    if command in read_only:
        return False
    if command == "/memory":
        return bool(args)
    if command == "/auto":
        return bool(args)
    if command == "/thinking":
        return bool(args)
    if command == "/workspace":
        return bool(args) and args[0].lower() not in ("list", "read", "stat")
    if command == "/peripherals":
        return bool(args)
    if command == "/web":
        return bool(args)
    if command == "/mcp":
        return bool(args) and args[0].lower() not in ("list", "status")
    if command in ("/background", "/bg"):
        return bool(args) and args[0].lower() not in (
            "list", "ls", "status", "show")
    return True


class LiveTurn:
    """Bounded copy of the model output produced by AUTO continuation.

    The web request that carried the original chat is not open while the
    device main thread auto-continues a background result. This buffer lets
    the browser poll the same reasoning/content chunks instead of waiting for
    the final history entry to appear.
    """

    def __init__(self, limit=16384):
        self.limit = max(1024, int(limit))
        self.id = 0
        self.active = False
        self.content = ""
        self.reasoning = ""
        self.started_ms = 0
        self.finished_ms = 0
        self.truncated = False
        self.turns = 0
        self.events = []
        self.events_dropped = 0

    def begin(self):
        self.id += 1
        self.active = True
        self.content = ""
        self.reasoning = ""
        self.started_ms = _ticks_ms()
        self.finished_ms = 0
        self.truncated = False
        self.turns = 0
        self.events = []
        self.events_dropped = 0

    def next_turn(self):
        if not self.active:
            return
        self.turns += 1
        if self.content and not self.content.endswith("\n\n"):
            self.content += "\n\n"

    def _append(self, current, text):
        if not self.active or not text:
            return current
        text = str(text)
        remaining = self.limit - len(current)
        if remaining <= 0:
            self.truncated = True
            return current
        if len(text) > remaining:
            text = text[:remaining]
            self.truncated = True
        return current + text

    def append_content(self, text):
        self.content = self._append(self.content, text)

    def append_reasoning(self, text):
        self.reasoning = self._append(self.reasoning, text)

    def add_event(self, event):
        if not self.active:
            return
        if len(self.events) >= 24:
            self.events.pop(0)
            self.events_dropped += 1
        self.events.append(event)

    def finish(self):
        if not self.active:
            return
        self.active = False
        self.finished_ms = _ticks_ms()

    def summary(self):
        return {
            "id": self.id,
            "active": self.active,
            "chars": len(self.content),
            "reasoning_chars": len(self.reasoning),
            "truncated": self.truncated,
            "turns": self.turns,
        }

    def view(self):
        value = self.summary()
        value.update({
            "content": self.content,
            "reasoning": self.reasoning,
            "started_ms": self.started_ms,
            "finished_ms": self.finished_ms,
            "events": list(self.events),
            "event_base": self.events_dropped,
        })
        return value


class Application:
    def __init__(self, root="/", config=None, network=None, http=None, supervisor=None):
        self.main_thread_id = _thread_id()
        self.turn_lock = _thread.allocate_lock() if _thread is not None else None
        self.main_lock = _thread.allocate_lock() if _thread is not None else None
        self.main_request = None
        self.execution_active = False
        self.backgrounds = None
        self.busy_source = ""
        self._at_prompt = False
        self._auto_continue_running = False
        self._auto_continue_retry_at = 0
        self.auto_continue_seq = 0
        self._network_ready = False
        self.pending_wifi = None
        self.web = None
        self.web_app = None
        self.web_auth = None
        self.web_error = ""
        self._notices = []
        self.reboot_at = 0
        self.config = config or Config(root)
        try:
            live_limit = int(self.config.get("web_output_limit", 16384))
        except (TypeError, ValueError):
            live_limit = 16384
        self.live = LiveTurn(live_limit)
        self.reset_info = self._read_reset_cause()
        self.background_store = BackgroundStore(root, self.config)
        self.supervisor = supervisor or Supervisor(self.config.get("watchdog_ms"))
        self._serial_ui = SerialUI(self.idle)
        self.ui = UIRouter(self._serial_ui)
        self.board = Board()
        self.network = network or Network(self.feed)
        self.network.feed = self.feed
        self.http = http or HttpClient(self.feed)
        self.http.feed = self.feed
        self.http.cancel_check = self.check_cancel
        self.http.trace = self.http_trace
        self.llm = create_llm(self.config, self.http)
        self.skills = SkillStore(root)
        self.workspace = Workspace(root, self.config)
        self.memory = MemoryStore(root, self.config)
        self.hardware = HardwareManager(self.config)
        self.migrated = migrate_legacy_skills(root, self.skills)
        self.recovery = Recovery(root)
        self.conversation = ConversationStore(root, self.config)
        self.mcp_store = MCPStore(root, self.config)
        self.mcp = MCPManager(self.config, self.http, self.mcp_store)
        self.mcp.on_confirm = self.confirm_mcp_call
        self.hardware_api = HardwareAPI(self.hardware)
        # Keep ``import api`` working for code written against the earlier
        # capability wording, while generated code normally uses the injected
        # global object without importing anything.
        try:
            sys.modules["api"] = self.hardware_api
            # ``device`` is injected as a global; keep an import alias too so
            # generated code that writes ``import device`` still works.
            sys.modules["device"] = self.board
        except Exception:
            pass
        self.executor = Executor(
            self.board,
            self.config.get("output_limit"),
            self.recovery,
            timeout_s=self.config.get("code_timeout_s", 20),
            max_timeout_s=self.config.get("code_timeout_max_s", 300),
            feed=self.feed_execution,
            api=self.hardware_api,
        )
        self.executor.main_runner = self
        self.backgrounds = BackgroundPool(
            self.config, self.background_store,
            self._execute_background_job, notify=self.queue_notice,
        )
        self.agent = Agent(
            self.config, self.llm, self.board,
            skills=self.skills, workspace=self.workspace, memory=self.memory,
            executor=self.executor, http=self.http,
            conversation=self.conversation, mcp=self.mcp,
        )
        self.agent.on_model_start = self.show_model_start
        self.agent.on_model_result = self.show_model_result
        self.agent.on_reasoning = self.show_reasoning_chunk
        self.agent.on_content = self.show_content_chunk
        self.agent.on_notice = self.show_notice
        self.agent.on_tool_start = self.show_tool_start
        self.agent.on_tool_result = self.show_tool_result
        self.agent.on_workspace_run_confirm = self.confirm_workspace_run
        self._thinking_stream_open = False
        self._content_stream_open = False
        self._reasoning_stream_chars = 0
        self._reasoning_stream_truncated = False

    @staticmethod
    def _read_reset_cause():
        try:
            import machine
            value = machine.reset_cause()
        except Exception:
            return None
        names = {}
        for name in dir(machine):
            if not name.endswith("_RESET"):
                continue
            try:
                names[getattr(machine, name)] = name
            except Exception:
                pass
        return {"code": value, "name": names.get(value, "UNKNOWN")}

    def reset_note(self):
        info = getattr(self, "reset_info", None)
        if not info:
            return ""
        name = info.get("name", "UNKNOWN")
        if name == "WDT_RESET":
            return (
                "设备上次因看门狗复位（WDT_RESET），网页会话只保存在内存中，"
                "所以需要重新登录。常见原因是生成代码长时间阻塞、没有调用 "
                "__harness_check()，或长任务超过 watchdog_ms。"
            )
        if name == "BROWNOUT_RESET":
            return (
                "设备上次因供电不足复位（BROWNOUT_RESET）。请检查 USB/电源、"
                "背光和外设负载。"
            )
        if name in ("PWRON_RESET", "HARD_RESET"):
            return ""
        return "上次复位原因: %s (%s)。" % (name, info.get("code", "?"))

    def feed(self):
        if self.execution_active:
            return
        if _thread is not None and _thread.get_ident() != self.main_thread_id:
            return
        self.supervisor.feed()

    def feed_execution(self):
        """Feed the watchdog from cooperative execution hooks only.

        A tight loop that never calls a hook still starves the watchdog, so the
        hardware reset remains the last-resort safety net.
        """
        if self.on_main_thread():
            self.supervisor.feed()

    def reload_llm(self):
        """Rebuild the model client after provider or model changes."""
        self.http.close()
        self.llm = create_llm(self.config, self.http)
        if getattr(self, "agent", None) is not None:
            self.agent.llm = self.llm
        return self.llm

    def check_cancel(self):
        """Check the active serial or web client for a cancel request."""
        if self.on_main_thread():
            self._serial_ui.check_cancel()
            return
        sink = self.ui.sink()
        check = getattr(sink, "check_cancel", None) if sink is not None else None
        if check is not None:
            check()

    def live_view(self):
        return self.live.view()

    def live_status(self):
        return self.live.summary()

    def idle(self):
        self.pump_main_request()
        self.drain_notices()
        self.feed()
        serial = getattr(self, "_serial_ui", None)
        if (getattr(self, "_at_prompt", False)
                and (serial is None or not serial.has_pending_line())):
            self.auto_continue_background()
        self._service_reboot()

    def auto_continue_background(self):
        """Continue ready background results in AUTO mode without a prompt.

        The web card is only a view. AUTO mode must keep working when the
        browser tab is closed or hidden, so the device main thread owns this
        continuation. Manual mode (or auto_feedback off) keeps the explicit
        continue/discard controls.
        """
        if not self.config.get("auto_run", False):
            return None
        if not self.config.get("auto_feedback", True):
            return None
        if self.backgrounds is None:
            return None
        if getattr(self, "_auto_continue_running", False):
            return None
        if _ticks_diff(
                _ticks_ms(), getattr(self, "_auto_continue_retry_at", 0)) < 0:
            return None
        agent = self.agent
        has_pending = getattr(agent, "has_pending", None)
        if has_pending is not None and has_pending():
            return None
        if getattr(agent, "pending_question", None) is not None:
            return None
        ready = [job for job in self.backgrounds.ready_jobs()
                 if not job.get("recovered")]
        if not ready:
            return None
        job = ready[0]
        prompt_visible = bool(getattr(self, "_at_prompt", False))
        self._auto_continue_running = True
        try:
            try:
                self.prepare_cloud()
            except Exception as exc:
                self._auto_continue_retry_at = _ticks_add(_ticks_ms(), 15000)
                self.queue_notice(
                    "AUTO background continue delayed: %s" % exc)
                return None
            if not self.begin_operation("auto-continue"):
                self._auto_continue_retry_at = _ticks_add(_ticks_ms(), 1000)
                return None
            try:
                if prompt_visible:
                    self.ui.write("\n")
                self.ui.line(
                    "AUTO: continuing background result #%s automatically."
                    % job.get("id", "?"))
                self.live.begin()
                try:
                    self.continue_background(job.get("id"))
                finally:
                    self.live.finish()
            except LLMStreamCancelled:
                self.ui.line("Automatic background continue cancelled.")
            except Exception as exc:
                self.queue_notice(
                    "AUTO background continue failed: %s" % exc)
            finally:
                self.end_operation()
                self.auto_continue_seq = (
                    getattr(self, "auto_continue_seq", 0) + 1)
                if prompt_visible:
                    self.drain_notices()
                    self.ui.write(PROMPT)
        finally:
            self._auto_continue_running = False
        return job

    def request_reboot(self, delay_ms=1000):
        """Schedule a main-thread reboot after the web response is sent."""
        self.reboot_at = _ticks_add(_ticks_ms(), max(0, int(delay_ms)))

    def _service_reboot(self):
        if not self.reboot_at:
            return
        if _ticks_diff(_ticks_ms(), self.reboot_at) < 0:
            return
        self.reboot_at = 0
        self.ui.line("Rebooting...")
        self.supervisor.feed()
        import machine
        machine.reset()

    def factory_reset_data(self):
        """Delete user-defined data. This must run on the serial main thread."""
        if not self.on_main_thread():
            raise RuntimeError("Factory reset must run on the serial main thread")
        removed = factory_reset_files(self.config.root)
        self.config.reset_memory()
        self.pending_wifi = None
        if self.web_auth is not None:
            self.web_auth.clear_sessions()
        self.agent.clear()
        if self.backgrounds is not None:
            self.backgrounds.clear()
        else:
            try:
                self.background_store.clear()
            except Exception:
                pass
        self.mcp.reload()
        return removed

    def on_main_thread(self):
        return _thread is None or _thread.get_ident() == self.main_thread_id

    def is_exclusive_command(self, line):
        return command_is_exclusive(line)

    def run_on_main(self, func):
        if self.on_main_thread():
            return func()
        if self.main_lock is None:
            return func()
        self.main_lock.acquire()
        if self.main_request is not None:
            self.main_lock.release()
            raise RuntimeError("Another main-thread execution is already waiting")
        request = {
            "func": func, "started": False, "done": False,
            "result": None, "error": None,
        }
        self.main_request = request
        self.main_lock.release()
        started_at = time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.monotonic() * 1000)
        while True:
            self.main_lock.acquire()
            done = request["done"]
            started = request["started"]
            self.main_lock.release()
            if done:
                break
            if not started:
                now = time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.monotonic() * 1000)
                elapsed = time.ticks_diff(now, started_at) if hasattr(time, "ticks_diff") else now - started_at
                if elapsed > 10000:
                    cancelled = False
                    self.main_lock.acquire()
                    if self.main_request is request and not request["started"]:
                        self.main_request = None
                        cancelled = True
                    self.main_lock.release()
                    if cancelled:
                        raise RuntimeError("Main thread did not accept execution")
            _sleep_ms(20)
        if request["error"] is not None:
            raise request["error"]
        return request["result"]

    @property
    def background(self):
        """Compatibility view: the active/ready job the UI should focus on."""
        pool = getattr(self, "backgrounds", None)
        if pool is None:
            return None
        return pool.primary()

    @property
    def background_seq(self):
        pool = getattr(self, "backgrounds", None)
        return pool.seq if pool is not None else 0

    def background_active(self):
        pool = getattr(self, "backgrounds", None)
        return bool(pool is not None and pool.has_active())

    def _execute_background_job(self, job):
        """Run one job on its worker thread with isolated output."""
        executor = Executor(
            self.board,
            self.config.get("output_limit"),
            # A single recovery marker cannot represent concurrent jobs.
            # Background sources are shown in the conversation and their
            # bounded results are persisted instead of auto-running on boot.
            None,
            timeout_s=self.config.get("code_timeout_s", 20),
            max_timeout_s=self.config.get("code_timeout_max_s", 300),
            feed=self.feed_execution,
            cancel_check=lambda: bool(job.get("stop_requested")),
            isolated_output=True,
            api=self.hardware_api,
        )
        return executor.execute(
            job["source"], timeout_s=job.get("timeout"))

    def _release_before_worker_start(self):
        http = getattr(self, "http", None)
        if http is not None:
            try:
                http.close()
            except Exception:
                pass
        try:
            gc.collect()
        except Exception:
            pass

    def _require_background_execution(self):
        if not self.config.get("background_execution", True):
            raise RuntimeError("Background execution is disabled")
        if self.backgrounds is None:
            raise RuntimeError("Background execution is unavailable")

    def _create_background_job(self, source, timeout, auto=False,
                               driver="agent", note="", recovered=False):
        agent = self.agent
        if source is None:
            raise RuntimeError("No source for background execution")
        if not isinstance(source, str):
            source = str(source)
        if not source:
            raise RuntimeError("No source for background execution")
        code_limit = int(self.config.get("code_limit", 12288) or 12288)
        if len(source) > code_limit:
            raise RuntimeError(
                "Source exceeds code_limit=%s" % code_limit)
        task = ""
        try:
            index = getattr(agent, "last_user_index", None)
            if index is not None and 0 <= index < len(agent.history):
                task = str(agent.history[index].get("content", ""))
        except Exception:
            task = ""
        task = " ".join(task.split())[:200]
        try:
            turns = int(getattr(agent, "turns", 0) or 0)
        except (TypeError, ValueError):
            turns = 0
        # Worker stacks come from the ESP-IDF internal heap. A completed
        # model turn can leave a reusable TLS/socket connection holding a
        # large block; close it and collect before asking FreeRTOS for a
        # thread stack. The next model request reconnects on demand.
        self._release_before_worker_start()
        return self.backgrounds.create(
            source, timeout,
            auto=auto, driver=driver, task=task,
            note=note or task, turns=turns,
            recovered=recovered,
        )

    def start_background_source(self, source, timeout=None, auto=False,
                                driver="agent", note="", recovered=False):
        """Start AI/foreground supplied source as a detached job.

        Unlike start_background() this does not consume pending code. The
        Agent tool layer is responsible for requiring auto_run first.
        """
        self._require_background_execution()
        if source is None:
            raise RuntimeError("No source for background execution")
        if not isinstance(source, str):
            source = str(source)
        if auto and not self.config.get("auto_run", False):
            raise RuntimeError(
                "Automatic background start requires auto_run (AUTO mode)")
        effective_timeout = timeout
        if effective_timeout is None:
            effective_timeout = self.config.get("code_timeout_s", 20)
        try:
            effective_timeout = int(effective_timeout)
        except (TypeError, ValueError):
            effective_timeout = 0
        if (auto and effective_timeout != 0
                and not auto_source_is_safe(source)):
            raise RuntimeError(
                "Automatic background start rejected an obvious indefinite "
                "loop; pass timeout=0 for an intentional infinite job")
        return self._create_background_job(
            source, timeout, auto=auto, driver=driver, note=note,
            recovered=recovered)

    def start_background(self, auto=False, driver="web", note=""):
        """Move pending generated code into a detached worker thread."""
        self._require_background_execution()
        agent = self.agent
        source, timeout, recovered = agent.begin_execution()
        try:
            return self._create_background_job(
                source, timeout, auto=auto, driver=driver, note=note,
                recovered=recovered)
        except BaseException:
            agent.restore_pending_source(source, timeout, recovered)
            raise

    def stop_background(self, reason="", job_id=None):
        """Request a cooperative stop of one or all background programs."""
        if self.backgrounds is None:
            return 0
        return self.backgrounds.stop(job_id=job_id, reason=reason)

    def _commit_background_result(self, job):
        result = job.get("result")
        if result is not None:
            # A job in memory has not been committed yet. Deduplication is
            # only needed on boot restore; skipping here could leave
            # last_result unset and break /api/continue.
            self.agent.record_execution_result(
                result, recovered=job.get("recovered", False),
                background=True, job_id=job.get("token") or job["id"],
                task=job.get("task", ""))
        if job.get("turns") is not None:
            try:
                self.agent.turns = int(job["turns"])
            except (TypeError, ValueError):
                pass

    def take_background_result(self, job_id=None):
        """Commit one finished background result into the conversation."""
        if self.backgrounds is None:
            raise RuntimeError("No completed background execution")
        return self.backgrounds.take(
            job_id, commit=self._commit_background_result)

    def discard_background(self, record=False, job_id=None):
        if self.backgrounds is None:
            return False
        removed = self.backgrounds.discard(job_id=job_id)
        self._record_discarded(removed, record)
        return bool(removed)

    def discard_all_backgrounds(self, record=False):
        if self.backgrounds is None:
            return 0
        removed = self.backgrounds.discard(all_finished=True)
        self._record_discarded(removed, record)
        return len(removed)

    def _record_discarded(self, removed, record):
        if not record or not removed:
            return
        if len(removed) == 1:
            text = (
                "Local harness event: the user discarded the completed "
                "background execution result without continuing.")
        else:
            text = (
                "Local harness event: the user discarded %s completed "
                "background results without continuing." % len(removed))
        self.agent.record_local_observation(text)

    def prepare_for_new_turn(self):
        """Commit ready jobs and clear error jobs before a new user turn."""
        if self.backgrounds is None:
            return None
        last = None
        for job in list(self.backgrounds.ready_jobs()):
            try:
                last = self.take_background_result(job["id"])
            except Exception as exc:
                self.queue_notice(
                    "Could not commit background job #%s: %s"
                    % (job.get("id", "?"), exc))
                break
        for job in list(self.backgrounds.error_jobs()):
            error = job.get("error") or "unknown error"
            try:
                self.backgrounds.discard(job_id=job.get("id"))
            except Exception:
                continue
            self.agent.record_local_observation(
                "Local harness event: background execution #%s failed: %s"
                % (job.get("id", "?"), str(error)[:500]))
            last = job
        return last

    def restore_background(self):
        if self.backgrounds is None:
            return None
        try:
            jobs = self.backgrounds.restore(
                committed=lambda token: self.agent.has_background_result(token))
        except Exception as exc:
            self.queue_notice("Background state restore failed: %s" % exc)
            return None
        return jobs[0] if jobs else None

    def background_view(self, job_id=None):
        if self.backgrounds is None:
            return {
                "active": False, "phase": "none", "id": 0, "token": "",
                "auto": False, "recovered": False, "driver": "",
                "source_chars": 0, "timeout_s": None,
                "task": "", "note": "", "turns": 0,
                "created_ms": 0, "started_ms": 0, "finished_ms": 0,
                "stop_requested": False, "stop_reason": "",
                "elapsed_ms": 0, "error": "", "result": None,
            }
        return self.backgrounds.view(job_id)

    def background_views(self, result_limit=4096):
        if self.backgrounds is None:
            return []
        return self.backgrounds.views(result_limit=result_limit)

    def background_get(self, job_id=None):
        if self.backgrounds is None:
            return None
        return self.backgrounds.get(job_id)

    def background_ready_jobs(self):
        if self.backgrounds is None:
            return []
        return self.backgrounds.ready_jobs()

    def background_error_jobs(self):
        if self.backgrounds is None:
            return []
        return self.backgrounds.error_jobs()

    def set_background_note(self, job_id, text):
        if self.backgrounds is None:
            raise RuntimeError("Background execution is unavailable")
        return self.backgrounds.note(job_id, text)

    def refresh_background_limits(self):
        if self.backgrounds is not None:
            self.backgrounds.refresh_limits()

    def background_summary_lines(self, include_output=False):
        if self.backgrounds is None:
            return ["No background jobs."]
        return self.backgrounds.summary_lines(include_output=include_output)

    def pump_main_request(self):
        if self.main_lock is None:
            request = self.main_request
            self.main_request = None
        else:
            self.main_lock.acquire()
            request = self.main_request
            self.main_request = None
            if request is not None:
                request["started"] = True
            self.main_lock.release()
        if request is None:
            return
        result = None
        error = None
        self.execution_active = True
        try:
            result = request["func"]()
        except BaseException as exc:
            error = exc
        finally:
            self.execution_active = False
        if self.main_lock is None:
            request["result"] = result
            request["error"] = error
            request["done"] = True
        else:
            self.main_lock.acquire()
            request["result"] = result
            request["error"] = error
            request["done"] = True
            self.main_lock.release()

    def begin_operation(self, source):
        # Background jobs run on their own threads, so they no longer occupy
        # the main thread or the turn lock. New conversation turns and
        # foreground runs may proceed. Configuration/maintenance web actions
        # stay blocked until the jobs finish to avoid changing Wi-Fi or model
        # state underneath running code.
        if self.background_active() and source not in (
                "web-chat", "web-run", "web-continue", "web-background-stop",
                "web-background-discard", "web-discard", "web-save-skill",
                "auto-continue", "serial"):
            return False
        if self.turn_lock is not None:
            if not self.turn_lock.acquire(False):
                return False
        self.busy_source = source
        return True

    def end_operation(self):
        self.busy_source = ""
        if self.turn_lock is not None:
            self.turn_lock.release()

    def is_turn_busy(self):
        if self.busy_source:
            return True
        if self.turn_lock is None:
            return False
        try:
            return self.turn_lock.locked()
        except AttributeError:
            return False

    def is_busy(self):
        return self.background_active() or self.is_turn_busy()

    def queue_notice(self, text):
        if len(self._notices) >= 20:
            self._notices.pop(0)
        self._notices.append(str(text))

    def drain_notices(self):
        while self._notices:
            self.ui.line(self._notices.pop(0))

    def run(self):
        recovered = self.recovery.consume()
        self.agent.restore_pending(recovered)
        restored = 0
        if self.config.get("persist_conversation", True):
            try:
                state = self.conversation.load()
                if state:
                    restored = self.agent.restore_conversation(state)
            except Exception as exc:
                self.ui.line("WARNING: conversation restore failed: %s" % exc)
            if self.conversation.last_error:
                self.ui.line(
                    "WARNING: saved conversation could not be read: %s"
                    % self.conversation.last_error
                )
        self.ui.line("\nESP32 Harness %s - local MicroPython agent" % __version__)
        reset_note = self.reset_note()
        if reset_note:
            self.ui.line("WARNING: " + reset_note)
        if self.migrated:
            self.ui.line("Migrated %s legacy skill record(s) to SKILL.md." % self.migrated)
        if restored:
            self.ui.line(
                "Restored %s message(s) and the saved summary from persistent context."
                % restored
            )
        restored_background = self.restore_background()
        if restored_background is not None:
            views = self.background_views()
            self.ui.line(
                "Restored %s background record(s), including job %s; "
                "AUTO mode continues ready results automatically, otherwise "
                "use /background and /continue or the web console to review."
                % (len(views), restored_background.get("id", "?")))
        if recovered:
            self.ui.line("WARNING: previous generated code caused a reset. It is pending, NOT auto-run.")
        self.ui.line("Type /help for commands. Run /setup for first-time configuration.")
        self.boot_network()
        self.start_web()
        while True:
            try:
                self._at_prompt = True
                try:
                    line = self.ui.readline(PROMPT).strip()
                finally:
                    self._at_prompt = False
                if not line:
                    continue
                if line.startswith("/"):
                    exclusive = command_is_exclusive(line)
                    acquired = False
                    if exclusive:
                        if not self.begin_operation("serial"):
                            self.ui.line(
                                "Busy: a web operation is in progress; "
                                "read-only commands remain available."
                            )
                            continue
                        acquired = True
                    try:
                        self.command(line)
                    finally:
                        if acquired:
                            self.end_operation()
                else:
                    self.handle_task(line)
            except KeyboardInterrupt:
                self.ui.line("Input cancelled.")
            except LLMStreamCancelled:
                self.ui.line("Request cancelled.")
            except Exception as exc:
                self.ui.line("Error: %s" % exc)

    def command(self, line):
        parts = line.split()
        command = parts[0].lower()
        args = parts[1:]
        if command == "/help":
            self.ui.line(HELP)
        elif command == "/setup":
            self.configure_wifi()
            self.configure_model()
            if self.config.get("tls_verify"):
                try:
                    os.stat(self.config.get("ca_file"))
                    ca_exists = True
                except OSError:
                    ca_exists = False
                if ca_exists:
                    self.ui.line("A CA bundle is already installed; most users should keep it and answer N.")
                prompt = "CA file exists; replace it?" if ca_exists else "Paste the API CA certificate now?"
                if self.ui.ask_yes_no(prompt, not ca_exists):
                    self.configure_ca()
            self.configure_web_password()
        elif command == "/status":
            self.show_status()
        elif command == "/repl":
            self.enter_maintenance()
        elif command == "/wifi":
            self.configure_wifi()
        elif command == "/model":
            self.configure_model()
        elif command == "/thinking":
            self.configure_thinking(args)
        elif command == "/auto":
            self.configure_auto(args)
        elif command == "/native":
            self.configure_native(args)
        elif command == "/ca":
            self.configure_ca()
        elif command == "/test":
            self.test(args)
        elif command in ("/undo", "/rewind"):
            self.rewind_command(args)
        elif command == "/clear":
            if self.background_active():
                self.ui.line(
                    "Cannot clear while background jobs are running; stop "
                    "them first with /background stop.")
                return
            self.discard_all_backgrounds(record=True)
            try:
                self.background_store.clear()
            except Exception:
                pass
            self.agent.clear()
            self.ui.line("Conversation cleared.")
        elif command == "/history":
            self.show_history()
        elif command == "/context":
            self.show_context()
        elif command == "/compact":
            if not self.agent.history:
                self.ui.line("Conversation is empty.")
            elif len(self.agent.history) <= self.config.get("compact_keep_messages", 4):
                self.ui.line("Nothing to compact; history already fits the keep window.")
            else:
                self.prepare_cloud()
                self.ui.line("Compacting older history...")
                if self.agent.compact():
                    self.show_context()
        elif command == "/summary":
            self.show_summary()
        elif command == "/memory":
            self.memory_command(args)
        elif command == "/search":
            self.search_command(args)
        elif command == "/http":
            self.http_command(args)
        elif command == "/mcp":
            self.mcp_command(args)
        elif command == "/code":
            self.show_pending()
        elif command == "/run":
            background = False
            note = ""
            if args:
                if args[0].lower() != "bg":
                    raise ValueError("Usage: /run [bg [note]]")
                background = True
                note = " ".join(args[1:]).strip()
            self.run_pending(background=background, note=note)
        elif command == "/continue":
            if len(args) > 1:
                raise ValueError("Usage: /continue [job-id]")
            self.continue_background(args[0] if args else None)
        elif command in ("/background", "/bg"):
            self.background_command(args)
        elif command == "/discard":
            if len(args) > 1:
                raise ValueError("Usage: /discard [background-job-id]")
            job_id = args[0] if args else None
            had_pending = self.agent.has_pending()
            had_background = False
            try:
                if job_id is not None:
                    had_background = self.discard_background(
                        record=True, job_id=job_id)
                elif had_pending:
                    had_background = self.discard_background(record=True)
                else:
                    had_background = bool(
                        self.discard_all_backgrounds(record=True))
            except Exception as exc:
                self.ui.line(str(exc))
                return
            if job_id is None:
                self.agent.discard()
            self.ui.line(
                "Pending item discarded."
                if (had_pending or had_background)
                else "No pending code, skill, or background result.")
        elif command == "/skills":
            self.list_skills()
        elif command == "/skill-show":
            self.show_skill(self.require_name(args))
        elif command == "/skill-delete":
            self.delete_skill(self.require_name(args))
        elif command == "/peripherals":
            self.configure_peripherals(args)
        elif command == "/workspace":
            self.workspace_command(args)
        elif command == "/web":
            self.web_command(args)
        elif command == "/factory-reset":
            self.factory_reset()
        else:
            self.ui.line("Unknown command. Type /help.")

    @staticmethod
    def require_name(args):
        if len(args) != 1:
            raise ValueError("Expected exactly one name")
        return args[0]

    def http_trace(self, message):
        if self.config.get("http_trace"):
            self.ui.line(message)

    def configure_thinking(self, args):
        display = "on" if self.config.get("show_thinking") else "off"
        if not args:
            self.ui.line("Thinking level: %s" % normalize_thinking_level(
                self.config.get("thinking_level"), "high"))
            self.ui.line("Thinking text display: %s" % display)
            self.ui.line("Options: disable, low, high, max")
            self.ui.line("Display: /thinking show on|off")
            self.ui.line("The level is saved and restored after a reboot.")
            return
        requested = args[0].lower()
        if requested in ("show", "display"):
            if len(args) == 1:
                self.ui.line("Thinking text display: %s" % display)
                self.ui.line("Usage: /thinking show on|off")
                return
            if len(args) != 2 or args[1].lower() not in ("on", "off"):
                raise ValueError("Usage: /thinking show on|off")
            enabled = args[1].lower() == "on"
            self.config.set("show_thinking", enabled)
            if enabled:
                self.ui.line("Thinking text display enabled.")
                self.ui.line("Reasoning text will be printed to the serial console.")
            else:
                self.ui.line("Thinking text display disabled.")
                self.ui.line("Reasoning text is hidden; the model result still reports its character count.")
            return
        if len(args) != 1:
            raise ValueError(
                "Usage: /thinking or /thinking <disable|low|high|max> "
                "or /thinking show on|off"
            )
        level = normalize_thinking_level(requested)
        if level is None:
            raise ValueError("Unknown thinking level: %s" % args[0])
        self.config.set("thinking_level", level)
        self.ui.line("Thinking level set to %s." % level)
        if level != "disabled":
            self.ui.line(
                "Reasoning text is %s and request timeout is extended."
                % ("printed" if display == "on" else "hidden")
            )

    def show_model_start(self):
        sink = self.ui.sink()
        if sink is not None:
            sink.model_start()
            return
        live = getattr(self, "live", None)
        if live is not None:
            live.next_turn()
        self._end_stream_sections()
        self._reasoning_stream_chars = 0
        self._reasoning_stream_truncated = False
        if not self.config.get("show_model_meta"):
            return
        self.ui.line("--- model request: thinking=%s stream=%s ---" % (
            self.config.get("thinking_level"), self.config.get("stream", True)))

    def show_reasoning_chunk(self, text):
        if not self.config.get("show_thinking") or not text:
            return
        sink = self.ui.sink()
        if sink is not None:
            sink.reasoning(text)
            return
        live = getattr(self, "live", None)
        if live is not None:
            live.append_reasoning(text)
        limit = self.config.get("thinking_print_limit", 8192)
        if self._reasoning_stream_chars >= limit:
            if not self._reasoning_stream_truncated:
                self.ui.line("\n...[thinking live output truncated]")
                self._reasoning_stream_truncated = True
            return
        if not self._thinking_stream_open:
            self.ui.line("--- thinking (stream) ---")
            self._thinking_stream_open = True
        remaining = limit - self._reasoning_stream_chars
        chunk = text[:remaining]
        if chunk:
            self.ui.write(chunk)
            self._reasoning_stream_chars += len(chunk)
        if len(text) > remaining and not self._reasoning_stream_truncated:
            self.ui.line("\n...[thinking live output truncated]")
            self._reasoning_stream_truncated = True

    def show_content_chunk(self, text):
        if not text:
            return
        sink = self.ui.sink()
        if sink is not None:
            sink.content(text)
            return
        live = getattr(self, "live", None)
        if live is not None:
            live.append_content(text)
        if self._thinking_stream_open:
            self.ui.line("")
            self.ui.line("--- end thinking ---")
            self._thinking_stream_open = False
        if not self._content_stream_open:
            self.ui.line("--- response (stream) ---")
            self._content_stream_open = True
        self.ui.write(text)

    def _end_stream_sections(self):
        if self._thinking_stream_open:
            self.ui.line("")
            self.ui.line("--- end thinking ---")
            self._thinking_stream_open = False
        if self._content_stream_open:
            self.ui.line("")
            self.ui.line("--- end response ---")
            self._content_stream_open = False

    def show_model_result(self, error):
        sink = self.ui.sink()
        if sink is not None:
            sink.model_result(error, self.llm)
            return
        self._end_stream_sections()
        if not self.config.get("show_model_meta") and not error:
            return
        reasoning = getattr(self.llm, "last_reasoning", "")
        reasoning_chars = getattr(self.llm, "last_reasoning_chars", len(reasoning))
        finish = getattr(self.llm, "last_finish_reason", None)
        elapsed = getattr(self.llm, "last_elapsed_ms", 0)
        content_chars = getattr(self.llm, "last_content_chars", 0)
        response_chars = getattr(self.llm, "last_response_chars", 0)
        timeout_ms = getattr(self.llm, "last_timeout_ms", 0)
        idle_timeout_ms = getattr(self.llm, "last_idle_timeout_ms", 0)
        event_timeout_ms = getattr(self.llm, "last_event_idle_timeout_ms", 0)
        headers_ms = getattr(self.llm, "last_headers_ms", 0)
        first_token_ms = getattr(self.llm, "last_first_token_ms", 0)
        token_count = getattr(self.llm, "last_token_count", 0)
        max_gap_ms = getattr(self.llm, "last_max_gap_ms", 0)
        tail_gap_ms = getattr(self.llm, "last_tail_gap_ms", 0)
        streamed = getattr(self.llm, "last_streamed", False)
        status = "error" if error else "ok"
        self.ui.line("--- model result: status=%s stream=%s finish=%s elapsed=%sms "
                     "header_timeout=%sms idle=%sms event_idle=%sms | "
                     "headers=%sms first_token=%sms chunks=%s max_gap=%sms "
                     "tail_gap=%sms | reasoning=%s chars content=%s chars body=%s chars ---" % (
                         status, streamed, finish, elapsed, timeout_ms,
                         idle_timeout_ms, event_timeout_ms,
                         headers_ms, first_token_ms, token_count, max_gap_ms,
                         tail_gap_ms, reasoning_chars, content_chars, response_chars))
        if error:
            self.ui.line("model error: %s" % error)
        if self.config.get("show_thinking") and not streamed:
            self.ui.line("--- thinking ---")
            limit = self.config.get("thinking_print_limit")
            if reasoning:
                text = reasoning if len(reasoning) <= limit else reasoning[:limit] + "\n...[thinking truncated]"
                self.ui.line(text)
            else:
                self.ui.line("<empty>")
            self.ui.line("--- end thinking ---")

    def enter_maintenance(self):
        if self.background_active():
            self.ui.line(
                "Stop background jobs before entering maintenance mode.")
            return
        if not self.ui.ask_yes_no("Reset into maintenance REPL without starting the agent?", False):
            return
        write_text(join_path(self.config.root, "maintenance.flag"), "deployment\n")
        self.ui.line("Maintenance flag saved. Resetting; the app will stay stopped for deployment.")
        self.supervisor.feed()
        import machine
        machine.reset()

    def factory_reset(self):
        if self.background_active():
            self.ui.line(
                "Stop background jobs before resetting the device.")
            return
        self.ui.line("Factory reset deletes all user-defined data and reboots:")
        self.ui.line("  config.json, secrets.json, MEMORY.md, recovery files,")
        self.ui.line("  /skills, legacy /tools, /workspace and the conversation.")
        self.ui.line("Firmware, boot files and the public CA bundle are kept.")
        if not self.ui.ask_yes_no("Delete all user-defined data?", False):
            return
        if self.ui.readline("Type RESET to confirm: ").strip() != "RESET":
            self.ui.line("Factory reset cancelled.")
            return
        removed = self.factory_reset_data()
        self.ui.line("Deleted: %s" % (
            ", ".join(removed) if removed else "(no user files found)"))
        self.ui.line("Rebooting...")
        self.supervisor.feed()
        import machine
        machine.reset()

    def _ap_settings(self):
        ssid = self.network.ap_ssid(self.config.get("ap_ssid_prefix", "ESP32-Harness"))
        password = self.config.secret("ap_password")
        return ssid, password

    def start_setup_ap(self):
        status = self.network.status()
        if status.get("ap_active"):
            return status
        ssid, password = self._ap_settings()
        return self.network.start_ap(
            ssid, password, channel=self.config.get("ap_channel", 6)
        )

    def boot_network(self):
        if not self.config.get("web_enabled", True):
            self._network_ready = True
            return
        ssid = self.config.secret("wifi_ssid")
        password = self.config.secret("wifi_password")
        password_set = self.web_password_set()
        if not ssid:
            try:
                self.start_setup_ap()
            except Exception as exc:
                self.ui.line("AP start failed: %s" % exc)
            self._network_ready = True
            return
        if not password_set:
            try:
                self.start_setup_ap()
            except Exception as exc:
                self.ui.line("AP start failed: %s" % exc)
        boot_timeout = min(
            int(self.config.get("wifi_timeout", 25)),
            int(self.config.get("web_boot_timeout_s", 10)),
        )
        self.ui.line("Connecting to Wi-Fi '%s'..." % ssid)
        try:
            status = self.network.connect(
                ssid, password, timeout=boot_timeout, force=False,
                grace_ms=self.config.get("ap_grace_ms", 300000) if password_set else None,
            )
            self.ui.line("Wi-Fi connected: %s" % status.get("sta_ip", ""))
        except Exception as exc:
            self.ui.line("Wi-Fi connection failed: %s" % exc)
            if not self.network.status().get("ap_active"):
                try:
                    self.start_setup_ap()
                except Exception as ap_exc:
                    self.ui.line("AP start failed: %s" % ap_exc)
        self._network_ready = True

    def web_password_set(self):
        return bool(
            self.config.secret("web_password_hash")
            and self.config.secret("web_password_salt")
        )

    def _web_auth(self):
        if self.web_auth is None:
            from .webauth import WebAuth
            self.web_auth = WebAuth(self.config)
        return self.web_auth

    def start_web(self, force=False):
        if not force and not self.config.get("web_enabled", True):
            return False
        try:
            from .web import WebServer
            from .webapp import WebApp
            auth = self._web_auth()
            if self.web_app is None:
                self.web_app = WebApp(self, auth)
            if self.web is None:
                self.web = WebServer(
                    self.web_app,
                    port=int(self.config.get("web_port", 80)),
                )
            if not self.web.start():
                self.web_error = self.web.error or "web server did not start"
                self.ui.line("Web console failed to start: %s" % self.web_error)
                return False
            self.web_error = ""
            return True
        except Exception as exc:
            self.web_error = str(exc)
            self.ui.line("Web console unavailable: %s" % exc)
            return False

    def web_urls(self):
        status = self.network.status()
        port = int(self.config.get("web_port", 80))
        suffix = "" if port == 80 else ":%s" % port
        urls = []
        if status.get("sta_ip"):
            urls.append("http://%s%s/" % (status["sta_ip"], suffix))
        if status.get("ap_active") and status.get("ap_ip"):
            urls.append("http://%s%s/" % (status["ap_ip"], suffix))
        return urls

    def tick(self):
        if not self._network_ready:
            return
        if self.pending_wifi is not None and not self.is_busy():
            if self.begin_operation("wifi"):
                ssid, password = self.pending_wifi
                self.pending_wifi = None
                try:
                    self.network.connect(
                        ssid, password,
                        timeout=self.config.get("wifi_timeout", 25),
                        force=True,
                        grace_ms=self.config.get("ap_grace_ms", 300000),
                    )
                except Exception as exc:
                    self.queue_notice("Wi-Fi connection failed: %s" % exc)
                finally:
                    self.end_operation()
        if not self.is_busy() and self.config.get("ap_fallback_enabled", True):
            if self.begin_operation("wifi-maintain"):
                try:
                    ssid = self.config.secret("wifi_ssid")
                    password = self.config.secret("wifi_password")
                    ap_ssid, ap_password = self._ap_settings()
                    self.network.maintain(
                        ssid, password, ap_ssid, ap_password,
                        grace_ms=self.config.get("ap_grace_ms", 300000),
                        password_set=self.web_password_set(),
                        enabled=True,
                        lost_grace_ms=self.config.get("wifi_lost_grace_ms", 30000),
                        reconnect_interval=self.config.get("wifi_reconnect_interval", 60000),
                        timeout=min(
                            int(self.config.get("wifi_timeout", 25)),
                            int(self.config.get("web_boot_timeout_s", 10)),
                        ),
                        channel=self.config.get("ap_channel", 6),
                    )
                finally:
                    self.end_operation()

    def show_web_status(self):
        status = self.network.status()
        self.ui.line("Web console: %s" % (
            "enabled" if self.config.get("web_enabled", True) else "disabled"))
        self.ui.line("  port: %s; server: %s" % (
            self.config.get("web_port", 80),
            "running" if self.web is not None and self.web.alive else "stopped"))
        if self.web_error:
            self.ui.line("  error: %s" % self.web_error)
        sta_text = status.get("sta_ip", "") or "-"
        if status.get("sta_ssid"):
            sta_text = "%s (%s)" % (sta_text, status["sta_ssid"])
        self.ui.line("  mode: %s; STA: %s; AP: %s" % (
            status.get("mode", "off"), sta_text,
            status.get("ap_ip", "") or "-"))
        for url in self.web_urls():
            self.ui.line("  URL: %s" % url)
        self.ui.line("  admin password: %s; AP password: %s; sessions: %s" % (
            "set" if self.web_password_set() else "not set",
            "set" if self.config.secret("ap_password") else "not set",
            self.web_auth.session_count() if self.web_auth is not None else 0))
        if status.get("ap_active") and status.get("ap_ssid"):
            self.ui.line("  AP SSID: %s" % status["ap_ssid"])
            remaining = int(status.get("ap_off_in_s") or 0)
            if remaining > 0:
                self.ui.line("  AP closes in: %s s" % remaining)

    def web_command(self, args):
        if not args:
            self.show_web_status()
            return
        if args[0].lower() != "reset-password":
            raise ValueError("Usage: /web or /web reset-password")
        if not self.ui.ask_yes_no(
                "Reset the web admin password and start the setup AP?", False):
            self.ui.line("Web password reset cancelled.")
            return
        self._web_auth().clear_password()
        if not self.config.get("web_enabled", True):
            self.config.set("web_enabled", True)
        self.start_web(force=True)
        try:
            self.start_setup_ap()
        except Exception as exc:
            self.ui.line("AP start failed: %s" % exc)
        self.ui.line("Web admin password cleared.")
        for url in self.web_urls():
            self.ui.line("Setup URL: %s" % url)
        if self.network.status().get("ap_ssid"):
            self.ui.line("AP SSID: %s" % self.network.status()["ap_ssid"])
            if self.config.secret("ap_password") == "esp32setup":
                self.ui.line("AP password: esp32setup (default)")
            else:
                self.ui.line("AP password: configured")

    def configure_web_password(self):
        if not self.config.get("web_enabled", True):
            return
        if self.web_password_set():
            return
        if not self.ui.ask_yes_no("Set a web admin password now?", True):
            return
        first = self.ui.readline("Web admin password (8-128 chars): ", secret=True)
        second = self.ui.readline("Repeat web admin password: ", secret=True)
        if first != second:
            self.ui.line("Passwords do not match; web password unchanged.")
            return
        try:
            self._web_auth().set_password(first)
            self.ui.line("Web admin password saved.")
        except Exception as exc:
            self.ui.line("Web password was not saved: %s" % exc)

    def prepare_network(self):
        self.config.validate_wifi()
        self.network.connect(
            self.config.secret("wifi_ssid"),
            self.config.secret("wifi_password"),
            self.config.get("wifi_timeout"),
            grace_ms=self.config.get("ap_grace_ms", 300000),
        )
        if self.config.get("tls_verify"):
            try:
                self.network.sync_time(self.config.get("ntp_server"))
            except Exception:
                if not self.network.time_is_valid():
                    raise

    def prepare_cloud(self):
        self.prepare_network()
        self.config.validate_model()

    def handle_task(self, text):
        if not self.begin_operation("serial"):
            self.ui.line(
                "Busy: a web operation is in progress; "
                "read-only commands remain available."
            )
            return
        try:
            self._handle_task(text)
        finally:
            self.end_operation()

    def _handle_task(self, text):
        self.prepare_for_new_turn()
        self.prepare_cloud()
        explanation, has_code = self.agent.ask(text)
        auto = bool(has_code and self.config.get("auto_run"))
        if auto and not auto_source_is_safe(self.agent.pending_source):
            self.ui.line(
                "Auto-run blocked: generated code contains an obvious indefinite loop; "
                "review it and use /run if intended."
            )
            auto = False
        self.show_response(explanation, has_code, auto=auto)
        if auto:
            self.run_pending(auto=True)

    def show_response(self, explanation, has_code, auto=False):
        streamed = self.agent.last_streamed
        if explanation and not streamed:
            live = getattr(self, "live", None)
            if live is not None:
                live.append_content(explanation)
            self.ui.line(explanation)
        question = getattr(self.agent, "pending_question", None)
        if question is not None:
            self.ui.line(format_question(question))
            return
        if self.agent.pending_skill is not None:
            self.offer_pending_skill()
        elif has_code:
            if auto:
                self.ui.line("--- code received; auto-run enabled ---")
            elif streamed:
                self.ui.line("--- code received; use /run to confirm or /discard to reject it ---")
            else:
                self.show_pending()
                self.ui.line("Review the code, then use /run to confirm or /discard to reject it.")

    def offer_pending_skill(self):
        proposal = self.agent.pending_skill
        if proposal is None:
            return
        self.ui.line("--- proposed skill: %s ---" % proposal["name"])
        self.ui.line("description: %s" % proposal.get("description", ""))
        self.ui.line("--- SKILL.md ---")
        self.ui.line(proposal.get("body", ""))
        self.ui.line("--- end SKILL.md ---")
        if getattr(self, "_auto_continue_running", False):
            # AUTO continuation runs on the serial main thread even when no
            # terminal user is present. Blocking here would hold the turn lock
            # until someone answers; leave the proposal for /code or the web UI.
            self.ui.line(
                "Skill proposal is pending; save or discard it from the web "
                "console, or use /code on the serial console.")
            return
        if self.ui.ask_yes_no(
            "Save skill '%s' to /skills/%s/SKILL.md?" % (
                proposal["name"], proposal["name"]),
            False,
        ):
            metadata = self.agent.commit_pending_skill()
            self.ui.line("Saved skill '%s' (%s chars)." % (
                proposal["name"], metadata.get("chars", 0)))
        else:
            self.agent.discard_pending_skill()
            self.ui.line("Skill proposal discarded.")

    @staticmethod
    def _timeout_label(timeout):
        return "infinite" if timeout == 0 else "%ss" % timeout

    def show_pending(self):
        sink = self.ui.sink()
        if sink is not None:
            if self.agent.pending_source is not None:
                timeout = self.agent.pending_timeout
                if timeout is None:
                    timeout = self.config.get("code_timeout_s", 20)
                sink.line("timeout=%s" % self._timeout_label(timeout))
                sink.line(self.agent.pending_source)
            elif self.agent.pending_skill is not None:
                sink.line("Pending skill: %s" % self.agent.pending_skill.get("name", "?"))
            else:
                sink.line("No pending code.")
            return
        if self.agent.pending_source is None:
            if self.agent.pending_skill is not None:
                self.offer_pending_skill()
                return
            self.ui.line("No pending code.")
            return
        timeout = self.agent.pending_timeout
        if timeout is None:
            timeout = self.config.get("code_timeout_s", 20)
        self.ui.line("--- pending MicroPython (timeout=%s) ---"
                     % self._timeout_label(timeout))
        self.ui.line(self.agent.pending_source)
        self.ui.line("--- end code ---")

    def run_pending(self, auto=False, background=False, note=""):
        if self.agent.pending_source is None:
            self.ui.line("No pending code.")
            return
        if not auto and not self.ui.ask_yes_no("Execute this code locally on ESP32?", False):
            self.ui.line("Execution cancelled; code remains pending.")
            return
        if not auto_source_is_safe(self.agent.pending_source):
            self.ui.line(
                "WARNING: this code contains an obvious indefinite loop. "
                "The cooperative timeout should stop it, but code that bypasses "
                "all hooks may still trigger the watchdog."
            )
        if background:
            try:
                job = self.start_background(
                    auto=auto, driver="serial", note=note)
            except Exception as exc:
                self.ui.line("Could not start background execution: %s" % exc)
                return
            self.ui.line(
                "Background job #%s started (%s). Conversation is released; "
                "use /background to list jobs and /continue %s when it finishes."
                % (job.get("id", "?"), job.get("phase", "?"), job.get("id", "?")))
            return
        result = self.agent.execute_pending(self.executor)
        self.show_result(result)
        if self.agent.recovered_execution:
            self.ui.line("Recovered code executed; automatic feedback skipped because the original task context is unavailable.")
            return
        if not self.config.get("auto_feedback"):
            return
        self._feedback_loop(auto)

    def _feedback_loop(self, auto):
        while True:
            if not self.agent.can_continue():
                self.ui.line(
                    "Maximum automatic feedback turns reached; waiting for a new task. "
                    "Use /auto turns N to raise max_turns."
                )
                return
            self.ui.line("Sending bounded execution result to the model...")
            self.prepare_cloud()
            explanation, has_code = self.agent.continue_feedback()
            auto_code = bool(auto and self.config.get("auto_run") and has_code)
            if auto_code and not auto_source_is_safe(self.agent.pending_source):
                self.ui.line(
                    "Auto-run blocked: repaired code contains an obvious indefinite loop; "
                    "review it and use /run if intended."
                )
                auto_code = False
            self.show_response(explanation, has_code, auto=auto_code)
            if not auto_code:
                return
            result = self.agent.execute_pending(self.executor)
            self.show_result(result)
            if self.agent.recovered_execution:
                self.ui.line("Recovered code executed; automatic feedback skipped because the original task context is unavailable.")
                return

    def background_command(self, args):
        action = args[0].lower() if args else "list"
        if action in ("list", "ls"):
            for line in self.background_summary_lines():
                self.ui.line(line)
            return
        if action in ("status", "show"):
            if len(args) < 2:
                for line in self.background_summary_lines():
                    self.ui.line(line)
                return
            view = self.background_view(args[1])
            if view.get("phase", "none") == "none":
                self.ui.line("No background job #%s." % args[1])
                return
            self.ui.line("Background job #%s: %s" % (
                view.get("id"), view.get("phase")))
            self.ui.line("note: %s" % (view.get("note") or "<none>"))
            self.ui.line("task: %s" % (view.get("task") or "<unknown>"))
            self.ui.line("elapsed_ms: %s; timeout: %s; driver: %s; source: %s chars" % (
                view.get("elapsed_ms", 0),
                BackgroundPool.timeout_label(view.get("timeout_s")),
                view.get("driver") or "-", view.get("source_chars", 0)))
            if view.get("stop_requested"):
                self.ui.line("stop requested: %s" % (
                    view.get("stop_reason") or "yes"))
            result = view.get("result")
            if isinstance(result, dict):
                if result.get("cancelled"):
                    status = "cancelled"
                elif result.get("timed_out"):
                    status = "timeout"
                elif result.get("ok"):
                    status = "ok"
                else:
                    status = "error"
                self.ui.line("result: %s (%sms)" % (
                    status, result.get("elapsed_ms", 0)))
                output = result.get("output") or ""
                if output:
                    self.ui.line("--- output tail ---")
                    self.ui.line(output[-2000:])
                if result.get("error"):
                    self.ui.line("error: %s" % result["error"][:500])
            if view.get("error"):
                self.ui.line("error: %s" % view["error"][:500])
            return
        if action in ("stop", "cancel"):
            job_id = args[1] if len(args) > 1 else None
            target = self.background_view(job_id) if job_id is not None else None
            if target is not None and target.get("phase") == "none":
                self.ui.line("No background job #%s." % job_id)
                return
            count = self.stop_background(
                "requested from serial", job_id=job_id)
            if count:
                self.ui.line(
                    "Stop requested for %s background job(s); it takes effect "
                    "at the next cooperative checkpoint." % count)
            else:
                self.ui.line("No running background job to stop.")
            return
        if action == "note":
            if len(args) < 3:
                raise ValueError("Usage: /background note ID TEXT")
            job = self.backgrounds.note(args[1], " ".join(args[2:]))
            self.ui.line("Background job #%s note: %s" % (
                job.get("id"), job.get("note")))
            return
        if action == "discard":
            if len(args) != 2:
                raise ValueError("Usage: /background discard ID")
            if self.discard_background(record=True, job_id=args[1]):
                self.ui.line("Background job #%s discarded." % args[1])
            return
        raise ValueError(
            "Usage: /background [list|status ID|stop [ID]|note ID TEXT|"
            "discard ID]")

    def continue_background(self, job_id=None):
        job = None
        if job_id is not None and self.backgrounds is not None:
            job = self.backgrounds.get(job_id)
            if job is None:
                self.ui.line("No background job #%s." % job_id)
                return
        elif self.backgrounds is not None:
            ready = self.backgrounds.ready_jobs()
            job = ready[0] if ready else self.backgrounds.primary()
        if job is None:
            self.ui.line("No background result to continue.")
            return
        phase = job.get("phase")
        if phase in ("queued", "running"):
            self.ui.line("Background execution is still running; wait for it to finish.")
            return
        if phase == "error":
            self.ui.line("Background execution #%s failed: %s" % (
                job.get("id", "?"), job.get("error") or "unknown error"))
            try:
                self.discard_background(job_id=job.get("id"))
            except Exception:
                pass
            return
        try:
            job = self.take_background_result(job_id=job.get("id"))
        except Exception as exc:
            self.ui.line("Could not continue: %s" % exc)
            return
        result = job.get("result")
        if result is None:
            self.ui.line("The background result is unavailable.")
            return
        self.show_result(result)
        if job.get("recovered"):
            self.ui.line(
                "Recovered code executed; automatic feedback skipped because "
                "the original task context is unavailable.")
            return
        if not self.config.get("auto_feedback"):
            return
        self._feedback_loop(bool(job.get("auto")))

    def rewind_command(self, args):
        count = 1
        if args:
            if len(args) != 1:
                raise ValueError("Usage: /undo [number]")
            try:
                count = int(args[0])
            except ValueError:
                raise ValueError("Usage: /undo [number]")
        if self.background_active():
            self.ui.line("Cannot rewind while background code is running.")
            return
        if self.backgrounds is not None:
            try:
                self.discard_all_backgrounds(record=False)
            except Exception:
                pass
        try:
            index = self.agent.find_user_index(count)
            target = self.agent.history[index].get("content", "")
            removed = self.agent.rewind_to(index)
        except AgentError as exc:
            self.ui.line(str(exc))
            return
        preview = target.replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "..."
        self.ui.line(
            "Rewound context to before: \"%s\" (removed %s message(s); %s remain)."
            % (preview, removed, len(self.agent.history)))

    def show_result(self, result):
        sink = self.ui.sink()
        if sink is not None:
            sink.execution(result)
            return
        # The background card (web) or serial output already shows this
        # result. Do not also inject an execution event into the AUTO live
        # stream: that produced a second "background finished" card before
        # the streamed reply.
        self.ui.line("--- execution: %s (%s ms) ---" % (
            "TIMEOUT" if result.timed_out else ("OK" if result.ok else "ERROR"),
            result.elapsed_ms))
        if result.output and not result.live:
            self.ui.line(result.output)
        if result.error:
            self.ui.line(result.error)

    def configure_auto(self, args):
        run = bool(self.config.get("auto_run"))
        if not args:
            self.show_auto(run)
            return
        if len(args) == 1 and args[0].lower() in ("on", "off"):
            run = args[0].lower() == "on"
            self.config.set("auto_run", run)
        elif len(args) == 2 and args[0].lower() == "run" and args[1].lower() in ("on", "off"):
            run = args[1].lower() == "on"
            self.config.set("auto_run", run)
        elif len(args) == 2 and args[0].lower() in ("turns", "max"):
            try:
                turns = int(args[1])
            except (TypeError, ValueError):
                raise ValueError("Feedback turns must be an integer")
            if turns < 1 or turns > 50:
                raise ValueError("Feedback turns must be between 1 and 50")
            self.config.set("max_turns", turns)
            self.ui.line("Maximum automatic feedback turns: %s" % turns)
            return
        else:
            raise ValueError(
                "Usage: /auto [on|off], /auto run <on|off>, or /auto turns <1-50>"
            )
        self.show_auto(run)
        if run:
            self.ui.line(
                "WARNING: auto mode executes generated code without per-action "
                "confirmation. Use only in a trusted session."
            )

    def configure_native(self, args):
        enabled = bool(self.config.get("native_tools", True))
        if not args:
            self.ui.line("Native function calling: %s" % (
                "on" if enabled else "off"))
            return
        if len(args) != 1 or args[0].lower() not in ("on", "off"):
            raise ValueError("Usage: /native [on|off]")
        enabled = args[0].lower() == "on"
        self.config.set("native_tools", enabled)
        self.agent._native_tools_failed = False
        self.agent._system_tokens = None
        self.agent._system_tokens_at = None
        self.ui.line("Native function calling: %s" % (
            "on" if enabled else "off"))

    def show_auto(self, run):
        self.ui.line("Auto mode: %s" % ("on" if run else "off"))
        self.ui.line("  auto-run generated code: %s" % ("on" if run else "off"))
        self.ui.line("  max automatic feedback turns: %s" % self.config.get("max_turns", 8))

    def show_workspace_summary(self):
        summary = self.workspace.summary()
        self.ui.line("Workspace: %s" % summary["root"])
        self.ui.line("  files: %s/%s; chars: %s/%s" % (
            summary["files"], summary["files_limit"],
            summary["chars"], summary["total_limit"]))
        self.ui.line("  tmp: %s files, %s chars" % (
            summary["tmp_files"], summary["tmp_chars"]))

    def confirm_workspace_run(self, path, source):
        sink = self.ui.sink()
        if sink is not None:
            sink.notice(
                "Workspace script execution requires serial confirmation "
                "or auto_run; request cancelled."
            )
            return False
        self.ui.line("--- workspace script: %s ---" % path)
        self.ui.line(source)
        self.ui.line("--- end workspace script ---")
        if not auto_source_is_safe(source):
            self.ui.line(
                "WARNING: script contains an obvious indefinite loop; "
                "cooperative timeout should stop it, but code that bypasses "
                "hooks may trigger the watchdog."
            )
        return self.ui.ask_yes_no(
            "Execute workspace script '%s' locally?" % path, False
        )

    def confirm_mcp_call(self, server, tool, args):
        sink = self.ui.sink()
        if sink is not None:
            sink.notice(
                "MCP tool '%s' requires confirmation. Enable auto for server "
                "'%s' or run it from the serial console." % (tool, server)
            )
            return False
        if getattr(self, "_auto_continue_running", False):
            # Automatic background continuation may run without a terminal
            # user. Do not block the main thread waiting for an answer.
            self.ui.line(
                "MCP tool '%s' requires confirmation; enable auto for server "
                "'%s' or run it from the serial console later." % (
                    tool, server))
            return False
        self.ui.line("--- MCP tool call: %s (server %s) ---" % (tool, server))
        self.ui.line(json.dumps(args or {}))
        return self.ui.ask_yes_no("Execute this remote MCP tool?", False)

    def show_mcp(self):
        status = self.mcp.status()
        self.ui.line("MCP: %s; loaded=%s; tools=%s" % (
            "enabled" if status["enabled"] else "disabled",
            status["loaded"], status["tools"]))
        servers = status["servers"]
        if not servers:
            self.ui.line(
                "No MCP servers configured. Use /mcp add <name> <url> "
                "[Header=Value ...]"
            )
            return
        for server in servers:
            self.ui.line(
                "- %s: %s; enabled=%s auto=%s timeout=%ss protocol=%s headers=%s"
                % (
                    server["name"], server["url"],
                    server["enabled"], server["auto"],
                    server["timeout_s"], server["protocol"],
                    ",".join(server.get("header_names") or []) or "none",
                )
            )
        if status["errors"]:
            self.ui.line("MCP errors:")
            for name in status["errors"]:
                self.ui.line("  %s: %s" % (name, status["errors"][name]))
        if status["tool_names"]:
            self.ui.line("Loaded tools:")
            for name in status["tool_names"]:
                self.ui.line("  " + name)

    def _mcp_servers(self):
        return self.mcp_store.load()

    @staticmethod
    def _mcp_find(servers, name):
        for server in servers:
            if server["name"] == name:
                return server
        raise ValueError("Unknown MCP server: %s" % name)

    def mcp_command(self, args):
        if not args or args[0].lower() in ("list", "status"):
            self.show_mcp()
            return
        action = args[0].lower()
        if action == "refresh":
            self.ui.line("Refreshing MCP servers...")
            self.mcp.refresh()
            self.show_mcp()
            return
        if action == "tools":
            self.mcp.ensure_loaded()
            status = self.mcp.status()
            selected = args[1].lower() if len(args) > 1 else ""
            for tool in self.mcp.tools:
                if selected and tool["server"] != selected:
                    continue
                self.ui.line("- %s: %s" % (
                    tool["exposed"], tool["description"] or "<no description>"))
            if not self.mcp.tools:
                self.ui.line("No MCP tools loaded.")
            if status["errors"]:
                for name in status["errors"]:
                    self.ui.line("MCP error %s: %s" % (name, status["errors"][name]))
            return
        if action == "add":
            if len(args) < 3:
                raise ValueError(
                    "Usage: /mcp add <name> <url> [Header=Value ...]")
            name = args[1].lower()
            url = args[2]
            servers = self._mcp_servers()
            for server in servers:
                if server["name"] == name:
                    raise ValueError("MCP server already exists: %s" % name)
            headers = {}
            index = 3
            while index < len(args):
                token = args[index]
                if "=" not in token:
                    raise ValueError("MCP header must be Header=Value")
                key, value = token.split("=", 1)
                while (index + 1 < len(args) and "=" not in args[index + 1]):
                    index += 1
                    value += " " + args[index]
                headers[key] = value
                index += 1
            servers.append({
                "name": name, "url": url, "headers": headers,
                "enabled": True, "auto": False,
                "timeout_s": self.config.get("mcp_timeout_s", 60),
                "protocol": "auto",
            })
            self.mcp_store.save(servers)
            self.mcp.reload()
            self.ui.line("MCP server saved. Use /mcp refresh to load tools.")
            self.show_mcp()
            return
        if action == "remove":
            if len(args) != 2:
                raise ValueError("Usage: /mcp remove <name>")
            name = args[1].lower()
            servers = [server for server in self._mcp_servers()
                       if server["name"] != name]
            self.mcp_store.save(servers)
            self.mcp.reload()
            self.ui.line("MCP server removed: %s" % name)
            self.show_mcp()
            return
        if action in ("enable", "auto", "timeout", "protocol"):
            if len(args) != 3:
                raise ValueError(
                    "Usage: /mcp %s <name> <value>" % action)
            name = args[1].lower()
            servers = self._mcp_servers()
            server = self._mcp_find(servers, name)
            value = args[2].lower()
            if action in ("enable", "auto"):
                if value not in ("on", "off"):
                    raise ValueError("Use on or off")
                key = "enabled" if action == "enable" else "auto"
                server[key] = value == "on"
            elif action == "timeout":
                try:
                    timeout = int(args[2])
                except (TypeError, ValueError):
                    raise ValueError("Timeout must be an integer number of seconds")
                server["timeout_s"] = max(5, min(timeout, 300))
            else:
                if value not in ("auto", "legacy", "modern", "2026-07-28"):
                    raise ValueError("Protocol must be auto, legacy or modern")
                server["protocol"] = (
                    "2026-07-28" if value == "modern" else value)
            self.mcp_store.save(servers)
            self.mcp.reload()
            self.show_mcp()
            return
        if action == "read":
            if len(args) != 3:
                raise ValueError("Usage: /mcp read <server> <uri>")
            self.ui.line(self.mcp.read_resource(args[1].lower(), args[2]))
            return
        if action == "prompt":
            if len(args) < 3:
                raise ValueError(
                    "Usage: /mcp prompt <server> <name> [json-args]")
            prompt_args = {}
            if len(args) > 3:
                try:
                    prompt_args = json.loads(" ".join(args[3:]))
                except ValueError:
                    raise ValueError("Prompt arguments must be a JSON object")
                if not isinstance(prompt_args, dict):
                    raise ValueError("Prompt arguments must be a JSON object")
            self.ui.line(self.mcp.get_prompt(
                args[1].lower(), args[2], prompt_args))
            return
        raise ValueError(
            "Usage: /mcp [list|status|refresh|tools [server]|"
            "add|remove|enable|auto|timeout|protocol|read|prompt]"
        )

    def search_command(self, args):
        if not args:
            raise ValueError(
                "Usage: /search <query> | /search provider auto|bing | "
                "/search on|off"
            )
        action = args[0].lower()
        if action == "provider":
            if len(args) != 2 or args[1].lower() not in SEARCH_PROVIDERS:
                raise ValueError("Usage: /search provider auto|bing")
            self.config.set("search_provider", args[1].lower())
            self.ui.line("Search provider set to %s." % args[1].lower())
            return
        if action == "key":
            raise ValueError(
                "The DeepSeek search key was removed; use auto or bing"
            )
        if action in ("on", "off"):
            if len(args) != 1:
                raise ValueError("Usage: /search on|off")
            self.config.set("search_enabled", action == "on")
            self.ui.line("Web search %s." % (
                "enabled" if action == "on" else "disabled"))
            return
        self.prepare_cloud()
        result = self.agent._run_search_tool({"query": " ".join(args)})
        self.show_tool_result("__search", result)
        self.agent.record_local_observation(
            "Local web search result:\n%s"
            % result.observation(
                self.config.get("search_output_limit", 8192) + 2048
            )
        )

    def http_command(self, args):
        if len(args) < 2:
            raise ValueError(
                "Usage: /http get|head|download <url> [workspace-path]"
            )
        action = args[0].lower()
        if action not in ("get", "head", "download"):
            raise ValueError("HTTP action must be get, head or download")
        payload = {"action": action, "url": args[1]}
        if action == "download" and len(args) >= 3:
            payload["path"] = args[2]
        self.prepare_network()
        result = self.agent._run_http_tool(payload)
        self.show_tool_result("__http", result)
        if result.ok:
            self.agent.record_local_observation(
                "Local HTTP result:\n%s"
                % result.observation(
                    self.config.get("http_tool_output_limit", 8192) + 2048
                )
            )

    def workspace_command(self, args):
        if not args:
            self.show_workspace_summary()
            entries = self.workspace.list("")
            if entries:
                for entry in entries:
                    self.ui.line("  %s (%s, %s chars)" % (
                        entry["path"], entry["kind"], entry["size"]))
            else:
                self.ui.line("  workspace is empty")
            return
        action = args[0].lower()
        if action == "list":
            path = args[1] if len(args) == 2 else ""
            entries = self.workspace.list(path)
            if not entries:
                self.ui.line("Workspace directory is empty.")
                return
            for entry in entries:
                self.ui.line("%s (%s, %s chars)" % (
                    entry["path"], entry["kind"], entry["size"]))
            return
        if action == "read":
            if len(args) != 2:
                raise ValueError("Usage: /workspace read <path>")
            bundle = self.workspace.read(args[1])
            self.ui.line("--- %s (%s chars%s) ---" % (
                bundle["path"], bundle["chars"],
                ", truncated" if bundle["truncated"] else ""))
            self.ui.line(bundle["text"])
            self.ui.line("--- end workspace file ---")
            return
        if action == "run":
            background = False
            timeout = None
            if len(args) in (3, 4) and args[1].lower() == "bg":
                background = True
                path = args[2]
                if len(args) == 4:
                    try:
                        timeout = int(args[3])
                    except (TypeError, ValueError):
                        raise ValueError("Background timeout must be an integer")
                    if timeout < 0:
                        raise ValueError("Background timeout must be >= 0")
            elif len(args) == 2:
                path = args[1]
            else:
                raise ValueError(
                    "Usage: /workspace run [bg [timeout]] <path>")
            if not path.endswith(".py"):
                raise ValueError("Workspace execution requires a .py file")
            run_limit = self.workspace.source_limit()
            bundle = self.workspace.read_source(path, limit=run_limit + 1)
            if bundle["truncated"] or len(bundle["text"]) > run_limit:
                raise ValueError(
                    "Workspace script exceeds workspace_file_limit=%s"
                    % run_limit
                )
            self.ui.line("--- workspace script: %s (%s chars) ---" % (
                bundle["path"], bundle["chars"]))
            self.ui.line(bundle["text"])
            self.ui.line("--- end workspace script ---")
            if not auto_source_is_safe(bundle["text"]):
                self.ui.line(
                    "WARNING: script contains an obvious indefinite loop; "
                    "execution may trigger the watchdog."
                )
            if not self.ui.ask_yes_no(
                "Execute workspace script '%s' %s?" % (
                    bundle["path"], "in the background" if background else "locally"),
                False,
            ):
                return
            if background:
                if not self.config.get("background_execution", True):
                    raise ValueError("Background execution is disabled")
                if timeout is None:
                    timeout = self.config.get("code_timeout_max_s", 300)
                job = self.start_background_source(
                    bundle["text"],
                    timeout=timeout,
                    auto=False,
                    driver="workspace",
                    note="workspace: %s" % bundle["path"],
                )
                self.ui.line(
                    "Started background job #%s for %s (timeout=%s). "
                    "Use /background status %s." % (
                        job.get("id", "?"), bundle["path"],
                        job.get("timeout"), job.get("id", "?")))
                return
            result = self.executor.execute(bundle["text"])
            self.show_result(result)
            self.agent.record_local_observation(
                "Local workspace script result for '%s':\n%s" % (
                    bundle["path"],
                    result.observation(
                        self.config.get("tool_output_limit", 4096)
                    ),
                )
            )
            return
        if action == "mkdir":
            if len(args) != 2:
                raise ValueError("Usage: /workspace mkdir <path>")
            result = self.workspace.mkdir(args[1])
            self.ui.line("Created %s." % result["path"])
            return
        if action == "remove":
            if len(args) != 2:
                raise ValueError("Usage: /workspace remove <path>")
            if self.ui.ask_yes_no("Remove workspace path '%s'?" % args[1], False):
                result = self.workspace.remove(args[1])
                self.ui.line("Removed %s." % result["path"])
            return
        if action == "clean":
            if self.ui.ask_yes_no("Clear /workspace/tmp?", False):
                self.workspace.clean_tmp()
                self.ui.line("Temporary workspace cleared.")
                self.show_workspace_summary()
            return
        raise ValueError(
            "Usage: /workspace [list [path]|read <path>|run <path>|mkdir <path>|"
            "remove <path>|clean]"
        )

    def configure_wifi(self):
        current = self.config.secret("wifi_ssid")
        while True:
            ssid = self.ui.readline("Wi-Fi SSID [%s]: " % current) or current
            if ssid:
                break
            self.ui.line("Wi-Fi SSID cannot be empty.")
        password = self.ui.readline("Wi-Fi password (empty=keep, '-'=clear): ", secret=True)
        if not password and not current:
            password = ""
        elif password == "-":
            password = ""
        elif not password:
            password = self.config.secret("wifi_password")
        self.config.set_secret("wifi_ssid", ssid, save=False)
        self.config.set_secret("wifi_password", password, save=False)
        self.config.save_secrets()
        self.http.close()
        if self.config.get("web_enabled", True):
            try:
                self.start_setup_ap()
            except Exception as exc:
                self.ui.line("Could not start recovery AP: %s" % exc)
        self.ui.line("Wi-Fi settings saved. Connecting...")
        try:
            status = self.network.connect(
                ssid, password, self.config.get("wifi_timeout"), force=True,
                grace_ms=self.config.get("ap_grace_ms", 300000),
            )
        except Exception as exc:
            if self.config.get("web_enabled", True) and not self.network.status().get("ap_active"):
                try:
                    self.start_setup_ap()
                except Exception:
                    pass
            raise RuntimeError(
                "Wi-Fi connection failed: %s; recovery AP is available" % exc
            )
        self.ui.line("Wi-Fi connected: %s; AP remains for %s seconds if active." % (
            status["ip"], int(self.config.get("ap_grace_ms", 300000)) // 1000))

    def configure_model(self):
        current_provider = normalize_api_provider(
            self.config.get("api_provider", "openai")
        )
        provider_text = self.ui.readline(
            "API format openai|anthropic [%s]: " % current_provider
        ) or current_provider
        provider = normalize_api_provider(provider_text, "")
        if not provider:
            raise ValueError("API format must be openai or anthropic")
        current_base = str(self.config.get("base_url") or "")
        entered_base = self.ui.readline("Base URL [%s]: " % current_base)
        base_url = entered_base or current_base
        if provider != current_provider:
            # Only replace a known default endpoint when the user pressed
            # Enter; a custom proxy URL is always preserved.
            known = (
                default_base_url("openai"),
                default_base_url("anthropic"),
                "https://api.openai.com",
                "https://api.anthropic.com",
                "https://api.deepseek.com",
                "https://api.deepseek.com/v1",
            )
            known = tuple(item.rstrip("/") for item in known)
            if (not entered_base
                    and current_base.rstrip("/") in known):
                base_url = default_base_url(provider)
        model = self.ui.readline("Model [%s]: " % self.config.get("model")) or self.config.get("model")
        api_key = self.ui.readline("API key (empty=keep, '-'=clear): ", secret=True)
        if api_key == "-":
            api_key = ""
        elif not api_key:
            api_key = self.config.secret("api_key")
        self.ui.line("Custom CA configuration is usually unnecessary; keep the current path to use the installed public bundle.")
        try:
            os.stat(self.config.get("ca_file"))
        except OSError:
            self.ui.line("No CA file currently exists at %s; install the public bundle before verified HTTPS." % self.config.get("ca_file"))
        ca_file = self.ui.readline("CA PEM path [%s]: " % self.config.get("ca_file")) or self.config.get("ca_file")
        verify = self.ui.ask_yes_no("Verify TLS certificate?", self.config.get("tls_verify"))
        self.config.set("api_provider", provider, save=False)
        self.config.set("base_url", base_url.rstrip("/"), save=False)
        self.config.set("model", model, save=False)
        self.config.set("ca_file", ca_file, save=False)
        self.config.set("tls_verify", verify, save=False)
        self.config.save()
        self.config.set_secret("api_key", api_key)
        self.reload_llm()
        self.ui.line("Model settings saved (%s format)." % provider)
        if not verify:
            self.ui.line("WARNING: TLS certificate verification is DISABLED.")

    def configure_ca(self):
        self.ui.line("Paste one PEM certificate. Input ends after -----END CERTIFICATE-----.")
        lines = []
        size = 0
        while True:
            line = self.ui.readline()
            lines.append(line)
            size += len(line) + 1
            if size > 16384:
                raise ValueError("CA certificate exceeds 16384 characters")
            if "-----END CERTIFICATE-----" in line:
                break
        pem = "\n".join(lines) + "\n"
        if "-----BEGIN CERTIFICATE-----" not in pem:
            raise ValueError("Invalid PEM certificate")
        write_text(self.config.get("ca_file"), pem)
        self.http.close()
        self.ui.line("CA certificate saved to %s" % self.config.get("ca_file"))

    def show_status(self):
        info = self.board.info()
        network = self.network.status()
        self.ui.line("Firmware: MicroPython %s on %s" % (info["version"], info["platform"]))
        self.ui.line("Heap free: %s bytes; filesystem free: %s bytes" % (
            info["heap_free"], info.get("filesystem_free", "?")))
        self.ui.line("Wi-Fi: %s" % (
            "%s (%s)" % (network["ip"], network.get("sta_ssid") or "?")
            if network["connected"] else "disconnected"))
        self.ui.line("Network mode: %s; AP: %s; AP IP: %s" % (
            network.get("mode", "off"),
            network.get("ap_ssid", "") or "-",
            network.get("ap_ip", "") or "-"))
        if network.get("last_error"):
            self.ui.line("Wi-Fi error: %s" % network["last_error"])
        self.ui.line("Web console: %s; %s" % (
            "enabled" if self.config.get("web_enabled", True) else "disabled",
            ", ".join(self.web_urls()) or "no URL"))
        self.ui.line("Model API: %s; %s at %s" % (
            self.config.get("api_provider", "openai"),
            self.config.get("model") or "<unset>",
            self.config.get("base_url"),
        ))
        try:
            os.stat(self.config.get("ca_file"))
            ca_state = "present"
        except OSError:
            ca_state = "missing"
        self.ui.line("TLS verification: %s; CA: %s (%s)" % (
            self.config.get("tls_verify"), self.config.get("ca_file"), ca_state))
        self.ui.line("Pending code: %s; pending skill: %s" % (
            self.agent.pending_source is not None,
            self.agent.pending_skill is not None,
        ))
        if self.agent.pending_question is not None:
            self.ui.line("Pending question: %s" % self.agent.pending_question.get(
                "header", "Question"))
        backgrounds = self.background_views()
        if backgrounds:
            self.ui.line("Background jobs: %s active, %s total" % (
                sum(1 for job in backgrounds if job.get("active")),
                len(backgrounds)))
            for job in backgrounds:
                self.ui.line(
                    "  #%s: phase=%s; note=%s; elapsed=%sms; timeout=%s" % (
                        job.get("id"), job.get("phase"),
                        (job.get("note") or "-")[:60],
                        job.get("elapsed_ms", 0),
                        BackgroundPool.timeout_label(job.get("timeout_s"))))
        self.ui.line("Saved skills: %s; history messages: %s" % (
            len(self.skills.list()), len(self.agent.history)))
        persistence = self.conversation.status()
        self.ui.line("Persistent context: %s; %s saved message(s) at %s" % (
            "on" if persistence["enabled"] else "off",
            persistence["messages"], persistence["path"]))
        status = self.hardware.status()
        self.ui.line("Peripherals: %s; GPIO pins=%s; I2C=%s; SPI=%s; UART=%s; "
                     "NeoPixel=%s; ADC=%s; PWM=%s; touch=%s; RTC write=%s" % (
            "enabled" if status["enabled"] else "disabled",
            status["allowed_gpio_pins"],
            len(status["i2c_buses"]),
            len(status["spi_buses"]),
            len(status["uart_buses"]),
            len(status["neopixel_strips"]),
            len(status["adc_pins"]),
            len(status["pwm_pins"]),
            len(status["touch_pins"]),
            "on" if status["rtc_write"] else "off",
        ))
        self.ui.line("Auto mode: code=%s" % (
            self.config.get("auto_run", False),
        ))
        memory = self.memory.bundle()
        self.ui.line("Memory: %s/%s chars" % (memory["chars"], memory["limit"]))
        search = self.agent.search_status()
        provider = search["configured"]
        if search["effective"] != provider:
            provider += " -> " + search["effective"]
        self.ui.line("Web search: %s (provider: %s)" % (
            "enabled" if search["enabled"] else "disabled", provider))
        self.ui.line("HTTP fetch: %s" % (
            "enabled" if self.config.get("http_tool_enabled", True) else "disabled"))
        mcp = self.mcp.status()
        self.ui.line("MCP: %s; servers=%s; tools=%s" % (
            "enabled" if mcp["enabled"] else "disabled",
            len(mcp["servers"]), mcp["tools"]))
        summary = self.workspace.summary()
        self.ui.line("Workspace: %s files, %s chars; tmp %s files" % (
            summary["files"], summary["chars"], summary["tmp_files"]))
        self.ui.line("Configuration (redacted):\n" + json.dumps(self.config.public_values()))

    def test(self, args):
        self.prepare_cloud()
        self.ui.line("Wi-Fi and secure-time checks passed.")
        if args:
            if args != ["api"]:
                raise ValueError("Usage: /test or /test api")
            self.http.trace = self.ui.line
            try:
                try:
                    messages = [{"role": "user", "content": "Reply with OK only."}]
                    response = self.agent._retry_api_call(
                        "Test API request",
                        lambda: self._test_api_request(messages),
                    )
                except Exception as exc:
                    self.show_model_result(str(exc))
                    raise
                self.show_model_result("")
            finally:
                self.http.trace = self.http_trace
            if not self.llm.last_streamed:
                self.ui.line("API response: " + response)
            else:
                self.ui.line("API stream complete.")

    def _test_api_request(self, messages):
        self.show_model_start()
        if self.config.get("stream", True):
            return self.llm.complete_stream(
                messages,
                on_reasoning=self.show_reasoning_chunk,
                on_content=self.show_content_chunk,
            )
        return self.llm.complete(messages)

    def show_history(self):
        if not self.agent.history:
            self.ui.line("History is empty.")
            return
        for index, message in enumerate(self.agent.history):
            self.ui.line("[%s] %s: %s" % (index, message["role"], message["content"]))

    def show_context(self):
        usage = self.agent.context_usage(fresh=True)
        self.ui.line("Context: ~%s / %s tokens (%s%%)" % (
            usage["estimated_tokens"], usage["context_tokens"], usage["percent"]))
        self.ui.line("History: %s messages, %s chars (~%s tokens); summary: %s chars" % (
            usage["messages"], usage["chars"], usage["history_tokens"],
            usage["summary_chars"]))
        if usage["auto_compact"]:
            self.ui.line("Auto-compact: on at %s tokens; keep last %s messages" % (
                usage["auto_compact_tokens"],
                self.config.get("compact_keep_messages", 4)))
        else:
            self.ui.line("Auto-compact: off; manual /compact is available")
        self.ui.line("Streaming: %s; thinking: %s" % (
            self.config.get("stream", True), self.config.get("thinking_level")))

    def show_summary(self):
        if not self.agent.summary:
            self.ui.line("No conversation summary.")
            return
        self.ui.line("--- conversation summary (%s chars) ---" % len(self.agent.summary))
        self.ui.line(self.agent.summary)
        self.ui.line("--- end summary ---")

    def show_notice(self, text):
        sink = self.ui.sink()
        if sink is not None:
            sink.notice(text)
            return
        live = getattr(self, "live", None)
        if live is not None:
            live.add_event({"t": "notice", "d": str(text)[:256]})
        self.ui.line(text)

    def show_tool_start(self, name, args):
        sink = self.ui.sink()
        if sink is not None:
            sink.tool_start(name, args)
            return
        live = getattr(self, "live", None)
        if live is not None:
            try:
                arg_text = json.dumps(args)
            except Exception:
                arg_text = "{}"
            live.add_event({
                "t": "tool", "phase": "start", "name": str(name)[:64],
                "args": (args if len(arg_text) <= 512
                         else {"_truncated": arg_text[:512]}),
            })
        self.ui.line("--- tool call: %s %s ---" % (name, json.dumps(args)))

    def show_tool_result(self, name, result):
        sink = self.ui.sink()
        if sink is not None:
            sink.tool_result(name, result)
            return
        live = getattr(self, "live", None)
        if live is not None:
            live.add_event({
                "t": "tool", "phase": "result", "name": str(name)[:64],
                "ok": bool(result.ok),
                "output": str(result.output or "")[:512],
                "error": str(result.error or "")[:256],
                "elapsed_ms": result.elapsed_ms,
            })
        self.ui.line("--- tool result: %s %s (%s ms) ---" % (
            name, "OK" if result.ok else "ERROR", result.elapsed_ms))
        if result.output:
            if name == "__search":
                limit = self.config.get("search_output_limit", 8192)
            else:
                limit = self.config.get("tool_output_limit", 4096)
            text = result.output
            if len(text) > limit:
                text = text[:limit] + "\n...[tool display truncated]"
            self.ui.line(text)
        if result.error:
            self.ui.line(result.error)

    def configure_peripherals(self, args):
        if not args:
            self.show_peripherals()
            return
        action = args[0].lower()
        if action in ("enable", "disable"):
            self.config.set("peripherals_enabled", action == "enable")
            self.ui.line("Peripherals %s." % ("enabled" if action == "enable" else "disabled"))
            self.show_peripherals()
            return
        if action == "gpio":
            values = " ".join(args[1:]).split(",")
            pins = []
            for value in values:
                value = value.strip()
                if not value:
                    continue
                try:
                    pin = validate_pin(int(value))
                except PeripheralError as exc:
                    raise ValueError(str(exc))
                if pin not in pins:
                    pins.append(pin)
            self._save_peripheral_list("allowed_gpio_pins", pins)
            self.ui.line("Allowed GPIO pins: %s" % pins)
            self.show_peripherals()
            return
        if action in ("i2c", "spi", "uart"):
            self._configure_bus(action, args[1:])
            return
        if action == "transfer":
            if len(args) != 2:
                raise ValueError("Usage: /peripherals transfer <bytes>")
            value = int(args[1])
            if value < 1 or value > 4096:
                raise ValueError("Transfer limit must be between 1 and 4096")
            self.config.set("peripheral_max_transfer", value)
            self.ui.line("Peripheral transfer limit: %s bytes." % value)
            self.show_peripherals()
            return
        if action == "neopixel":
            self._configure_neopixel(args[1:])
            return
        if action == "adc":
            self._configure_adc(args[1:])
            return
        if action == "pwm":
            if len(args) != 2:
                raise ValueError("Usage: /peripherals pwm <pin>")
            pin = validate_pin(int(args[1]))
            current = [int(value) for value in self.config.get("pwm_pins", [])]
            if pin not in current:
                current.append(pin)
            self._save_peripheral_list("pwm_pins", current)
            self.ui.line("PWM enabled on GPIO%s." % pin)
            self.show_peripherals()
            return
        if action == "touch":
            if len(args) != 2:
                raise ValueError("Usage: /peripherals touch <pin>")
            pin = validate_pin(int(args[1]))
            if pin not in TOUCH_PINS:
                raise ValueError(
                    "GPIO%s is not a touch-capable pin (GPIO1-GPIO14)" % pin
                )
            current = [int(value) for value in self.config.get("touch_pins", [])]
            if pin not in current:
                current.append(pin)
            self._save_peripheral_list("touch_pins", current)
            self.ui.line("Touch enabled on GPIO%s." % pin)
            self.show_peripherals()
            return
        if action == "rtc":
            if (len(args) != 3 or args[1].lower() != "write"
                    or args[2].lower() not in ("on", "off")):
                raise ValueError("Usage: /peripherals rtc write on|off")
            enabled = args[2].lower() == "on"
            self.config.set("rtc_write", enabled)
            self.ui.line("RTC writes %s." % ("enabled" if enabled else "disabled"))
            self.show_peripherals()
            return
        if action == "remove":
            if len(args) != 3 or args[1] not in (
                "i2c", "spi", "uart", "neopixel", "adc", "pwm", "touch"
            ):
                raise ValueError(
                    "Usage: /peripherals remove "
                    "<i2c|spi|uart|neopixel|adc|pwm|touch> <id>"
                )
            kind = args[1]
            value = int(args[2])
            if kind in ("i2c", "spi", "uart"):
                key = kind + "_buses"
                current = list(self.config.get(key, []))
                self._save_peripheral_list(
                    key,
                    [item for item in current if int(item.get("id", -1)) != value],
                )
            elif kind == "neopixel":
                current = list(self.config.get("neopixel_strips", []))
                self._save_peripheral_list(
                    "neopixel_strips",
                    [item for item in current if int(item.get("pin", -1)) != value],
                )
            elif kind in ("pwm", "touch"):
                key = kind + "_pins"
                current = list(self.config.get(key, []))
                self._save_peripheral_list(
                    key,
                    [item for item in current if int(item) != value],
                )
            else:
                current = list(self.config.get("adc_pins", []))
                self._save_peripheral_list(
                    "adc_pins",
                    [item for item in current if int(item) != value],
                )
            self.ui.line("Removed %s %s." % (kind, value))
            self.show_peripherals()
            return
        if action == "scan":
            if len(args) != 2:
                raise ValueError("Usage: /peripherals scan <i2c-id>")
            addresses = self.hardware.i2c_scan(int(args[1]))
            self.ui.line("I2C bus %s devices: %s" % (args[1], addresses))
            return
        if action == "clear":
            self.config.set("peripherals_enabled", False, save=False)
            self.config.set("allowed_gpio_pins", [], save=False)
            self.config.set("i2c_buses", [], save=False)
            self.config.set("spi_buses", [], save=False)
            self.config.set("uart_buses", [], save=False)
            self.config.set("neopixel_strips", [], save=False)
            self.config.set("adc_pins", [], save=False)
            self.config.set("pwm_pins", [], save=False)
            self.config.set("touch_pins", [], save=False)
            self.config.set("rtc_write", False, save=False)
            self.config.save()
            self.ui.line("Peripheral configuration cleared and disabled.")
            self.show_peripherals()
            return
        raise ValueError(
            "Usage: /peripherals [enable|disable|clear|gpio <pins>|scan <i2c-id>|"
            "i2c <id> <sda> <scl> [freq]|spi <id> <sck> <mosi> <miso|-> "
            "[freq] [hw|sw]|uart <id> <tx> <rx> [baud]|neopixel <pin> "
            "[count]|adc <pin>|pwm <pin>|touch <pin>|rtc write on|off|"
            "transfer <bytes>|remove <type> <id>]"
        )

    def _configure_bus(self, kind, args):
        if kind == "i2c":
            if len(args) not in (3, 4):
                raise ValueError("Usage: /peripherals i2c <id> <sda> <scl> [freq]")
            bus_id, sda, scl = int(args[0]), validate_pin(int(args[1])), validate_pin(int(args[2]))
            freq = int(args[3]) if len(args) == 4 else 400000
            item = {"id": bus_id, "sda": sda, "scl": scl, "freq": freq}
        elif kind == "spi":
            if len(args) not in (4, 5, 6):
                raise ValueError(
                    "Usage: /peripherals spi <id> <sck> <mosi> <miso|-> "
                    "[freq] [hw|sw]"
                )
            bus_id = int(args[0])
            sck, mosi = validate_pin(int(args[1])), validate_pin(int(args[2]))
            if args[3].lower() in ("-", "none", "off", "no"):
                miso = None
            else:
                miso = validate_pin(int(args[3]))
            freq = int(args[4]) if len(args) >= 5 else 1000000
            mode = args[5].lower() if len(args) == 6 else "hw"
            if mode not in ("hw", "sw"):
                raise ValueError("SPI mode must be hw or sw")
            item = {
                "id": bus_id,
                "sck": sck,
                "mosi": mosi,
                "freq": freq,
                "mode": mode,
            }
            if miso is not None:
                item["miso"] = miso
        else:
            if len(args) not in (3, 4):
                raise ValueError("Usage: /peripherals uart <id> <tx> <rx> [baud]")
            bus_id = int(args[0])
            if bus_id not in (1, 2):
                raise ValueError("UART id must be 1 or 2; UART0 is reserved")
            tx, rx = validate_pin(int(args[1])), validate_pin(int(args[2]))
            baud = int(args[3]) if len(args) == 4 else 115200
            item = {"id": bus_id, "tx": tx, "rx": rx, "baud": baud}
        key = kind + "_buses"
        current = list(self.config.get(key, []))
        updated = []
        found = False
        for existing in current:
            if int(existing.get("id", -1)) == bus_id:
                updated.append(item)
                found = True
            else:
                updated.append(existing)
        if not found:
            updated.append(item)
        self._save_peripheral_list(key, updated)
        self.ui.line("Configured %s bus %s." % (kind, bus_id))
        self.show_peripherals()

    def _configure_neopixel(self, args):
        if len(args) not in (1, 2):
            raise ValueError("Usage: /peripherals neopixel <pin> [count]")
        pin = validate_pin(int(args[0]))
        count = int(args[1]) if len(args) == 2 else 1
        if count < 1 or count > 64:
            raise ValueError("NeoPixel count must be between 1 and 64")
        item = {"pin": pin, "count": count}
        current = list(self.config.get("neopixel_strips", []))
        updated = []
        found = False
        for existing in current:
            if int(existing.get("pin", -1)) == pin:
                updated.append(item)
                found = True
            else:
                updated.append(existing)
        if not found:
            updated.append(item)
        self._save_peripheral_list("neopixel_strips", updated)
        self.ui.line(
            "Configured NeoPixel strip on GPIO%s with %s pixel(s)." % (pin, count)
        )
        self.show_peripherals()

    def _configure_adc(self, args):
        if len(args) != 1:
            raise ValueError("Usage: /peripherals adc <pin>")
        pin = validate_pin(int(args[0]))
        current = list(self.config.get("adc_pins", []))
        if pin not in [int(value) for value in current]:
            current.append(pin)
        self._save_peripheral_list("adc_pins", current)
        self.ui.line("Configured ADC on GPIO%s." % pin)
        self.show_peripherals()

    def _save_peripheral_list(self, key, value):
        previous = self.config.get(key)
        self.config.set(key, value, save=False)
        errors = self.hardware.validate_config()
        if errors:
            self.config.set(key, previous, save=False)
            raise ValueError("; ".join(errors))
        self.config.save()

    def show_peripherals(self):
        status = self.hardware.status()
        self.ui.line("Peripherals: %s" % ("enabled" if status["enabled"] else "disabled"))
        self.ui.line("Allowed GPIO pins: %s" % status["allowed_gpio_pins"])
        self.ui.line("I2C buses: %s" % status["i2c_buses"])
        self.ui.line("SPI buses: %s" % status["spi_buses"])
        self.ui.line("UART buses: %s" % status["uart_buses"])
        self.ui.line("NeoPixel strips: %s" % status["neopixel_strips"])
        self.ui.line("ADC pins: %s" % status["adc_pins"])
        self.ui.line("PWM pins: %s" % status["pwm_pins"])
        self.ui.line("Touch pins: %s" % status["touch_pins"])
        self.ui.line("RTC writes: %s" % (
            "enabled" if status["rtc_write"] else "disabled"))
        errors = self.hardware.validate_config()
        if errors:
            self.ui.line("Configuration errors: %s" % "; ".join(errors))
        self.ui.line(
            "Usage: /peripherals enable|disable|clear|gpio <pins>|scan <i2c-id>|"
            "i2c <id> <sda> <scl> [freq]|spi <id> <sck> <mosi> <miso|-> "
            "[freq] [hw|sw]|uart <id> <tx> <rx> [baud]|neopixel <pin> "
            "[count]|adc <pin>|pwm <pin>|touch <pin>|rtc write on|off|"
            "transfer <bytes>|remove <type> <id>"
        )

    def list_skills(self):
        skills = self.skills.list()
        if not skills:
            self.ui.line("No saved skills.")
            return
        for skill in skills:
            self.ui.line("%s (%s chars): %s" % (
                skill["name"], skill.get("chars", 0),
                skill.get("description", "")))

    def show_skill(self, name):
        bundle = self.skills.read_bundle(
            name, limit=self.config.get("skill_char_limit", 16384)
        )
        self.ui.line("--- skill: %s ---" % name)
        self.ui.line("description: %s" % bundle.get("description", ""))
        self.ui.line(bundle.get("content", ""))
        self.ui.line("--- end skill ---")

    def delete_skill(self, name):
        if self.ui.ask_yes_no("Delete skill '%s'?" % name, False):
            self.skills.delete(name)
            self.ui.line("Deleted skill '%s'." % name)

    def memory_command(self, args):
        if not args:
            bundle = self.memory.bundle()
            self.ui.line("MEMORY.md: %s/%s chars%s" % (
                bundle["chars"], bundle["limit"],
                " (truncated)" if bundle["truncated"] else ""))
            self.ui.line(bundle["content"])
            return
        action = args[0].lower()
        if action == "append":
            text = " ".join(args[1:]).strip()
            if not text:
                raise ValueError("Usage: /memory append <text>")
            result = self.memory.append(text)
            self.ui.line("Memory appended (%s chars; total %s)." % (
                result["chars"], result["total"]))
            return
        if action == "delete":
            match = " ".join(args[1:]).strip()
            if not match:
                raise ValueError("Usage: /memory delete <text>")
            matches = self.memory.matching(match)
            if not matches:
                self.ui.line("No memory line contains: %s" % match)
                return
            self.ui.line("Matching memory line(s):")
            for line in matches:
                self.ui.line("  %s" % line)
            if not self.ui.ask_yes_no(
                "Delete %s memory line(s)?" % len(matches), False
            ):
                return
            result = self.memory.delete(match)
            self.ui.line("Deleted %s memory line(s); MEMORY.md is now %s chars." % (
                result["removed"], result["total"]))
            return
        if action == "replace":
            match = " ".join(args[1:]).strip()
            if not match:
                raise ValueError("Usage: /memory replace <text>")
            matches = self.memory.matching(match)
            if not matches:
                self.ui.line("No memory line contains: %s" % match)
                return
            if len(matches) > 1:
                self.ui.line(
                    "%s memory lines contain '%s'; use a more specific match."
                    % (len(matches), match)
                )
                return
            self.ui.line("Current line: %s" % matches[0])
            replacement = self.ui.readline("New memory line: ")
            if not replacement.strip():
                self.ui.line("Replacement cancelled.")
                return
            result = self.memory.replace(match, replacement)
            self.ui.line("Memory line replaced; MEMORY.md is now %s chars." % (
                result["total"]))
            return
        if action == "clear":
            if self.ui.ask_yes_no("Reset MEMORY.md?", False):
                self.memory.clear()
                self.ui.line("MEMORY.md reset.")
            return
        raise ValueError(
            "Usage: /memory [append <text>|delete <text>|replace <text>|clear]"
        )
