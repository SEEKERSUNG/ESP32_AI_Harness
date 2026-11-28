"""Fast on-device smoke; no network, no web server, no factory reset.

Run after a partial deployment:

    mpremote connect <PORT> run tools/device_quick_smoke.py

It imports every module touched by the usual change loop, checks the current
timeout/question limits, runs one fake Agent turn and executes one tiny
program. Use tools/device_smoke.py for the full hardware/thread regression.
"""

import builtins
import gc
import os
import sys
import time

from harness.agent import (
    Agent,
    MAX_QUESTION_OPTIONS,
    SEARCH_PROVIDERS,
    builtin_tool_prompt,
    extract_text_question,
    message_kind,
    native_tool_definitions,
    parse_bing_rss_results,
    parse_response,
    parse_response_ex,
    parse_tool_call,
    system_prompt,
)
from harness.clock import format_snapshot, snapshot
from harness.schedule import ScheduleStore, Scheduler
from harness.config import Config
from harness.console import Application
from harness.device import Board
from harness.executor import (
    ExecutionResult,
    Executor,
    _compile_memory_note,
    instrument_source,
    warm_viper_compiler,
)
from harness.http_client import HTTPBodyReader, split_url
from harness.llm import OpenAIClient
from harness.llm_anthropic import AnthropicClient
from harness.mcp import MCPStore
from harness.conversation import BackgroundStore, ConversationStore
from harness.webapp import WebApp
from harness.webui import APP_JS, INDEX_HTML


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return self.responses.pop(0)

    def complete_stream(self, messages, **kwargs):
        return self.complete(messages, **kwargs)


class FailingLLM:
    def complete(self, messages, **kwargs):
        raise OSError(110, "SSE idle timeout")

    complete_stream = complete


class SavedConversation:
    def __init__(self):
        self.saved = []
        self.original_request = ""

    def save(self, history, summary="", original_request=""):
        self.saved.append([dict(message) for message in history])
        self.original_request = original_request
        return True


def remove_tree(path):
    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        child = path + "/" + name
        try:
            os.remove(child)
        except OSError:
            remove_tree(child)
        try:
            os.rmdir(child)
        except OSError:
            pass
    try:
        os.rmdir(path)
    except OSError:
        pass


class NoWatchdog:
    def feed(self):
        pass


def run():
    original_print = builtins.print
    config = Config()
    assert config.get("stream_first_token_timeout") == 600
    assert config.get("stream_idle_timeout") >= 1
    assert config.get("stream_header_timeout") >= 1
    assert config.get("background_execution") is True
    board_info = Board().info()
    assert "cpu_freq" in board_info and "unique_id" in board_info, board_info
    prompt = system_prompt(config, Board(), workspace=True)
    assert "listdir/open/stat" in prompt
    assert "no api object" in prompt
    assert "import machine" in prompt
    assert "machine.SPI" in prompt
    assert "CS/DC/RST/BL" in prompt
    assert "api.workspace" not in prompt
    assert "listdir/open/stat" in builtin_tool_prompt(
        config, workspace=object())
    native = native_tool_definitions(
        config, workspace=object(), memory=object())
    names = [tool["name"] for tool in native]
    assert "__workspace" in names and "__ask" in names, names
    assert "__time" in names and "__schedule" in names, names
    workspace_tool = [
        tool for tool in native if tool["name"] == "__workspace"][0]
    assert "background" in workspace_tool["parameters"]["properties"]
    assert "match" in workspace_tool["parameters"]["properties"]
    actions = workspace_tool["parameters"]["properties"]["action"]["enum"]
    assert "append" in actions and "replace" in actions, actions
    assert MAX_QUESTION_OPTIONS == 8
    rss = (
        '<rss><channel><item><title>Quick &amp; Result</title>'
        '<link>https://example.com/quick</link>'
        '<description>First &lt;b&gt;snippet&lt;/b&gt;; keep a &lt; b &gt; c.'
        '</description><pubDate>2026-01-01</pubDate></item></channel></rss>'
    )
    rss_results = parse_bing_rss_results(rss, 5)
    assert rss_results and rss_results[0]["title"] == "Quick & Result", rss_results
    assert rss_results[0]["snippet"] == (
        "First snippet; keep a < b > c."), rss_results
    assert SEARCH_PROVIDERS == ("auto", "bing"), SEARCH_PROVIDERS
    assert "search_provider_effective" in APP_JS
    assert "questions" in APP_JS or "question" in APP_JS
    assert "background" in APP_JS and "rewind" in APP_JS
    assert "sidebar" in INDEX_HTML and "scheduleList" in INDEX_HTML
    assert "screenFiles" in INDEX_HTML and "screenSkills" in INDEX_HTML
    assert "/api/files" in APP_JS and "/api/skills" in APP_JS
    assert "/api/schedules" in APP_JS and "syncSchedules" in APP_JS
    assert "harness" in INDEX_HTML.lower()

    client = OpenAIClient(config)
    assert client.session_id.startswith("ses_")
    anthropic = AnthropicClient(config)
    assert anthropic._endpoint().endswith("/messages")
    assert split_url("https://example.com/v1")[2] == 443
    reader = HTTPBodyReader(
        None,
        {"_status_line": "HTTP/1.1 200 OK", "content-length": "0"},
        b"",
        max_response=10,
        idle_timeout=5,
        feed=lambda: None,
        trace=lambda message: None,
        first_byte_timeout=600,
    )
    assert reader.first_byte_timeout == 600

    agent = Agent(config, FakeLLM("quick-ok"), Board())
    explanation, has_code = agent.ask("quick smoke")
    assert explanation == "quick-ok", explanation
    assert not has_code
    assert len(agent.history) == 2
    assert agent.last_user_index == 0
    assert agent.pending_question is None
    clock_text = format_snapshot(snapshot(config))
    assert "local:" in clock_text and "utc:" in clock_text, clock_text
    print("clock:", clock_text.replace("\n", " | "))
    time_result = agent._execute_tool({"name": "__time", "args": {}})
    assert time_result.ok and "timezone:" in time_result.output, (
        time_result.observation())
    schedule_store = ScheduleStore("/", config)
    assert schedule_store.path.endswith("/schedules.json")
    scheduler = Scheduler(config, None)
    now = snapshot(config)["epoch"]
    scheduled = scheduler.create({
        "type": "prompt", "name": "quick schedule",
        "prompt": "quick scheduled task", "when": "once", "delay_s": 600,
    }, now)
    assert scheduled["next_run"] == now + 600, scheduled
    assert "once" in scheduler.view(scheduled["id"])["when_label"]
    scheduler.delete(scheduled["id"])
    assert not scheduler.snapshot()
    for count in (8, 9):
        result = agent._ask_user({
            "question": "pick",
            "options": ["option-%s" % index for index in range(count)],
        })
        assert result.ok == (count == 8), (count, result.error)
    assert parse_response("x\n```python\nprint(1)\n```")[1] == "print(1)"
    _, background_call = parse_tool_call(
        '```tool\n{"name":"__background","args":{"action":"stop"}}\n```')
    assert background_call["name"] == "__background"
    assert background_call["args"]["action"] == "stop"
    _, _, timeout = parse_response_ex(
        "```python\nprint(1)\n```\ntimeout=12\ntail")
    assert timeout == 12
    _, _, timeout = parse_response_ex(
        "```python timeout = 9\nprint(1)\n```")
    assert timeout == 9
    _, source, timeout = parse_response_ex(
        "```python\ntimeout = 45\nprint(1)\n```")
    assert timeout == 45 and source == "print(1)"
    _, _, timeout = parse_response_ex(
        "```python timeout=infinite\nprint(1)\n```")
    assert timeout == 0
    text_question, explanation = extract_text_question(
        "--- question for you: Check ---\nWhich one?\n"
        "  1. A\n  2. B\nReply with a number."
    )
    assert explanation == ""
    assert text_question["header"] == "Check"
    assert text_question["question"] == "Which one?"
    assert [item["label"] for item in text_question["options"]] == ["A", "B"]

    agent.history = [
        {"role": "user", "content": "rewind task", "kind": "user"},
        {"role": "assistant", "content": "done", "kind": "assistant"},
    ]
    index, removed = agent.rewind_last(1)
    assert index == 0 and removed == 2 and not agent.history
    assert message_kind({
        "role": "user", "content": "Local tool result for x",
    }) == "observation"
    background = BackgroundStore("/", config)
    assert background.path.endswith("/background.json")
    result = ExecutionResult(True, "quick-bg", "", 1)
    agent.record_execution_result(
        result, background=True, job_id=1, task="quick background task")
    assert "job=1" in agent.history[-1]["content"]
    assert "quick background task" in agent.history[-1]["content"]
    assert agent.has_background_result(1)
    agent.history = []

    # A stopped turn must keep its user message and a failure note.
    conversation = SavedConversation()
    retries = config.get("api_retries", 3)
    config.values["api_retries"] = 0
    try:
        failing = Agent(config, FailingLLM(), Board(),
                        conversation=conversation)
        try:
            failing.ask("quick failure")
        except Exception:
            pass
    finally:
        config.values["api_retries"] = retries
    assert conversation.saved, "failed turn was not persisted"
    joined = "\n".join(
        message["content"] for message in conversation.saved[-1])
    assert "quick failure" in joined, joined
    assert "did not finish" in joined, joined

    result = Executor(Board()).execute("print('quick-exec-ok')")
    assert result.ok and "quick-exec-ok" in result.output, result.output
    cancelled = Executor(
        Board(), cancel_check=lambda: True
    ).execute("while True:\n    pass")
    assert cancelled.cancelled and not cancelled.ok, cancelled.observation()
    nested = Executor(Board()).execute(
        "g = {'__name__': '__main__'}\n"
        "exec('__harness_check(); print(\"nested-ok\")', g)")
    assert nested.ok and "nested-ok" in nested.output, nested.output
    try:
        builtins.__harness_check
        raise AssertionError("builtins.__harness_check leaked after execution")
    except AttributeError:
        pass

    # The cooperative hook runs on every instrumented while condition. Measure
    # it against the same source executed without instrumentation: the exec
    # globals themselves already cost ~20 us/iteration on this board, so a
    # fixed absolute budget would blame the hook for the interpreter's
    # module-level global access. A full checkpoint (clock + cancel + feed)
    # costs ~30-40 us, so an un-amortised rewrite shows up as a large delta.
    loop_source = (
        "import gc, time\n"
        "n = 1000\n"
        "gc.collect()\n"
        "gc.disable()\n"
        "t0 = time.ticks_us()\n"
        "i = 0\n"
        "while i < n:\n"
        "    i += 1\n"
        "elapsed = time.ticks_diff(time.ticks_us(), t0)\n"
        "gc.enable()\n"
        "print('loop-us', elapsed / n)\n")
    plain = Executor(Board()).execute(loop_source)
    assert plain.ok, plain.observation()
    base_us = float(plain.output.split("loop-us")[1].strip())
    perf = Executor(Board(), timeout_s=30).execute(loop_source)
    assert perf.ok, perf.observation()
    check_us = float(perf.output.split("loop-us")[1].strip())
    assert check_us - base_us < 15, (
        "instrumented loop adds %.1f us/iter (plain %.1f, checked %.1f)"
        % (check_us - base_us, base_us, check_us))
    # while uses one __h_tick call per iteration; an extra explicit
    # __harness_check() in the body must add a second checkpoint call, not
    # make the automatic condition pay the full poll itself.
    explicit = Executor(Board(), timeout_s=30).execute(
        "import gc, time\n"
        "n = 1000\n"
        "gc.collect()\n"
        "gc.disable()\n"
        "t0 = time.ticks_us()\n"
        "i = 0\n"
        "while i < n:\n"
        "    __harness_check()\n"
        "    i += 1\n"
        "elapsed = time.ticks_diff(time.ticks_us(), t0)\n"
        "gc.enable()\n"
        "print('explicit-us', elapsed / n)\n")
    assert explicit.ok, explicit.observation()
    explicit_us = float(explicit.output.split("explicit-us")[1].strip())
    assert check_us < explicit_us, (
        "instrumented while %.1f us is not cheaper than an explicit check "
        "%.1f us (plain %.1f)" % (check_us, explicit_us, base_us))

    # /workspace modules are user code and may be edited between runs. A
    # cached sys.modules entry used to keep executing the old bytecode.
    cache_name = "harness_cache_smoke"
    cache_path = "/workspace/tmp/" + cache_name + ".py"
    # A freshly flashed board has no user workspace until the application
    # boots, so create the container directories this check writes into.
    for directory in ("/workspace", "/workspace/tmp"):
        try:
            os.mkdir(directory)
        except OSError:
            pass
    sys.path.append("/workspace/tmp")
    try:
        with open(cache_path, "w") as handle:
            handle.write("VERSION = 1\n")
        import_source = (
            "import %s\n"
            "print('cache-version', %s.VERSION)\n"
            % (cache_name, cache_name))
        first = Executor(Board(), timeout_s=10).execute(import_source)
        assert first.ok and "cache-version 1" in first.output, (
            first.observation())
        # A Workspace write drops the loaded module even when size and the
        # one-second mtime are unchanged. The reference user workspace may
        # already be at its total/file limit, so use a permissive config for
        # this tiny temporary write instead of failing the smoke or deleting
        # user files.
        from harness.workspace import Workspace

        class CacheWorkspaceConfig:
            def get(self, name, default=None):
                if name in ("workspace_total_limit", "workspace_files_limit"):
                    return 1048576
                return default

        Workspace("/", CacheWorkspaceConfig()).write(
            "tmp/" + cache_name + ".py", "VERSION = 2\n", overwrite=True)
        second = Executor(Board(), timeout_s=10).execute(import_source)
        assert second.ok and "cache-version 2" in second.output, (
            second.observation())
        # A direct write outside Workspace is detected by the mtime stamp on
        # the next execution.
        time.sleep(1.1)
        with open(cache_path, "w") as handle:
            handle.write("VERSION = 3\n")
        third = Executor(Board(), timeout_s=10).execute(import_source)
        assert third.ok and "cache-version 3" in third.output, (
            third.observation())
    finally:
        sys.modules.pop(cache_name, None)
        try:
            sys.path.remove("/workspace/tmp")
        except ValueError:
            pass
        try:
            os.remove(cache_path)
        except OSError:
            pass

    # Viper functions compile to native code and only accept int/ptr locals;
    # injecting __harness_range/__harness_check used to raise ViperTypeError.
    viper = Executor(Board(), timeout_s=10).execute(
        "import micropython\n"
        "@micropython.viper\n"
        "def total(n: int) -> int:\n"
        "    s: int = 0\n"
        "    for i in range(n):\n"
        "        s += i\n"
        "    return s\n"
        "print('viper-ok', total(10))\n")
    assert viper.ok, viper.observation()
    assert "viper-ok 45" in viper.output, viper.observation()
    # The bare form after "from micropython import viper" must be skipped too.
    # On this firmware viper/native are compiler-recognised rather than
    # importable names, so verify the rewriter output instead of executing it.
    bare_source = (
        "from micropython import viper\n"
        "@viper\n"
        "def total2(n: int) -> int:\n"
        "    s: int = 0\n"
        "    for i in range(n):\n"
        "        s += i\n"
        "    return s\n"
    )
    assert instrument_source(bare_source) == bare_source
    remove_tree("/smoke-ws")
    try:
        from harness.workspace import Workspace

        os.mkdir("/smoke-ws")
        first = Workspace("/smoke-ws")
        first.write("tmp/keep.py", "print('kept')\n")
        second = Workspace("/smoke-ws")
        assert second.read("tmp/keep.py")["text"] == "print('kept')\n"
    finally:
        remove_tree("/smoke-ws")

    note = _compile_memory_note(
        "from micropython import viper\n@viper\ndef f():\n    pass\n", 2)
    assert "compile stage" in note, note
    assert "compile_retries=2" in note, note
    assert "viper/native=yes" in note, note
    assert "not a low-memory condition" in note, note
    plain_note = _compile_memory_note("print('x')\n", 2)
    assert "internal_free=" in plain_note, plain_note
    assert "internal_largest_block=" in plain_note, plain_note
    assert "psram_free=" in plain_note, plain_note
    assert warm_viper_compiler() in (True, False)

    # Exercise real worker threads, concurrent foreground execution and the
    # multi-job result store against an isolated root so the user's /state
    # conversation is untouched.
    remove_tree("/smoke")
    try:
        os.mkdir("/smoke")
    except OSError:
        pass
    shell = None
    try:
        shell = Application(root="/smoke", supervisor=NoWatchdog())
        machine_result = shell.executor.execute(
            "import machine\nprint(machine.freq() > 0)")
        assert machine_result.ok and "True" in machine_result.output, (
            machine_result.observation())
        device_global = shell.executor.execute(
            "print(device.info()['platform'])")
        assert device_global.ok and "esp32" in device_global.output, (
            device_global.observation())
        device_import = shell.executor.execute(
            "import device\nprint(device.info()['platform'])")
        assert device_import.ok and "esp32" in device_import.output, (
            device_import.observation())
        big_source = "# pad\n" * 2200 + "print('workspace-big-ok')\n"
        assert len(big_source) > shell.config.get("code_limit", 12288)
        shell.workspace.write("big_script.py", big_source)
        old_auto = shell.config.values.get("auto_run")
        shell.config.values["auto_run"] = True
        try:
            big_result = shell.agent._run_workspace_tool({
                "action": "run", "path": "big_script.py"})
        finally:
            if old_auto is None:
                shell.config.values.pop("auto_run", None)
            else:
                shell.config.values["auto_run"] = old_auto
        assert big_result.ok and "workspace-big-ok" in big_result.output, (
            big_result.observation())
        shell.workspace.write("tmp/edit.py", "a = 1\nb = 2\n")
        appended = shell.workspace.append("tmp/edit.py", "c = 3\n")
        assert appended["chars"] == len("a = 1\nb = 2\nc = 3\n"), appended
        replaced = shell.workspace.replace(
            "tmp/edit.py", "b = 2", "b = 20")
        assert replaced["occurrences"] == 1, replaced
        assert shell.workspace.read("tmp/edit.py")["text"] == (
            "a = 1\nb = 20\nc = 3\n")
        edit_result = shell.agent._run_workspace_tool({
            "action": "replace", "path": "tmp/edit.py",
            "match": "c = 3", "content": "c = 30"})
        assert edit_result.ok and "Replaced 1" in edit_result.output, (
            edit_result.observation())
        assert shell.backgrounds.max_jobs >= 2
        shell.agent.history = [{
            "role": "user", "content": "quick background task",
            "kind": "user",
        }, {
            "role": "user", "content": "old result (job=1)",
            "kind": "observation",
        }]
        shell.agent.last_user_index = 0
        shell.agent.turns = 1

        def wait_jobs(jobs, guard_limit=300):
            guard = 0
            while guard < guard_limit:
                active = False
                for item in jobs:
                    if item["phase"] in ("queued", "running"):
                        active = True
                        break
                if not active:
                    return
                if hasattr(time, "sleep_ms"):
                    time.sleep_ms(20)
                else:
                    time.sleep(0.02)
                guard += 1
            raise AssertionError("background jobs did not finish: %s" % (
                [(job["id"], job["phase"], job.get("error")) for job in jobs],))

        shell.agent.pending_source = (
            "import time\n"
            "for i in range(6):\n"
            "    print('quick-bg-a', i)\n"
            "    time.sleep_ms(30)\n")
        shell.agent.pending_timeout = 5
        first = shell.start_background(
            auto=False, driver="serial", note="alpha")
        shell.agent.pending_source = (
            "import time\n"
            "for i in range(6):\n"
            "    print('quick-bg-b', i)\n"
            "    time.sleep_ms(30)\n")
        shell.agent.pending_timeout = 5
        second = shell.start_background(
            auto=False, driver="serial", note="beta")
        assert first["phase"] == "running", first.get("error")
        assert second["phase"] == "running", second.get("error")
        assert first["task"] == "quick background task"
        assert first["turns"] == 1
        assert shell.background_active()
        assert not shell.is_turn_busy()
        assert shell.begin_operation("web-chat")
        shell.end_operation()
        assert shell.begin_operation("serial")
        shell.end_operation()
        assert shell.begin_operation("web-config")
        shell.end_operation()
        assert not shell.begin_operation("web-ca")

        foreground = shell.executor.execute("print('quick-foreground')")
        assert foreground.ok and "quick-foreground" in foreground.output

        wait_jobs([first, second])
        assert first["phase"] == "ready", first.get("error")
        assert second["phase"] == "ready", second.get("error")
        assert "quick-bg-a" in first["result"].output
        assert "quick-bg-b" in second["result"].output
        assert "quick-foreground" not in first["result"].output
        assert "quick-foreground" not in second["result"].output
        assert "quick-bg-a" not in foreground.output
        assert "quick-bg-b" not in foreground.output
        lines = "\n".join(shell.background_summary_lines())
        assert ("#%s" % first["id"]) in lines and ("#%s" % second["id"]) in lines
        assert "alpha" in lines and "beta" in lines
        shell.set_background_note(second["id"], "quick note")
        assert shell.background_view(second["id"])["note"] == "quick note"

        taken = shell.take_background_result(first["id"])
        assert taken["id"] == first["id"]
        assert "quick background task" in shell.agent.history[-1]["content"]
        assert "earlier user task" in shell.agent.history[-1]["content"]
        assert ("job=%s" % first["token"]) in shell.agent.history[-1]["content"]
        assert "print()" in shell.agent.history[-1]["content"]
        assert len(shell.background_views()) == 1

        shell.agent.pending_source = "while True:\n    pass"
        shell.agent.pending_timeout = 0
        stoppable = shell.start_background(
            auto=False, driver="serial", note="stoppable")
        assert stoppable["timeout"] == 0
        assert stoppable["phase"] == "running", stoppable.get("error")
        assert shell.stop_background("quick smoke", job_id=stoppable["id"])
        assert stoppable["stop_requested"]
        wait_jobs([stoppable], guard_limit=300)
        assert stoppable["phase"] == "ready", stoppable.get("error")
        assert stoppable["result"].cancelled, stoppable["result"].observation()
        shell.take_background_result(stoppable["id"])
        assert "status=cancelled" in shell.agent.history[-1]["content"]
        assert "cooperative" in shell.agent.history[-1]["content"]

        shell.agent.pending_source = (
            "import time\n"
            "for i in range(200):\n"
            "    time.sleep_ms(10)\n")
        shell.agent.pending_timeout = 5
        discard_job = shell.start_background(
            auto=False, driver="serial", note="discard-me")
        assert discard_job["phase"] == "running", discard_job.get("error")
        assert shell.stop_background(
            "quick smoke discard", job_id=discard_job["id"], discard=True)
        assert discard_job["discard_on_finish"]
        guard = 0
        while (shell.background_view(discard_job["id"]).get("phase")
               != "none" and guard < 300):
            if hasattr(time, "sleep_ms"):
                time.sleep_ms(20)
            else:
                time.sleep(0.02)
            guard += 1
        assert shell.background_view(discard_job["id"])["phase"] == "none"
        assert discard_job["id"] not in [
            view["id"] for view in shell.background_views()]

        # With one slot, the queued task must start from the finishing
        # worker's _launch_next path (a thread creating another thread).
        shell.backgrounds.max_jobs = 1
        shell.agent.pending_source = (
            "import time\n"
            "time.sleep_ms(400)\n"
            "print('queue-first')\n")
        shell.agent.pending_timeout = 5
        queue_first = shell.start_background(
            auto=False, driver="serial", note="queue-first")
        shell.agent.pending_source = "print('queue-second')\n"
        shell.agent.pending_timeout = 5
        queue_second = shell.start_background(
            auto=False, driver="serial", note="queue-second")
        assert queue_first["phase"] == "running", queue_first.get("error")
        assert queue_second["phase"] == "queued", queue_second.get("error")
        wait_jobs([queue_first, queue_second])
        assert queue_first["phase"] == "ready", queue_first.get("error")
        assert queue_second["phase"] == "ready", queue_second.get("error")
        assert "queue-first" in queue_first["result"].output
        assert "queue-second" in queue_second["result"].output
        shell.backgrounds.max_jobs = 2

        # No annotation must mean the configured default, never infinite.
        shell.agent.pending_source = "print('default-timeout-bg')\n"
        shell.agent.pending_timeout = None
        default_job = shell.start_background(
            auto=False, driver="serial", note="default-timeout")
        assert default_job["timeout"] == shell.config.get("code_timeout_s", 20)
        wait_jobs([default_job])
        assert default_job["phase"] == "ready", default_job.get("error")
        assert "default-timeout-bg" in default_job["result"].output

        # Nested exec in a worker must capture print through builtins routing.
        shell.agent.pending_source = (
            "g = {'__name__': '__main__'}\n"
            "exec(\"print('nested-bg-ok')\", g)\n")
        shell.agent.pending_timeout = 5
        nested_job = shell.start_background(
            auto=False, driver="serial", note="nested-output")
        wait_jobs([nested_job])
        assert nested_job["phase"] == "ready", nested_job.get("error")
        assert "nested-bg-ok" in nested_job["result"].output

        # AI may start background source only when AUTO is enabled.
        shell.config.values["auto_run"] = False
        blocked = shell.agent._run_background_tool({
            "action": "start", "source": "print('must-not-run')\n"})
        assert not blocked.ok and "AUTO" in blocked.error, blocked
        shell.config.values["auto_run"] = True
        ai_started = shell.agent._run_background_tool({
            "action": "start", "source": "print('ai-start-ok')\n",
            "note": "ai-start", "timeout": 5})
        assert ai_started.ok, ai_started.error
        assert "Started background job" in ai_started.output
        ai_job = shell.background_views()[-1]
        ai_job = shell.background_get(ai_job["id"])
        wait_jobs([ai_job])
        assert ai_job["phase"] == "ready", ai_job.get("error")
        assert "ai-start-ok" in ai_job["result"].output
        assert ai_job["note"] == "ai-start"
        assert ai_job["driver"] == "agent"

        # AUTO mode must continue a ready result on the device itself, even
        # without a browser polling. Patch only the model call so the smoke
        # does not use network credentials.
        shell.backgrounds.discard(all_finished=True)
        shell.config.values["auto_run"] = True
        shell.config.values["auto_feedback"] = True
        shell.agent.turns = 0
        shell.agent.pending_source = None
        shell.agent.pending_skill = None
        shell.agent.pending_question = None
        original_prepare_cloud = shell.prepare_cloud
        original_continue_feedback = shell.agent.continue_feedback
        shell.prepare_cloud = lambda: None
        shell.agent.continue_feedback = lambda: ("auto-continued", False)
        try:
            shell.agent.pending_source = "print('auto-continue-source')\n"
            shell.agent.pending_timeout = 5
            auto_job = shell.start_background(
                auto=True, driver="serial", note="auto-device")
            wait_jobs([auto_job])
            assert auto_job["phase"] == "ready", auto_job.get("error")
            # Exercise the real serial idle path, not only the helper.
            shell._at_prompt = True
            shell.idle()
            assert not shell.background_views()
            assert shell.auto_continue_seq >= 1
            assert shell.live.content == "auto-continued", shell.live.view()
            assert shell.live.active is False
            assert shell.live.id >= 1
            assert not any(event.get("t") == "execution"
                           for event in shell.live.events), shell.live.view()
            joined = "\n".join(
                message.get("content", "") for message in shell.agent.history)
            assert "auto-continue-source" in joined
        finally:
            shell.prepare_cloud = original_prepare_cloud
            shell.agent.continue_feedback = original_continue_feedback

        # Four concurrent workers (the configured maximum) plus the main
        # thread must all start and keep their output separate.
        shell.backgrounds.discard(all_finished=True)
        shell.backgrounds.max_jobs = 4
        four = []
        for index in range(4):
            shell.agent.pending_source = (
                "import time\n"
                "for step in range(3):\n"
                "    print('four-%s', step)\n"
                "    time.sleep_ms(10)\n" % index)
            shell.agent.pending_timeout = 5
            four.append(shell.start_background(
                auto=False, driver="serial", note="four-%s" % index))
        assert all(job["phase"] in ("running", "ready") for job in four), [
            (job.get("id"), job.get("phase"), job.get("error"))
            for job in four]
        wait_jobs(four, guard_limit=400)
        for index, job in enumerate(four):
            assert job["phase"] == "ready", job.get("error")
            assert ("four-%s" % index) in job["result"].output
            for other in range(4):
                if other != index:
                    assert ("four-%s" % other) not in job["result"].output

        # Worker threads must not leak the global time/print/check hooks.
        assert not hasattr(builtins, "__harness_check")
        assert builtins.print is original_print
        restored_time = sys.modules.get("time")
        assert restored_time is None or restored_time is time
    finally:
        if shell is not None:
            shell.agent.clear()
            try:
                shell.backgrounds.clear()
            except Exception:
                pass
            del shell
        gc.collect()
        remove_tree("/smoke")

    assert MCPStore is not None and ConversationStore is not None
    assert BackgroundStore is not None
    assert WebApp is not None
    print("DEVICE_QUICK_SMOKE_OK heap_free=%s" % gc.mem_free())


run()
