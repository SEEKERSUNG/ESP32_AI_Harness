import json
import os
import tempfile
import unittest
from unittest import mock

import _thread

from harness.agent import auto_source_is_safe
from harness.config import Config
from harness.console import LiveTurn, UIRouter, command_is_exclusive
from harness.llm import LLMStreamCancelled
from harness.mcp import MCPManager, MCPStore
from harness.schedule import ScheduleStore, Scheduler
from harness.skills import SkillStore
from harness.web import WebError, WebRequest
from harness.webapp import (
    CommandCaptureSink,
    WebApp,
    WebCommandConfirm,
    WebTurnSink,
)
from harness.webauth import WebAuth, parse_cookie
from harness.workspace import Workspace


class FakeSerial:
    def __init__(self):
        self.written = []

    def write(self, text):
        self.written.append(str(text))

    def line(self, text=""):
        self.written.append(str(text) + "\n")

    def readline(self, prompt="", secret=False):
        raise AssertionError("web test must not read serial input")

    def ask_yes_no(self, prompt, default=False):
        raise AssertionError("web test must not ask serial questions")


class FakeNetwork:
    def __init__(self):
        self.current = {
            "connected": True, "ip": "192.168.1.50", "time_synced": True,
            "mode": "apsta", "sta_connected": True, "sta_ip": "192.168.1.50",
            "sta_ssid": "home", "ap_active": True, "ap_ip": "192.168.4.1",
            "ap_ssid": "ESP32-Harness-CCDD", "ap_off_in_s": 0,
            "connecting": False, "last_error": "",
        }

    def status(self):
        return dict(self.current)


class FakeHttp:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class FakeLLM:
    def __init__(self):
        self.last_streamed = False
        self.last_finish_reason = "stop"
        self.last_elapsed_ms = 12
        self.last_timeout_ms = 120
        self.last_reasoning_chars = 0
        self.last_content_chars = 2
        self.last_response_chars = 2

    def complete_stream(self, messages, on_reasoning=None, on_content=None):
        self.last_streamed = True
        return "OK"

    def complete(self, messages):
        self.last_streamed = False
        return "OK"


class FakeResult:
    def __init__(self, ok=True, output="1\n", error="", elapsed_ms=7, live=False):
        self.ok = ok
        self.output = output
        self.error = error
        self.elapsed_ms = elapsed_ms
        self.live = live


class FakeAgent:
    def __init__(self, app):
        self.app = app
        self.history = []
        self.summary = ""
        self.pending_source = None
        self.pending_skill = None
        self.pending_timeout = None
        self.pending_question = None
        self.turns = 0
        self.last_streamed = False
        self.recovered_execution = False
        self.recovered_pending = False
        self.last_result = None
        self.last_user_index = None
        self.ask_text = "hello"
        self.ask_code = None
        self.ask_question = None
        self.ask_stream = False
        self.ask_error = None
        self.feedback_code = None
        self.feedback_text = "finished"
        self.feedback_error = None
        self.can_continue_value = True
        self.execute_error = None
        self.recover = False
        self.executed = 0
        self.feedback_calls = 0
        self.ask_calls = []
        self.execute_result = FakeResult()
        self.skill_metadata = {"name": "demo-skill", "chars": 12}

    def has_pending(self):
        return any((self.pending_source, self.pending_skill))

    def pending_kind(self):
        if self.pending_source is not None:
            return "code"
        if self.pending_skill is not None:
            return "skill"
        if self.pending_question is not None:
            return "question"
        return "none"

    def context_usage(self):
        return {
            "messages": len(self.history),
            "chars": sum(len(item.get("content", "")) for item in self.history),
            "estimated_tokens": 12,
            "percent": 3,
            "summary_chars": len(self.summary),
        }

    def ask(self, text):
        self.ask_calls.append(text)
        if self.ask_error is not None:
            raise self.ask_error
        self.history.append({"role": "user", "content": text,
                             "kind": "user"})
        self.last_user_index = len(self.history) - 1
        self.recovered_execution = False
        self.last_streamed = self.ask_stream
        sink = self.app.ui.current()
        sink.model_start()
        if self.ask_stream:
            sink.content(self.ask_text)
        sink.model_result(None, self.app.llm)
        if self.ask_code is not None:
            self.pending_source = self.ask_code
        self.pending_question = self.ask_question
        self.turns = 1
        return self.ask_text, self.ask_code is not None

    def execute_pending(self, executor):
        if self.execute_error is not None:
            raise self.execute_error
        self.executed += 1
        self.pending_source = None
        self.pending_timeout = None
        self.recovered_execution = self.recover
        self.last_result = self.execute_result
        return self.execute_result

    def continue_feedback(self):
        self.feedback_calls += 1
        if self.feedback_error is not None:
            raise self.feedback_error
        if self.feedback_code is not None:
            self.pending_source = self.feedback_code
            return "fixed", True
        return self.feedback_text, False

    def can_continue(self):
        return self.can_continue_value

    def discard(self):
        had = self.has_pending()
        self.pending_source = None
        self.pending_skill = None
        self.pending_question = None
        self.recovered_pending = False
        return had

    def begin_execution(self):
        if self.pending_source is None:
            raise RuntimeError("No pending code")
        source = self.pending_source
        timeout = self.pending_timeout
        self.pending_source = None
        self.pending_timeout = None
        recovered = self.recovered_pending
        self.recovered_pending = False
        self.recovered_execution = recovered
        return source, timeout, recovered

    def restore_pending_source(self, source, timeout=None, recovered=False):
        self.pending_source = source
        self.pending_timeout = timeout
        self.recovered_pending = recovered
        self.recovered_execution = False

    def record_execution_result(self, result, recovered=False,
                                background=False, job_id=0, task=""):
        self.last_result = result
        self.recovered_execution = recovered
        text = "background" if background else "execution"
        if task:
            text += " task=" + task
        self.history.append({"role": "user", "content": text,
                             "kind": "observation"})
        return text

    def has_background_result(self, job_id):
        return False

    def record_local_observation(self, text):
        self.history.append({"role": "user", "content": text,
                             "kind": "observation"})
        return text

    def find_user_index(self, count=1):
        seen = 0
        for index in range(len(self.history) - 1, -1, -1):
            message = self.history[index]
            if message.get("role") == "user" and message.get("kind") != "observation":
                seen += 1
                if seen == count:
                    return index
        raise RuntimeError("No matching user message to rewind")

    def rewind_to(self, index):
        removed = len(self.history) - index
        self.history = self.history[:index]
        self.pending_source = None
        self.pending_skill = None
        self.pending_question = None
        return removed

    def commit_pending_skill(self):
        if self.pending_skill is None:
            raise RuntimeError("No pending skill proposal")
        self.pending_skill = None
        return dict(self.skill_metadata)

    def _retry_api_call(self, label, call):
        return call()


class FakeApp:
    def __init__(self, root):
        self.config = Config(root)
        self.config.values.update({
            "base_url": "https://api.example/v1",
            "model": "test-model",
            "input_limit": 100,
            "max_turns": 4,
            "stream": True,
            "auto_run": False,
            "auto_feedback": True,
            "background_execution": False,
        })
        self.config.secrets.update({
            "wifi_ssid": "home",
            "wifi_password": "wifi-secret",
            "api_key": "api-secret",
            "ap_password": "ap-secret-value",
        })
        self.network = FakeNetwork()
        self.http = FakeHttp()
        self.mcp_store = MCPStore(root, self.config)
        self.mcp = MCPManager(self.config, self.http, self.mcp_store)
        self.schedule_store = ScheduleStore(root, self.config)
        self.schedules = Scheduler(self.config, self.schedule_store)
        self.schedule_seq = 0
        self.schedule_now = 1795500000
        self.llm = FakeLLM()
        self.agent = FakeAgent(self)
        self.skills = SkillStore(root)
        self.workspace = Workspace(root, self.config)
        self.executor = object()
        self.ui = UIRouter(FakeSerial())
        self.turn_lock = _thread.allocate_lock()
        self.busy_source = ""
        self.stop_requested = False
        self.stop_turn_calls = 0
        self.background_jobs = []
        self.background_seq = 0
        self.background_started = []
        self.pending_wifi = None
        self.operations = []
        self.prepare_calls = 0
        self.prepare_network_calls = 0
        self.prepare_error = None
        self.main_calls = 0
        self.stop_calls = []
        self.start_ap_calls = 0
        self.start_ap_error = None
        self.commands = []
        self.notices = []
        self.factory_reset_calls = 0
        self.reboot_delays = []
        self.live = LiveTurn()

    def live_status(self):
        return self.live.summary()

    def live_view(self):
        return self.live.view()

    def reload_llm(self):
        self.http.close()
        return self.llm

    def begin_operation(self, source):
        if self.background_active() and source not in (
                "web-chat", "web-run", "web-continue", "web-background-stop",
                "web-background-discard", "web-discard", "web-save-skill",
                "schedule", "serial"):
            return False
        if self.turn_lock is not None and not self.turn_lock.acquire(False):
            return False
        self.busy_source = source
        self.operations.append(source)
        return True

    def end_operation(self):
        self.busy_source = ""
        if self.turn_lock is not None:
            self.turn_lock.release()

    def request_turn_stop(self):
        self.stop_turn_calls += 1
        source = self.busy_source or ""
        if not source or source.startswith("web-"):
            return False
        self.stop_requested = True
        return True

    def is_busy(self):
        if self.background_active():
            return True
        if self.busy_source:
            return True
        return self.turn_lock.locked()

    @property
    def background(self):
        for phases in (("queued", "running"), ("ready",), ("error",)):
            for job in self.background_jobs:
                if job.get("phase") in phases:
                    return job
        return None

    @background.setter
    def background(self, job):
        if job is None:
            self.background_jobs = []
        else:
            self.background_jobs = [job]

    def background_active(self):
        return any(job.get("phase") in ("queued", "running")
                   for job in self.background_jobs)

    def background_get(self, job_id=None):
        for job in self.background_jobs:
            if job_id is None or job.get("id") == int(job_id):
                return job
        return None

    def background_ready_jobs(self):
        return [job for job in self.background_jobs
                if job.get("phase") == "ready"]

    def background_error_jobs(self):
        return [job for job in self.background_jobs
                if job.get("phase") == "error"]

    def background_view(self, job_id=None):
        job = self.background_get(job_id)
        if job is None:
            return {
                "active": False, "phase": "none", "id": 0,
                "auto": False, "recovered": False, "driver": "",
                "source_chars": 0, "timeout_s": None,
                "task": "", "note": "", "turns": 0,
                "stop_requested": False, "stop_reason": "",
                "elapsed_ms": 0, "error": "", "result": None,
            }
        result = job.get("result")
        view = {
            "active": job.get("phase") in ("queued", "running"),
            "phase": job.get("phase", "queued"),
            "id": job.get("id", 0),
            "token": job.get("token", ""),
            "auto": bool(job.get("auto")),
            "recovered": bool(job.get("recovered")),
            "driver": job.get("driver", ""),
            "source_chars": int(job.get("source_chars", 0) or 0),
            "timeout_s": job.get("timeout"),
            "task": job.get("task", ""),
            "note": job.get("note", job.get("task", "")),
            "turns": job.get("turns", 0),
            "stop_requested": bool(job.get("stop_requested")),
            "stop_reason": job.get("stop_reason", ""),
            "elapsed_ms": 0,
            "error": job.get("error", ""),
            "result": None,
        }
        if result is not None:
            view["result"] = {
                "ok": bool(result.ok),
                "timed_out": bool(getattr(result, "timed_out", False)),
                "cancelled": bool(getattr(result, "cancelled", False)),
                "output": result.output,
                "error": result.error,
                "elapsed_ms": result.elapsed_ms,
            }
        return view

    def background_views(self, result_limit=4096):
        return [self.background_view(job.get("id"))
                for job in self.background_jobs]

    def background_summary_lines(self, include_output=False):
        if not self.background_jobs:
            return ["No background jobs."]
        lines = ["Background jobs: %s." % len(self.background_jobs)]
        for job in self.background_jobs:
            lines.append("#%s %s note=%s" % (
                job.get("id"), job.get("phase"),
                job.get("note") or job.get("task") or "<none>"))
        return lines

    def stop_background(self, reason="", job_id=None, discard=False):
        self.stop_calls.append((reason, job_id, discard))
        count = 0
        for job in self.background_jobs:
            if job.get("phase") not in ("queued", "running"):
                continue
            if job_id is not None and job.get("id") != int(job_id):
                continue
            job["stop_requested"] = True
            job["stop_reason"] = reason
            if discard:
                job["discard_on_finish"] = True
            count += 1
        return count

    def set_background_note(self, job_id, text):
        job = self.background_get(job_id)
        if job is None:
            raise RuntimeError("No background job")
        job["note"] = text
        return job

    def start_background(self, auto=False, driver="web", note=""):
        source, timeout, recovered = self.agent.begin_execution()
        self.background_seq += 1
        task = ""
        try:
            index = self.agent.last_user_index
            if index is not None and 0 <= index < len(self.agent.history):
                task = self.agent.history[index].get("content", "")
        except Exception:
            pass
        job = {
            "id": self.background_seq,
            "token": "%s@0" % self.background_seq,
            "phase": "queued",
            "source": source,
            "source_chars": len(source),
            "timeout": timeout,
            "auto": auto,
            "recovered": recovered,
            "driver": driver,
            "task": task,
            "note": note or task,
            "turns": self.agent.turns,
            "started_ms": 0,
            "finished_ms": 0,
            "stop_requested": False,
            "stop_reason": "",
            "result": None,
            "error": "",
        }
        self.background_jobs.append(job)
        self.background_started.append(job)
        return job

    def finish_background(self, result=None, job_id=None):
        job = self.background_get(job_id)
        if job is None:
            raise RuntimeError("No background execution")
        job["result"] = result or self.agent.execute_result
        job["phase"] = "ready"
        job["finished_ms"] = 1
        job["source"] = None
        return job

    def take_background_result(self, job_id=None):
        job = self.background_get(job_id)
        if job is None or job.get("phase") != "ready":
            raise RuntimeError("No completed background execution")
        result = job.get("result")
        if result is not None:
            self.agent.record_execution_result(
                result, recovered=job.get("recovered", False),
                background=True, job_id=job.get("token") or job["id"],
                task=job.get("task", ""))
        if job.get("turns") is not None:
            self.agent.turns = job["turns"]
        self.background_jobs.remove(job)
        return job

    def discard_background(self, record=False, job_id=None):
        job = self.background_get(job_id)
        if job is None:
            return False
        if job.get("phase") in ("queued", "running"):
            raise RuntimeError("Background execution is still running")
        if job_id is None and len(self.background_jobs) > 1:
            raise RuntimeError("Multiple background jobs")
        self.background_jobs.remove(job)
        if record and job.get("result") is not None:
            self.agent.record_local_observation(
                "Local harness event: discarded background result")
        return True

    def discard_all_backgrounds(self, record=False):
        finished = [job for job in self.background_jobs
                    if job.get("phase") in ("ready", "error")]
        self.background_jobs = [job for job in self.background_jobs
                                if job.get("phase") in ("queued", "running")]
        if record and finished:
            self.agent.record_local_observation(
                "Local harness event: discarded background results")
        return len(finished)

    def prepare_for_new_turn(self):
        last = None
        for job in list(self.background_ready_jobs()):
            last = self.take_background_result(job.get("id"))
        for job in list(self.background_error_jobs()):
            self.background_jobs.remove(job)
            self.agent.record_local_observation(
                "Local harness event: background failed")
            last = job
        return last

    def refresh_background_limits(self):
        pass

    def schedule_views(self):
        return self.schedules.views()

    def schedule_full_views(self):
        return [
            self.schedules.view(task.get("id"), full=True)
            for task in self.schedules.snapshot()
        ]

    def schedule_view(self, task_id, full=False):
        return self.schedules.view(task_id, full=full)

    def schedule_text(self, task_id, full=False):
        return self.schedules.format_task(task_id, full=full)

    def schedule_summary_lines(self):
        return self.schedules.summary_lines()

    def create_schedule(self, fields):
        return self.schedules.create(fields, self.schedule_now)

    def update_schedule(self, task_id, fields):
        return self.schedules.update(task_id, fields, self.schedule_now)

    def delete_schedule(self, task_id):
        return self.schedules.delete(task_id)

    def set_schedule_enabled(self, task_id, enabled):
        return self.schedules.set_enabled(task_id, enabled)

    def request_schedule_run(self, task_id):
        return self.schedules.request_run(task_id, self.schedule_now)

    def rebase_schedules(self):
        return self.schedules.rebase_timezone(self.schedule_now)

    def is_turn_busy(self):
        if self.busy_source:
            return True
        return self.turn_lock.locked()

    def is_exclusive_command(self, line):
        return command_is_exclusive(line)

    def prepare_network(self):
        self.prepare_network_calls += 1
        if self.prepare_error is not None:
            raise self.prepare_error

    def prepare_cloud(self):
        self.prepare_calls += 1
        if self.prepare_error is not None:
            raise self.prepare_error

    def run_on_main(self, func):
        self.main_calls += 1
        return func()

    def factory_reset_data(self):
        self.factory_reset_calls += 1
        return ["/config.json", "/skills"]

    def request_reboot(self, delay_ms=1000):
        self.reboot_delays.append(delay_ms)

    def tick(self):
        pass

    def start_setup_ap(self):
        self.start_ap_calls += 1
        if self.start_ap_error is not None:
            raise self.start_ap_error
        self.network.current["ap_active"] = True
        return self.network.status()

    def command(self, line):
        self.commands.append(line)
        self.ui.line("output for " + line)

    def queue_notice(self, text):
        self.notices.append(text)


class FakeWriter:
    def __init__(self):
        self.status = None
        self.body = None
        self.json = None
        self.extra = None
        self.content_type = None
        self.stream = False
        self.events = []
        self.headers_sent = False
        self.closed = False
        self.fail_stream = False
        self.raise_stream = False
        self.fail_after = None

    def send_response(self, status, body, content_type="text/plain", extra=None):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.extra = extra

    def send_json(self, status, value, extra=None):
        self.status = status
        self.json = value
        self.extra = extra

    def start_stream(self):
        if self.raise_stream:
            raise RuntimeError("stream failed")
        if self.fail_stream:
            return False
        self.status = 200
        self.stream = True
        self.headers_sent = True
        return True

    def event(self, value):
        if self.closed or (self.fail_after is not None
                           and len(self.events) >= self.fail_after):
            self.closed = True
            return False
        self.events.append(value)
        return True

    def client_closed(self):
        return bool(self.closed)

    def close(self):
        self.closed = True


def request(method, path, value=None, token=None, query=None,
            host="192.168.4.1:8080", peer="192.168.4.2", headers=None):
    body = b""
    all_headers = {"host": host}
    if method == "POST":
        all_headers.update({
            "x-harness": "1",
            "content-type": "application/json",
            "origin": "http://" + host,
        })
        body = json.dumps(value if value is not None else {}).encode()
    if token:
        all_headers["cookie"] = "harness_session=" + token
    if headers:
        all_headers.update(headers)
    return WebRequest(method, path, query or {}, all_headers, body, peer)


def cookie_token(writer):
    for name, value in (writer.extra or []):
        if name.lower() == "set-cookie":
            return parse_cookie(value)
    return None


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = FakeApp(self.temp.name)
        self.auth = WebAuth(
            self.app.config,
            sleep_fn=lambda _ms: None,
        )
        self.web = WebApp(self.app, self.auth)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, request_value):
        writer = FakeWriter()
        self.web.handle(request_value, writer)
        return writer

    def error_call(self, request_value):
        with self.assertRaises(WebError) as raised:
            self.call(request_value)
        return raised.exception

    def token(self):
        return self.auth.set_password("admin-password")

    def test_setup_login_logout_and_secret_audit(self):
        self.assertFalse(self.auth.configured())
        writer = self.call(request("GET", "/api/setup"))
        self.assertTrue(writer.json["setup_required"])
        self.assertTrue(writer.json["ap_active"])

        writer = self.call(request("POST", "/api/setup", {
            "password": "admin-password",
            "confirm": "admin-password",
            "wifi_ssid": "new-home",
            "wifi_password": "new-wifi-secret",
            "base_url": "https://api.example/v1/",
            "model": "new-model",
            "api_key": "new-api-secret",
        }))
        self.assertEqual(200, writer.status)
        self.assertTrue(writer.json["ok"])
        token = cookie_token(writer)
        self.assertTrue(token)
        self.assertTrue(self.auth.configured())
        self.assertEqual("new-home", self.app.config.secret("wifi_ssid"))
        self.assertEqual("new-wifi-secret", self.app.config.secret("wifi_password"))
        self.assertEqual("new-api-secret", self.app.config.secret("api_key"))
        self.assertEqual("new-model", self.app.config.get("model"))
        self.assertEqual("https://api.example/v1", self.app.config.get("base_url"))
        self.assertEqual(("new-home", "new-wifi-secret"), self.app.pending_wifi)
        self.assertFalse(self.app.is_busy())

        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual(200, status.status)
        status_text = json.dumps(status.json)
        for secret in ("new-wifi-secret", "new-api-secret", "admin-password",
                       "ap-secret-value", "wifi-secret", "api-secret",
                       self.app.config.secret("web_password_hash"),
                       self.app.config.secret("web_password_salt")):
            self.assertNotIn(secret, status_text)

        config = self.call(request("GET", "/api/config", token=token))
        config_text = json.dumps(config.json)
        for secret in ("new-wifi-secret", "new-api-secret", "admin-password",
                       "ap-secret-value", "wifi-secret", "api-secret"):
            self.assertNotIn(secret, config_text)
        self.assertTrue(config.json["api_key_set"])
        self.assertIn("search_provider_effective", config.json)
        self.assertIn("search_provider_fallback", config.json)
        self.assertTrue(config.json["wifi_password_set"])
        self.assertTrue(config.json["ap_password_set"])
        self.assertTrue(config.json["web_password_set"])
        self.assertEqual("new-home", config.json["wifi_ssid"])

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/setup", {
                "password": "admin-password", "confirm": "admin-password",
            }))
        self.assertEqual(409, raised.exception.status)

        login = self.call(request("POST", "/api/login",
                                  {"password": "admin-password"}))
        self.assertEqual(200, login.status)
        login_token = cookie_token(login)
        self.assertTrue(login_token)
        self.assertTrue(self.auth.valid(login_token))
        self.assertEqual(200, self.call(
            request("GET", "/api/status", token=login_token)
        ).status)

        logout = self.call(request("POST", "/api/logout", {}, token=login_token))
        self.assertEqual(200, logout.status)
        self.assertFalse(self.auth.valid(login_token))
        self.assertTrue(self.auth.valid(token))

    def test_setup_save_failure_allows_clean_retry(self):
        old_values = dict(self.app.config.values)
        old_secrets = dict(self.app.config.secrets)
        body = {
            "password": "admin-password", "confirm": "admin-password",
            "wifi_ssid": "new-home", "wifi_password": "new-secret",
            "model": "new-model",
        }
        with mock.patch.object(self.app.config, "save_all",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.call(request("POST", "/api/setup", body))
        self.assertEqual(old_values, self.app.config.values)
        self.assertEqual(old_secrets, self.app.config.secrets)
        self.assertFalse(self.auth.configured())
        self.assertIsNone(self.app.pending_wifi)
        self.assertFalse(self.app.is_busy())

        writer = self.call(request("POST", "/api/setup", body))
        self.assertEqual(200, writer.status)
        self.assertTrue(self.auth.configured())

    def test_auth_host_origin_and_content_type_checks(self):
        token = self.token()
        with self.assertRaises(WebError) as raised:
            self.call(request("GET", "/api/status"))
        self.assertEqual(401, raised.exception.status)

        bad_origin = request(
            "POST", "/api/chat", {"text": "hello"}, token=token,
            headers={"origin": "http://evil.example"},
        )
        with self.assertRaises(WebError) as raised:
            self.call(bad_origin)
        self.assertEqual(403, raised.exception.status)

        missing_header = request(
            "POST", "/api/chat", {"text": "hello"}, token=token,
            headers={"x-harness": ""},
        )
        with self.assertRaises(WebError) as raised:
            self.call(missing_header)
        self.assertEqual(403, raised.exception.status)

        wrong_type = request(
            "POST", "/api/chat", {"text": "hello"}, token=token,
            headers={"content-type": "text/plain"},
        )
        with self.assertRaises(WebError) as raised:
            self.call(wrong_type)
        self.assertEqual(415, raised.exception.status)

        with self.assertRaises(WebError) as raised:
            self.call(request("GET", "/api/status", token=token,
                              host="evil.example:8080"))
        self.assertEqual(421, raised.exception.status)

        self.auth.clear_password()
        self.app.network.current["ap_active"] = True
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/setup", {
                "password": "admin-password", "confirm": "admin-password",
            }, peer="192.168.1.99"))
        self.assertEqual(403, raised.exception.status)

    def test_status_history_pending_and_config_redaction(self):
        token = self.token()
        self.app.agent.history = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
            {"role": "assistant", "content": "four"},
        ]
        self.app.agent.pending_source = "print(1)"
        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual("code", status.json["pending"]["kind"])
        self.assertTrue(status.json["pending"]["safe"])
        self.assertEqual(4, status.json["history"]["messages"])
        self.assertFalse(status.json["auto"]["run"])
        self.assertEqual(0, status.json["auto"]["continue_seq"])
        self.assertEqual(1, status.json["sessions"])

        self.app.agent.pending_source = "while True:\n    pass"
        status = self.call(request("GET", "/api/status", token=token))
        self.assertFalse(status.json["pending"]["safe"])

        history = self.call(request(
            "GET", "/api/history", token=token, query={"limit": "2"}
        ))
        self.assertEqual(["three", "four"],
                         [item["content"] for item in history.json["messages"]])
        self.assertTrue(history.json["truncated"])
        self.assertEqual(4, history.json["total_messages"])
        self.assertEqual(2, history.json["shown_messages"])

        self.app.agent.pending_source = None
        self.app.agent.pending_question = {
            "header": "Choice",
            "question": "Which one?",
            "options": [{"label": "A", "description": ""}],
            "allow_custom": True,
        }
        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual("Which one?", status.json["question"]["question"])
        # `pending` covers code/skill slots only; questions use `question`.
        self.assertEqual("none", status.json["pending"]["kind"])
        self.app.agent.pending_question = None
        config = self.call(request("GET", "/api/config", token=token))
        self.assertEqual("home", config.json["wifi_ssid"])
        self.assertTrue(config.json["api_key_set"])
        self.assertTrue(config.json["web_password_set"])
        self.assertEqual("apsta", config.json["mode"])
        self.assertEqual("192.168.1.50", config.json["sta_ip"])
        self.assertNotIn("wifi_password", config.json)
        self.assertNotIn("api_key", config.json)

    def test_history_budget_keeps_newest_messages(self):
        token = self.token()
        self.app.agent.history = [
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "mid2"},
            {"role": "user", "content": "new3"},
        ]
        self.app.config.values["web_history_chars"] = 5
        history = self.call(request(
            "GET", "/api/history", token=token, query={"limit": "3"}
        ))
        self.assertEqual(200, history.status)
        self.assertEqual(["new3"], [item["content"] for item in history.json["messages"]])
        self.assertTrue(history.json["truncated"])
        self.assertEqual(3, history.json["total_messages"])
        self.assertEqual(1, history.json["shown_messages"])

    def test_history_keeps_recent_reasoning_in_tool_heavy_turns(self):
        token = self.token()
        history = [{"role": "user", "content": "task", "kind": "user"}]
        for index in range(20):
            history.append({
                "role": "user", "kind": "observation",
                "content": "Local tool result for 'x':\n" + ("o" * 9000),
            })
            history.append({
                "role": "assistant", "kind": "assistant",
                "content": "round %s" % index,
                "reasoning": "thinking %s" % index,
            })
        self.app.agent.history = history
        self.app.config.values["web_history_chars"] = 65536
        view = self.call(request("GET", "/api/history", token=token))
        messages = view.json["messages"]
        assistant = [item for item in messages if item["role"] == "assistant"]
        self.assertTrue(assistant)
        self.assertTrue(any(item["reasoning"] for item in assistant),
                        "recent reasoning must survive tool-heavy history")
        self.assertIn("thinking 19", [item["reasoning"] for item in assistant])
        for item in messages:
            if item["kind"] == "observation":
                self.assertLessEqual(len(item["content"]), 2048)
                self.assertTrue(item["truncated"])

    def test_history_exposes_message_kind_and_absolute_index(self):
        token = self.token()
        self.app.agent.history = [
            {"role": "user", "content": "one", "kind": "user"},
            {"role": "assistant", "content": "two", "kind": "assistant",
             "reasoning": "thinking about two"},
            {"role": "user", "content": "Local tool result for x",
             "kind": "observation"},
        ]
        history = self.call(request("GET", "/api/history", token=token))
        self.assertEqual(0, history.json["start_index"])
        self.assertEqual([0, 1, 2],
                         [item["index"] for item in history.json["messages"]])
        self.assertEqual(["user", "assistant", "observation"],
                         [item["kind"] for item in history.json["messages"]])
        self.assertEqual("thinking about two",
                         history.json["messages"][1]["reasoning"])

    def test_status_exposes_summary_chars_and_skill_context(self):
        token = self.token()
        self.app.agent.summary = "compressed context"
        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual(len("compressed context"),
                         status.json["history"]["summary_chars"])

    def test_skills_api_lists_and_reads_bundles(self):
        token = self.token()
        self.app.skills.save("demo", "Demo skill", "Use this body.")
        listing = self.call(request("GET", "/api/skills", token=token))
        self.assertEqual(200, listing.status)
        self.assertEqual(["demo"],
                         [item["name"] for item in listing.json["skills"]])
        self.assertEqual("Demo skill",
                         listing.json["skills"][0]["description"])
        detail = self.call(request(
            "GET", "/api/skills", token=token, query={"name": "demo"}))
        self.assertEqual(200, detail.status)
        self.assertIn("Use this body.", detail.json["content"])
        self.assertEqual("Demo skill", detail.json["description"])
        with self.assertRaises(WebError) as missing:
            self.call(request(
                "GET", "/api/skills", token=token, query={"name": "missing"}))
        self.assertEqual(404, missing.exception.status)

    def test_files_api_lists_reads_and_writes_workspace(self):
        token = self.token()
        workspace = os.path.join(self.temp.name, "workspace")
        with open(os.path.join(workspace, "note.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("hello file")
        listing = self.call(request(
            "GET", "/api/files", token=token, query={"path": "/workspace"}))
        self.assertEqual(200, listing.status)
        self.assertTrue(listing.json["writable"])
        entries = {entry["name"]: entry for entry in listing.json["entries"]}
        self.assertTrue(entries["note.txt"]["writable"])
        self.assertTrue(entries["note.txt"]["editable"])
        self.assertFalse(entries["note.txt"]["protected"])

        read = self.call(request(
            "GET", "/api/files/read", token=token,
            query={"path": "/workspace/note.txt"}))
        self.assertEqual("hello file", read.json["text"])
        self.assertTrue(read.json["editable"])
        self.assertFalse(read.json["truncated"])

        written = self.call(request("POST", "/api/files/write", {
            "path": "/workspace/note.txt", "content": "updated",
        }, token=token))
        self.assertEqual(200, written.status)
        with open(os.path.join(workspace, "note.txt"),
                  encoding="utf-8") as handle:
            self.assertEqual("updated", handle.read())

        uploaded = self.call(request("POST", "/api/files/upload", {
            "path": "/workspace/new.txt", "content": "uploaded text",
        }, token=token))
        self.assertEqual(200, uploaded.status)
        duplicate = self.error_call(request("POST", "/api/files/upload", {
            "path": "/workspace/new.txt", "content": "again",
        }, token=token))
        self.assertEqual(400, duplicate.status)
        overwritten = self.call(request("POST", "/api/files/upload", {
            "path": "/workspace/new.txt", "content": "again",
            "overwrite": True,
        }, token=token))
        self.assertEqual(200, overwritten.status)

    def test_files_api_protects_system_and_sensitive_files(self):
        token = self.token()
        with open(os.path.join(self.temp.name, "secrets.json"), "w",
                  encoding="utf-8") as handle:
            handle.write('{"api_key":"secret"}')
        with open(os.path.join(self.temp.name, "boot.py"), "w",
                  encoding="utf-8") as handle:
            handle.write("print('boot')")
        root = self.call(request("GET", "/api/files", token=token,
                                 query={"path": "/"}))
        entries = {entry["name"]: entry for entry in root.json["entries"]}
        self.assertTrue(entries["boot.py"]["protected"])
        self.assertFalse(entries["boot.py"]["writable"])
        self.assertTrue(entries["secrets.json"]["sensitive"])
        self.assertFalse(root.json["writable"])

        for path in ("/workspace/../secrets.json", "/a/../../secrets.json"):
            rejected = self.error_call(request(
                "GET", "/api/files/read", token=token,
                query={"path": path}))
            self.assertEqual(400, rejected.status)

        secret_read = self.error_call(request(
            "GET", "/api/files/read", token=token,
            query={"path": "/secrets.json"}))
        self.assertEqual(403, secret_read.status)
        # FAT lookups are case-insensitive; mixed case must not bypass the
        # secret-file block.
        mixed_case = self.error_call(request(
            "GET", "/api/files/read", token=token,
            query={"path": "/SeCrEtS.json"}))
        self.assertEqual(403, mixed_case.status)
        secret_download = self.error_call(request(
            "GET", "/api/files/download", token=token,
            query={"path": "/secrets.json"}))
        self.assertEqual(403, secret_download.status)

        protected_write = self.error_call(request("POST", "/api/files/write", {
            "path": "/boot.py", "content": "print('changed')",
        }, token=token))
        self.assertEqual(403, protected_write.status)
        protected_upload = self.error_call(request("POST", "/api/files/upload", {
            "path": "/boot.py", "content": "print('changed')",
        }, token=token))
        self.assertEqual(403, protected_upload.status)

    def test_files_api_download_mkdir_rename_and_delete(self):
        token = self.token()
        workspace = os.path.join(self.temp.name, "workspace")
        with open(os.path.join(workspace, "download.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("download body")
        downloaded = self.call(request(
            "GET", "/api/files/download", token=token,
            query={"path": "/workspace/download.txt"}))
        self.assertEqual(200, downloaded.status)
        self.assertEqual(b"download body", downloaded.body)
        self.assertEqual("application/octet-stream", downloaded.content_type)
        self.assertTrue(any(name == "Content-Disposition"
                            for name, _ in (downloaded.extra or [])))

        created = self.call(request("POST", "/api/files/mkdir", {
            "path": "/workspace/folder",
        }, token=token))
        self.assertEqual(200, created.status)
        renamed = self.call(request("POST", "/api/files/rename", {
            "path": "/workspace/folder", "name": "renamed",
        }, token=token))
        self.assertEqual(200, renamed.status)
        self.assertTrue(os.path.isdir(os.path.join(workspace, "renamed")))
        with open(os.path.join(workspace, "renamed", "x.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("x")
        nonempty = self.error_call(request("POST", "/api/files/delete", {
            "path": "/workspace/renamed",
        }, token=token))
        self.assertEqual(409, nonempty.status)
        child_delete = self.call(request("POST", "/api/files/delete", {
            "path": "/workspace/renamed/x.txt",
        }, token=token))
        self.assertEqual(200, child_delete.status)
        empty_delete = self.call(request("POST", "/api/files/delete", {
            "path": "/workspace/renamed",
        }, token=token))
        self.assertEqual(200, empty_delete.status)

    def test_files_body_limit_allows_workspace_sized_upload(self):
        token = self.token()
        self.assertGreater(self.web.body_limit(), 32768)
        content = "x" * int(self.app.config.get("workspace_file_limit", 32768))
        writer = self.call(request("POST", "/api/files/upload", {
            "path": "/workspace/big.txt", "content": content,
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual(len(content), writer.json["chars"])

    def test_rewind_endpoint_by_count_and_index(self):
        token = self.token()
        self.app.agent.history = [
            {"role": "user", "content": "first", "kind": "user"},
            {"role": "assistant", "content": "one", "kind": "assistant"},
            {"role": "user", "content": "second", "kind": "user"},
            {"role": "assistant", "content": "two", "kind": "assistant",
             "reasoning": "thinking about two"},
        ]
        writer = self.call(request("POST", "/api/rewind",
                                   {"count": 1}, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual(2, writer.json["removed"])
        self.assertEqual(2, writer.json["remaining"])
        self.assertEqual(["first", "one"],
                         [item["content"] for item in self.app.agent.history])

        self.app.agent.history = [
            {"role": "user", "content": "only", "kind": "user"},
            {"role": "assistant", "content": "answer", "kind": "assistant"},
        ]
        writer = self.call(request("POST", "/api/rewind",
                                   {"index": 0}, token=token))
        self.assertEqual(2, writer.json["removed"])
        self.assertEqual([], self.app.agent.history)

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/rewind",
                              {"count": 0}, token=token))
        self.assertEqual(400, raised.exception.status)

    def test_live_status_and_stream_endpoint(self):
        token = self.token()
        self.app.live.begin()
        self.app.live.append_content("hello ")
        self.app.live.append_reasoning("thinking")
        self.app.live.add_event({"t": "tool", "phase": "start", "name": "x"})
        status = self.call(request("GET", "/api/status", token=token))
        self.assertTrue(status.json["live"]["active"])
        self.assertEqual(6, status.json["live"]["chars"])
        self.assertEqual(8, status.json["live"]["reasoning_chars"])
        live = self.call(request("GET", "/api/live", token=token))
        self.assertEqual(200, live.status)
        self.assertEqual(1, live.json["id"])
        self.assertEqual("hello ", live.json["content"])
        self.assertEqual("thinking", live.json["reasoning"])
        self.assertEqual("x", live.json["events"][0]["name"])
        self.assertEqual(0, live.json["event_base"])
        self.assertTrue(live.json["active"])
        self.app.live.finish()
        live = self.call(request("GET", "/api/live", token=token))
        self.assertFalse(live.json["active"])

    def test_background_chat_queues_and_continue_resumes(self):
        token = self.token()
        self.app.config.values["background_execution"] = True
        self.app.config.values["auto_run"] = True
        self.app.config.values["auto_feedback"] = True
        self.app.agent.ask_code = "print(1)"
        writer = self.call(request("POST", "/api/chat",
                                   {"text": "run it"}, token=token))
        kinds = [event["t"] for event in writer.events]
        self.assertIn("background", kinds)
        self.assertNotIn("execution", kinds)
        code = [event for event in writer.events if event["t"] == "code"][0]
        self.assertTrue(code["auto"])
        self.assertTrue(code["background"])
        self.assertEqual("queued", self.app.background["phase"])
        self.assertEqual(0, self.app.agent.executed)
        self.assertEqual(0, self.app.agent.feedback_calls)
        self.assertIsNone(self.app.agent.pending_source)

        self.app.finish_background()
        writer = self.call(request("POST", "/api/continue", {}, token=token))
        kinds = [event["t"] for event in writer.events]
        self.assertIn("execution", kinds)
        self.assertIn("feedback", kinds)
        self.assertEqual("done", kinds[-1])
        self.assertEqual(1, self.app.agent.feedback_calls)
        self.assertEqual(0, self.app.agent.executed)
        self.assertIsNone(self.app.background)
        self.assertEqual("observation", self.app.agent.history[-1]["kind"])
        self.assertIn("background", self.app.agent.history[-1]["content"])

    def test_background_continue_commits_recovered_result_without_feedback(self):
        token = self.token()
        self.app.background = {
            "id": 3, "phase": "ready", "source": None, "source_chars": 10,
            "timeout": 20, "auto": False, "recovered": True,
            "driver": "web", "started_ms": 0, "finished_ms": 0,
            "result": FakeResult(), "error": "",
        }
        writer = self.call(request("POST", "/api/continue", {}, token=token))
        self.assertIn("execution", [event["t"] for event in writer.events])
        self.assertIn("notice", [event["t"] for event in writer.events])
        self.assertEqual(0, self.app.agent.feedback_calls)
        self.assertIsNone(self.app.background)
        self.assertIn("background", self.app.agent.history[-1]["content"])

    def test_manual_background_run_and_discard(self):
        token = self.token()
        self.app.config.values["background_execution"] = True
        self.app.agent.pending_source = "print(1)"
        writer = self.call(request("POST", "/api/run", {}, token=token))
        self.assertIn("background", [event["t"] for event in writer.events])
        self.assertNotIn("execution", [event["t"] for event in writer.events])
        self.assertEqual(0, self.app.agent.executed)
        self.assertEqual("queued", self.app.background["phase"])
        first = self.app.background["id"]

        # A second run is a separate concurrent job, not a conflict.
        self.app.agent.pending_source = "print(2)"
        writer = self.call(request("POST", "/api/run", {}, token=token))
        self.assertEqual(2, len(self.app.background_jobs))
        self.assertEqual(2, self.app.background_jobs[-1]["id"])

        self.app.background_jobs[0]["phase"] = "running"
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/background/discard",
                              {"id": first}, token=token))
        self.assertEqual(409, raised.exception.status)

        self.app.background_jobs[0]["phase"] = "ready"
        self.app.background_jobs[0]["result"] = FakeResult()
        writer = self.call(request("POST", "/api/background/discard",
                                   {"id": first}, token=token))
        self.assertTrue(writer.json["discarded"])
        self.assertEqual(1, len(self.app.background_jobs))
        self.assertIn("discarded background result",
                      self.app.agent.history[-1]["content"])

    def test_foreground_run_and_background_run_use_separate_modes(self):
        token = self.token()
        self.app.config.values["background_execution"] = True
        self.app.background = {
            "id": 40, "phase": "running", "source": "print(1)",
            "source_chars": 8, "timeout": 60, "auto": False,
            "recovered": False, "driver": "web", "task": "draw",
            "turns": 1, "started_ms": 0, "finished_ms": 0,
            "result": None, "error": "",
        }
        self.app.agent.pending_source = "print(2)"
        writer = self.call(request("POST", "/api/run",
                                   {"mode": "foreground"}, token=token))
        kinds = [event["t"] for event in writer.events]
        self.assertIn("execution", kinds)
        self.assertNotIn("background", kinds)
        self.assertEqual(1, self.app.agent.executed)
        self.assertEqual("running", self.app.background["phase"])
        self.assertIsNone(self.app.agent.pending_source)

        self.app.agent.pending_source = "print(3)"
        writer = self.call(request("POST", "/api/run",
                                   {"mode": "background"}, token=token))
        self.assertIn("background", [event["t"] for event in writer.events])
        self.assertEqual(2, len(self.app.background_jobs))

    def test_stop_background_endpoint(self):
        token = self.token()
        writer = self.call(request("POST", "/api/background/stop", {},
                                   token=token))
        self.assertEqual(200, writer.status)
        self.assertFalse(writer.json["stop_requested"])

        self.app.background = {
            "id": 25, "phase": "running", "source": "while True: pass",
            "source_chars": 16, "timeout": 0, "auto": True,
            "recovered": False, "driver": "web", "task": "run forever",
            "turns": 1, "started_ms": 0, "finished_ms": 0,
            "result": None, "error": "", "stop_requested": False,
            "stop_reason": "",
        }
        writer = self.call(request("POST", "/api/background/stop", {},
                                   token=token))
        self.assertEqual(200, writer.status)
        self.assertTrue(writer.json["stop_requested"])
        self.assertTrue(self.app.background["stop_requested"])
        self.assertIn("cooperative", writer.json["message"])

        status = self.call(request("GET", "/api/status", token=token))
        self.assertTrue(status.json["background"]["stop_requested"])
        self.assertEqual(1, len(status.json["backgrounds"]))

    def test_stop_one_of_multiple_jobs_uses_id(self):
        token = self.token()
        self.app.background_jobs = [{
            "id": 60, "phase": "running", "source": "print(1)",
            "source_chars": 8, "timeout": 0, "auto": True,
            "recovered": False, "driver": "web", "task": "one",
            "turns": 1, "started_ms": 0, "finished_ms": 0,
            "result": None, "error": "", "stop_requested": False,
            "stop_reason": "",
        }, {
            "id": 61, "phase": "running", "source": "print(2)",
            "source_chars": 8, "timeout": 0, "auto": True,
            "recovered": False, "driver": "web", "task": "two",
            "turns": 1, "started_ms": 0, "finished_ms": 0,
            "result": None, "error": "", "stop_requested": False,
            "stop_reason": "",
        }]
        writer = self.call(request("POST", "/api/background/stop",
                                   {"id": 61}, token=token))
        self.assertTrue(writer.json["stop_requested"])
        self.assertEqual(1, writer.json["count"])
        self.assertFalse(self.app.background_jobs[0]["stop_requested"])
        self.assertTrue(self.app.background_jobs[1]["stop_requested"])

        writer = self.call(request("POST", "/api/background/stop",
                                   {"id": 61, "discard": True}, token=token))
        self.assertTrue(writer.json["stop_requested"])
        self.assertTrue(writer.json["discard"])
        self.assertIn("discard", writer.json["message"])
        self.assertTrue(self.app.background_jobs[1]["discard_on_finish"])
        self.assertEqual(
            ("requested from web", 61, True), self.app.stop_calls[-1])

        writer = self.call(request("POST", "/api/background/stop", {},
                                   token=token))
        self.assertEqual(2, writer.json["count"])
        self.assertTrue(self.app.background_jobs[0]["stop_requested"])

    def test_chat_is_allowed_while_background_runs(self):
        token = self.token()
        self.app.background = {
            "id": 21, "phase": "running", "source": "print(1)",
            "source_chars": 8, "timeout": 60, "auto": True,
            "recovered": False, "driver": "web", "task": "draw dashboard",
            "turns": 1, "started_ms": 0, "finished_ms": 0,
            "result": None, "error": "",
        }
        self.app.agent.ask_code = None
        writer = self.call(request("POST", "/api/chat",
                                   {"text": "another question"}, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual(["another question"], self.app.agent.ask_calls)
        self.assertEqual("running", self.app.background["phase"])
        self.assertEqual(0, self.app.agent.executed)
        self.assertNotIn("execution", [event["t"] for event in writer.events])

    def test_chat_code_can_start_another_background_job_while_one_runs(self):
        token = self.token()
        self.app.config.values["auto_run"] = True
        self.app.config.values["background_execution"] = True
        self.app.background = {
            "id": 22, "phase": "running", "source": "print(1)",
            "source_chars": 8, "timeout": 60, "auto": True,
            "recovered": False, "driver": "web", "task": "draw dashboard",
            "turns": 1, "started_ms": 0, "finished_ms": 0,
            "result": None, "error": "",
        }
        self.app.agent.ask_code = "print(2)"
        writer = self.call(request("POST", "/api/chat",
                                   {"text": "run this too"}, token=token))
        code = [event for event in writer.events if event["t"] == "code"][0]
        self.assertTrue(code["auto"])
        self.assertTrue(code["runnable"])
        self.assertTrue(code["background"])
        self.assertIn("background",
                      [event["t"] for event in writer.events])
        self.assertIsNone(self.app.agent.pending_source)
        self.assertEqual(1, len(self.app.background_started))
        self.assertEqual(2, len(self.app.background_jobs))
        self.assertEqual("running", self.app.background_jobs[0]["phase"])

    def test_continue_waits_for_pending_code_or_question(self):
        token = self.token()
        self.app.background = {
            "id": 24, "phase": "ready", "source": None, "source_chars": 8,
            "timeout": 60, "auto": True, "recovered": False,
            "driver": "web", "task": "draw dashboard", "turns": 1,
            "started_ms": 0, "finished_ms": 0,
            "result": FakeResult(), "error": "",
        }
        self.app.agent.pending_source = "print(1)"
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/continue", {}, token=token))
        self.assertEqual(409, raised.exception.status)
        self.assertEqual("pending", raised.exception.code)
        self.assertEqual("ready", self.app.background["phase"])
        self.assertEqual(0, self.app.agent.feedback_calls)

        self.app.agent.pending_source = None
        self.app.agent.pending_question = {
            "header": "Q", "question": "?", "options": [], "allow_custom": True,
        }
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/continue", {}, token=token))
        self.assertEqual(409, raised.exception.status)
        self.assertEqual("pending", raised.exception.code)
        self.assertEqual("ready", self.app.background["phase"])

    def test_stop_turn_targets_main_thread_turns_only(self):
        token = self.token()
        self.app.busy_source = "auto-continue"
        writer = self.call(request("POST", "/api/stop", {}, token=token))
        self.assertEqual(200, writer.status)
        self.assertTrue(writer.json["stop_requested"])
        self.assertEqual("auto-continue", writer.json["busy_source"])
        self.assertTrue(self.app.stop_requested)
        self.assertEqual(1, self.app.stop_turn_calls)

        # A web-owned turn is stopped by closing its own stream.
        self.app.stop_requested = False
        self.app.busy_source = "web-chat"
        writer = self.call(request("POST", "/api/stop", {}, token=token))
        self.assertEqual(200, writer.status)
        self.assertFalse(writer.json["stop_requested"])
        self.assertFalse(self.app.stop_requested)

        # Idle device: nothing to stop.
        self.app.busy_source = ""
        writer = self.call(request("POST", "/api/stop", {}, token=token))
        self.assertEqual(200, writer.status)
        self.assertFalse(writer.json["stop_requested"])
        self.assertEqual(3, self.app.stop_turn_calls)

    def test_continue_restores_background_turn_budget(self):
        token = self.token()
        self.app.config.values["max_turns"] = 4
        self.app.agent.turns = 9
        self.app.background = {
            "id": 23, "phase": "ready", "source": None, "source_chars": 8,
            "timeout": 60, "auto": True, "recovered": False,
            "driver": "web", "task": "draw dashboard", "turns": 1,
            "started_ms": 0, "finished_ms": 0,
            "result": FakeResult(), "error": "",
        }
        writer = self.call(request("POST", "/api/continue", {}, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual(1, self.app.agent.turns)
        self.assertEqual(1, self.app.agent.feedback_calls)

    def test_status_exposes_background_job(self):
        token = self.token()
        self.app.background = {
            "id": 11, "phase": "running", "source": "print(1)",
            "source_chars": 8, "timeout": 30, "auto": True,
            "recovered": False, "driver": "web", "started_ms": 0,
            "finished_ms": 0, "result": None, "error": "",
        }
        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual(11, status.json["background"]["id"])
        self.assertEqual("running", status.json["background"]["phase"])
        self.assertTrue(status.json["background"]["active"])
        self.assertTrue(status.json["busy"])

    def test_status_tolerates_inconsistent_pending_kind(self):
        token = self.token()
        self.app.agent.pending_source = None
        self.app.agent.pending_kind = lambda: "code"
        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual(200, status.status)
        self.assertEqual("none", status.json["pending"]["kind"])

    def test_config_updates_clock_settings(self):
        token = self.token()
        writer = self.call(request("POST", "/api/config", {
            "ntp_server": "time.example.test",
            "timezone_offset_minutes": 330,
            "ntp_sync_interval_s": 7200,
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual("time.example.test",
                         self.app.config.get("ntp_server"))
        self.assertEqual(330,
                         self.app.config.get("timezone_offset_minutes"))
        self.assertEqual(7200,
                         self.app.config.get("ntp_sync_interval_s"))
        config = self.call(request("GET", "/api/config", token=token))
        self.assertEqual(330, config.json["timezone_offset_minutes"])
        self.assertEqual(7200, config.json["ntp_sync_interval_s"])
        self.assertEqual("time.example.test", config.json["ntp_server"])

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "timezone_offset_minutes": 900,
            }, token=token))
        self.assertEqual(400, raised.exception.status)
        self.assertEqual(330,
                         self.app.config.get("timezone_offset_minutes"))

    def test_config_updates_max_turns_bounds(self):
        token = self.token()
        writer = self.call(request("POST", "/api/config", {
            "max_turns": 12,
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual(12, self.app.config.get("max_turns"))
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "max_turns": 0,
            }, token=token))
        self.assertEqual(400, raised.exception.status)
        self.assertEqual(12, self.app.config.get("max_turns"))

    def test_config_updates_max_tool_calls_bounds(self):
        token = self.token()
        writer = self.call(request("POST", "/api/config", {
            "max_tool_calls": 32,
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual(32, self.app.config.get("max_tool_calls"))
        config = self.call(request("GET", "/api/config", token=token))
        self.assertEqual(32, config.json["max_tool_calls"])
        for value in (0, 65):
            with self.assertRaises(WebError) as raised:
                self.call(request("POST", "/api/config", {
                    "max_tool_calls": value,
                }, token=token))
            self.assertEqual(400, raised.exception.status)
        self.assertEqual(32, self.app.config.get("max_tool_calls"))

    def test_config_updates_context_and_auto_compact(self):
        token = self.token()
        writer = self.call(request("POST", "/api/config", {
            "context_tokens": 64000,
            "auto_compact_tokens": 50000,
            "auto_compact": False,
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual(64000, self.app.config.get("context_tokens"))
        self.assertEqual(50000, self.app.config.get("auto_compact_tokens"))
        self.assertIs(False, self.app.config.get("auto_compact"))
        config = self.call(request("GET", "/api/config", token=token))
        self.assertEqual(64000, config.json["context_tokens"])
        self.assertEqual(50000, config.json["auto_compact_tokens"])
        self.assertIs(False, config.json["auto_compact"])
        for body in (
            {"auto_compact_tokens": 64000},
            {"context_tokens": 500},
            {"auto_compact_tokens": 100},
        ):
            with self.assertRaises(WebError) as raised:
                self.call(request("POST", "/api/config", body, token=token))
            self.assertEqual(400, raised.exception.status)
        self.assertEqual(64000, self.app.config.get("context_tokens"))
        self.assertEqual(50000, self.app.config.get("auto_compact_tokens"))

    def test_setup_reports_last_reset_note(self):
        self.app.reset_info = {"code": 3, "name": "WDT_RESET"}
        self.app.reset_note = lambda: "设备上次因看门狗复位"
        writer = self.call(request("GET", "/api/setup"))
        self.assertEqual(200, writer.status)
        self.assertEqual("WDT_RESET", writer.json["last_reset"]["name"])
        self.assertIn("看门狗", writer.json["reset_note"])

    def test_setup_anthropic_provider_uses_anthropic_default_base_url(self):
        writer = self.call(request("POST", "/api/setup", {
            "password": "admin-password",
            "confirm": "admin-password",
            "api_provider": "anthropic",
            "model": "claude-opus-5-5",
            "api_key": "anthropic-secret",
        }))
        self.assertEqual(200, writer.status)
        self.assertEqual("anthropic", self.app.config.get("api_provider"))
        self.assertEqual("https://api.anthropic.com",
                         self.app.config.get("base_url"))
        token = cookie_token(writer)
        config = self.call(request("GET", "/api/config", token=token))
        self.assertEqual("anthropic", config.json["api_provider"])
        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual("anthropic", status.json["model"]["provider"])
        self.assertNotIn("anthropic-secret", json.dumps(config.json))

    def test_mcp_api_redacts_headers_and_requires_auto_confirm(self):
        token = self.token()
        body = {"servers": [{
            "name": "demo",
            "url": "https://mcp.example/mcp",
            "headers": {"Authorization": "Bearer mcp-secret"},
            "enabled": True,
            "auto": False,
        }]}
        writer = self.call(request("POST", "/api/mcp", body, token=token))
        self.assertEqual(200, writer.status)
        self.assertNotIn("mcp-secret", json.dumps(writer.json))
        self.assertTrue(writer.json["servers"][0]["headers_set"])
        saved = self.app.mcp_store.load()[0]
        self.assertEqual("Bearer mcp-secret", saved["headers"]["Authorization"])

        writer = self.call(request("GET", "/api/mcp", token=token))
        self.assertEqual(200, writer.status)
        self.assertNotIn("mcp-secret", json.dumps(writer.json))

        auto_body = {"servers": [{
            "name": "demo",
            "url": "https://mcp.example/mcp",
            "auto": True,
        }]}
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/mcp", auto_body, token=token))
        self.assertEqual(400, raised.exception.status)

        auto_body["confirm"] = True
        writer = self.call(request("POST", "/api/mcp", auto_body, token=token))
        self.assertEqual(200, writer.status)
        saved = self.app.mcp_store.load()[0]
        self.assertTrue(saved["auto"])
        self.assertEqual("Bearer mcp-secret", saved["headers"]["Authorization"])

    def test_config_updates_rollback_and_password_rotation(self):
        old_token = self.token()
        writer = self.call(request("POST", "/api/config", {
            "base_url": "https://new.example/v1",
            "model": "new-model",
            "thinking_level": "max",
            "show_thinking": True,
            "tls_verify": False,
            "temperature": 0.1,
            "max_tokens": 1024,
            "search_enabled": False,
            "search_provider": "bing",
            "http_tool_enabled": False,
            "http_tool_allow_http": True,
        }, token=old_token))
        self.assertEqual(200, writer.status)
        self.assertEqual("https://new.example/v1", self.app.config.get("base_url"))
        self.assertEqual("new-model", self.app.config.get("model"))
        self.assertEqual("max", self.app.config.get("thinking_level"))
        self.assertTrue(self.app.config.get("show_thinking"))
        self.assertFalse(self.app.config.get("tls_verify"))
        self.assertFalse(self.app.config.get("search_enabled"))
        self.assertEqual("bing", self.app.config.get("search_provider"))
        self.assertFalse(self.app.config.get("http_tool_enabled"))
        self.assertTrue(self.app.config.get("http_tool_allow_http"))
        self.assertEqual(1, self.app.http.closed)

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "search_provider": "invalid",
            }, token=old_token))
        self.assertEqual(400, raised.exception.status)

        for provider in ("auto", "bing"):
            writer = self.call(request("POST", "/api/config", {
                "search_provider": provider,
            }, token=old_token))
            self.assertEqual(200, writer.status)
            self.assertEqual(provider, self.app.config.get("search_provider"))

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "base_url": "https://should-rollback.example",
                "max_tokens": "not-a-number",
            }, token=old_token))
        self.assertEqual(400, raised.exception.status)
        self.assertEqual("https://new.example/v1",
                         self.app.config.get("base_url"))

        writer = self.call(request("POST", "/api/config", {
            "api_key_clear": True,
        }, token=old_token))
        self.assertEqual(200, writer.status)
        self.assertEqual("", self.app.config.secret("api_key"))

        writer = self.call(request("POST", "/api/config", {
            "api_key": "replacement-secret",
        }, token=old_token))
        self.assertEqual(200, writer.status)
        self.assertEqual("replacement-secret", self.app.config.secret("api_key"))

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "ap_password": "short",
            }, token=old_token))
        self.assertEqual(400, raised.exception.status)
        self.assertEqual("ap-secret-value", self.app.config.secret("ap_password"))

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "web_password": "another-password",
                "current_password": "wrong",
                "confirm_password": "another-password",
            }, token=old_token))
        self.assertEqual(401, raised.exception.status)

        writer = self.call(request("POST", "/api/config", {
            "web_password": "another-password",
            "current_password": "admin-password",
            "confirm_password": "another-password",
        }, token=old_token))
        self.assertEqual(200, writer.status)
        new_token = cookie_token(writer)
        self.assertTrue(new_token)
        self.assertTrue(self.auth.verify("another-password"))
        self.assertFalse(self.auth.valid(old_token))
        self.assertTrue(self.auth.valid(new_token))

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "wifi_ssid": "no",
            }, token=new_token))
        self.assertEqual(400, raised.exception.status)

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "web_port": "99999",
            }, token=new_token))
        self.assertEqual(400, raised.exception.status)

        writer = self.call(request("POST", "/api/config", {
            "web_port": 8081,
        }, token=new_token))
        self.assertTrue(writer.json["restart_required"])

    def test_config_save_failure_restores_ram_and_password_session(self):
        token = self.token()
        old_values = dict(self.app.config.values)
        old_secrets = dict(self.app.config.secrets)
        body = {
            "base_url": "https://new.example/v1",
            "web_password": "another-password",
            "current_password": "admin-password",
            "confirm_password": "another-password",
        }
        with mock.patch.object(self.app.config, "save_all",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.call(request("POST", "/api/config", body, token=token))
        self.assertEqual(old_values, self.app.config.values)
        self.assertEqual(old_secrets, self.app.config.secrets)
        self.assertTrue(self.auth.verify("admin-password"))
        self.assertFalse(self.auth.verify("another-password"))
        self.assertTrue(self.auth.valid(token))
        self.assertFalse(self.app.is_busy())

    def test_config_response_failure_does_not_roll_back_saved_values(self):
        token = self.token()
        writer = FakeWriter()
        writer.send_json = mock.Mock(side_effect=OSError("client disconnected"))
        req = request("POST", "/api/config", {
            "base_url": "https://saved.example/v1",
        }, token=token)
        with self.assertRaises(OSError):
            self.web.handle(req, writer)
        self.assertEqual("https://saved.example/v1",
                         self.app.config.get("base_url"))
        loaded = Config(self.temp.name)
        self.assertEqual("https://saved.example/v1", loaded.get("base_url"))
        self.assertFalse(self.app.is_busy())

    def test_json_booleans_are_strict_and_thinking_aliases_are_canonical(self):
        token = self.token()
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/auto", {
                "auto_run": "false", "confirm": True,
            }, token=token))
        self.assertEqual(400, raised.exception.status)
        self.assertFalse(self.app.config.get("auto_run"))

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config", {
                "tls_verify": "false",
            }, token=token))
        self.assertEqual(400, raised.exception.status)
        self.assertTrue(self.app.config.get("tls_verify"))

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/wifi/connect", {
                "ssid": "home", "clear_password": "false",
            }, token=token))
        self.assertEqual(400, raised.exception.status)
        self.assertIsNone(self.app.pending_wifi)

        writer = self.call(request("POST", "/api/config", {
            "thinking_level": "middle",
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual("high", self.app.config.get("thinking_level"))
        writer = self.call(request("POST", "/api/config", {
            "thinking_level": "max",
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual("max", self.app.config.get("thinking_level"))

    def test_config_body_limit(self):
        token = self.token()
        self.app.config.values["web_body_limit"] = 64
        body = ("x" * 200).encode()
        req = WebRequest(
            "POST", "/api/config", {},
            {"host": "192.168.4.1:8080", "x-harness": "1",
             "content-type": "application/json",
             "cookie": "harness_session=" + token},
            body, "192.168.4.2",
        )
        with self.assertRaises(WebError) as raised:
            self.call(req)
        self.assertEqual(413, raised.exception.status)

    def test_ca_upload_validation(self):
        token = self.token()
        self.app.config.values["ca_file"] = "/custom-ca.pem"
        pem = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"
        with mock.patch("harness.webapp.write_text") as write:
            writer = self.call(request("POST", "/api/config/ca",
                                       {"pem": pem}, token=token))
        self.assertEqual(200, writer.status)
        write.assert_called_once_with("/custom-ca.pem", pem)
        self.assertGreaterEqual(self.app.http.closed, 1)

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config/ca",
                              {"pem": "not a certificate"}, token=token))
        self.assertEqual(400, raised.exception.status)

        self.app.config.values["ca_file"] = "../secrets.json"
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/config/ca",
                              {"pem": pem}, token=token))
        self.assertEqual(400, raised.exception.status)

    def test_auto_mode_requires_confirmation(self):
        token = self.token()
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/auto", {
                "auto_run": True, "auto_feedback": True,
            }, token=token))
        self.assertEqual(400, raised.exception.status)
        self.assertFalse(self.app.config.get("auto_run"))

        writer = self.call(request("POST", "/api/auto", {
            "auto_run": True, "auto_feedback": False, "confirm": True,
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertTrue(self.app.config.get("auto_run"))
        self.assertFalse(self.app.config.get("auto_feedback"))
        self.assertIn("warning", writer.json)

        writer = self.call(request("POST", "/api/auto", {
            "auto_run": False, "auto_feedback": True,
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertFalse(self.app.config.get("auto_run"))

    def test_factory_reset_requires_confirmation_and_password(self):
        token = self.token()
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/factory-reset",
                              {"password": "admin-password"}, token=token))
        self.assertEqual(400, raised.exception.status)

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/factory-reset",
                              {"confirm": True, "password": "wrong"},
                              token=token))
        self.assertEqual(401, raised.exception.status)
        self.assertEqual(0, self.app.factory_reset_calls)

    def test_factory_reset_runs_on_main_thread_and_requests_reboot(self):
        token = self.token()
        writer = self.call(request(
            "POST", "/api/factory-reset",
            {"confirm": True, "password": "admin-password"}, token=token,
        ))
        self.assertEqual(200, writer.status)
        self.assertTrue(writer.json["ok"])
        self.assertTrue(writer.json["reboot"])
        self.assertEqual(["/config.json", "/skills"], writer.json["deleted"])
        self.assertEqual(1, self.app.factory_reset_calls)
        self.assertEqual([1000], self.app.reboot_delays)
        self.assertFalse(self.app.is_busy())

    def test_factory_reset_rejects_busy_device(self):
        token = self.token()
        self.assertTrue(self.app.begin_operation("serial"))
        with self.assertRaises(WebError) as raised:
            self.call(request(
                "POST", "/api/factory-reset",
                {"confirm": True, "password": "admin-password"}, token=token,
            ))
        self.assertEqual(409, raised.exception.status)
        self.assertEqual(0, self.app.factory_reset_calls)
        self.app.end_operation()

    def test_commands_whitelist_capture_and_busy(self):
        token = self.token()
        writer = self.call(request("POST", "/api/command",
                                   {"line": "/status"}, token=token))
        self.assertEqual(200, writer.status)
        self.assertIn("output for /status", writer.json["output"])
        self.assertNotIn("web-command", self.app.operations)

        for line in ("/background", "/background list", "/bg status 2"):
            writer = self.call(request("POST", "/api/command",
                                       {"line": line}, token=token))
            self.assertEqual(200, writer.status)
            self.assertIn("output for " + line, writer.json["output"])
        self.assertNotIn("web-command", self.app.operations)

        writer = self.call(request("POST", "/api/command",
                                   {"line": "/clear"}, token=token))
        self.assertEqual(200, writer.status)
        self.assertIn("web-command", self.app.operations)

        for line in (
                "/memory append note",
                "/http get https://example.com",
                "/native on", "/test", "/mcp",
                "/background stop", "/background note 2 dashboard",
                "/background discard 2", "/run", "/run bg note",
                "/workspace run tmp/a.py", "/continue",
                "/memory replace old => new"):
            writer = self.call(request("POST", "/api/command",
                                       {"line": line}, token=token))
            self.assertEqual(200, writer.status)
            self.assertIn("output for " + line, writer.json["output"])

        for line in ("/auto on", "/auto run on", "/mcp auto github on"):
            with self.assertRaises(WebError) as raised:
                self.call(request("POST", "/api/command",
                                  {"line": line}, token=token))
            self.assertEqual(400, raised.exception.status)
            self.assertEqual("confirm", raised.exception.code)
            writer = self.call(request("POST", "/api/command",
                                       {"line": line, "confirm": True},
                                       token=token))
            self.assertEqual(200, writer.status)
            self.assertTrue(writer.json["ok"])

        writer = self.call(request("POST", "/api/command",
                                   {"line": "/skills"}, token=token))
        self.assertEqual(200, writer.status)
        self.assertTrue(writer.json["ok"])
        self.assertIn("output for /skills", writer.json["output"])
        self.assertIn("web-command", self.app.operations)

        for line in ("/wifi", "/setup", "/model", "/ca", "/repl",
                     "/factory-reset", "/web reset-password",
                     "/peripherals", "/bad"):
            with self.assertRaises(WebError) as raised:
                self.call(request("POST", "/api/command",
                                  {"line": line}, token=token))
            self.assertEqual(400, raised.exception.status)
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/command",
                              {"line": "/" + "x" * 5000}, token=token))
        self.assertEqual(413, raised.exception.status)

        self.app.turn_lock.acquire()
        try:
            with self.assertRaises(WebError) as raised:
                self.call(request("POST", "/api/command",
                                  {"line": "/clear"}, token=token))
            self.assertEqual(409, raised.exception.status)
        finally:
            self.app.turn_lock.release()

    def test_chat_stream_code_and_pending_guard(self):
        token = self.token()
        self.app.config.values["auto_feedback"] = False
        self.app.agent.ask_code = "print(1)"
        self.app.agent.ask_text = "Here is code"
        writer = self.call(request("POST", "/api/chat",
                                   {"text": "do it"}, token=token))
        self.assertEqual(200, writer.status)
        self.assertTrue(writer.stream)
        kinds = [event["t"] for event in writer.events]
        self.assertEqual("start", kinds[0])
        self.assertIn("model_start", kinds)
        self.assertIn("model", kinds)
        self.assertIn("response", kinds)
        self.assertIn("code", kinds)
        self.assertEqual("done", kinds[-1])
        self.assertEqual("code", writer.events[-1]["pending"])
        self.assertEqual(0, writer.events[-1]["user_index"])
        self.assertEqual(0, self.app.agent.last_user_index)
        code = [event for event in writer.events if event["t"] == "code"][0]
        self.assertFalse(code["auto"])
        self.assertTrue(code["safe"])
        self.assertEqual(1, self.app.prepare_calls)
        self.assertEqual(0, self.app.agent.executed)
        self.assertFalse(self.app.is_busy())

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/chat",
                              {"text": "again"}, token=token))
        self.assertEqual(409, raised.exception.status)
        self.assertEqual("pending", raised.exception.code)

        self.app.agent.pending_source = None
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/chat", {"text": ""}, token=token))
        self.assertEqual(400, raised.exception.status)
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/chat",
                              {"text": "x" * 101}, token=token))
        self.assertEqual(413, raised.exception.status)

    def test_chat_streaming_does_not_duplicate_text(self):
        token = self.token()
        self.app.agent.ask_stream = True
        self.app.agent.ask_text = "streamed text"
        writer = self.call(request("POST", "/api/chat",
                                   {"text": "hello"}, token=token))
        contents = [event for event in writer.events if event["t"] == "content"]
        self.assertEqual(1, len(contents))
        self.assertEqual("streamed text", contents[0]["d"])

    def test_chat_start_stream_failure_releases_lock(self):
        token = self.token()
        writer = FakeWriter()
        writer.fail_stream = True
        req = request("POST", "/api/chat", {"text": "hello"}, token=token)
        self.web.handle(req, writer)
        self.assertFalse(self.app.is_busy())
        self.assertEqual([], writer.events)

        writer = FakeWriter()
        writer.raise_stream = True
        req = request("POST", "/api/chat", {"text": "hello"}, token=token)
        with self.assertRaises(RuntimeError):
            self.web.handle(req, writer)
        self.assertFalse(self.app.is_busy())

    def test_chat_question_event_ends_turn_without_code(self):
        token = self.token()
        self.app.agent.ask_text = "Which one?"
        self.app.agent.ask_question = {
            "header": "Choice",
            "question": "Which one?",
            "options": [{"label": "A", "description": ""}],
            "allow_custom": True,
        }
        writer = self.call(request("POST", "/api/chat",
                                   {"text": "choose"}, token=token))
        self.assertEqual(200, writer.status)
        kinds = [event["t"] for event in writer.events]
        self.assertIn("question", kinds)
        self.assertNotIn("code", kinds)
        self.assertNotIn("execution", kinds)
        event = [e for e in writer.events if e["t"] == "question"][0]
        self.assertEqual("Choice", event["header"])
        self.assertEqual("Which one?", event["question"])
        self.assertEqual("question", writer.events[-1]["pending"])
        self.assertEqual(0, self.app.agent.executed)

    def test_chat_auto_executes_safe_code_and_feedback(self):
        token = self.token()
        self.app.config.values["auto_run"] = True
        self.app.config.values["auto_feedback"] = True
        self.app.agent.ask_code = "print(1)"
        self.app.agent.feedback_code = None
        writer = self.call(request("POST", "/api/chat",
                                   {"text": "run"}, token=token))
        kinds = [event["t"] for event in writer.events]
        self.assertIn("execution", kinds)
        self.assertIn("feedback", kinds)
        self.assertEqual("done", kinds[-1])
        self.assertEqual("none", writer.events[-1]["pending"])
        code = [event for event in writer.events if event["t"] == "code"][0]
        self.assertTrue(code["auto"])
        self.assertEqual(1, self.app.agent.executed)
        self.assertEqual(1, self.app.agent.feedback_calls)
        self.assertEqual(1, self.app.main_calls)
        self.assertGreaterEqual(self.app.prepare_calls, 2)

    def test_chat_auto_blocks_unsafe_loop(self):
        token = self.token()
        self.app.config.values["auto_run"] = True
        self.app.agent.ask_code = "while True:\n    pass"
        writer = self.call(request("POST", "/api/chat",
                                   {"text": "run"}, token=token))
        code = [event for event in writer.events if event["t"] == "code"][0]
        self.assertFalse(code["auto"])
        self.assertFalse(code["safe"])
        self.assertNotIn("execution", [event["t"] for event in writer.events])
        self.assertEqual(0, self.app.agent.executed)

    def test_chat_disconnect_stops_before_execution(self):
        token = self.token()
        self.app.config.values["auto_run"] = True
        self.app.agent.ask_code = "print(1)"
        writer = FakeWriter()
        writer.fail_after = 0
        req = request("POST", "/api/chat", {"text": "run"}, token=token)
        self.web.handle(req, writer)
        self.assertEqual(0, self.app.agent.executed)
        self.assertFalse(self.app.is_busy())
        self.assertNotIn("error", [e["t"] for e in writer.events])

    def test_chat_client_stop_cancels_stream_mid_turn(self):
        token = self.token()
        self.app.config.values["auto_run"] = True
        self.app.agent.ask_code = "print(1)"
        self.app.agent.ask_stream = True
        self.app.agent.ask_text = "partial answer"
        writer = FakeWriter()
        writer.fail_after = 2
        req = request("POST", "/api/chat", {"text": "run"}, token=token)
        self.web.handle(req, writer)
        self.assertEqual(0, self.app.agent.executed)
        self.assertFalse(self.app.is_busy())
        kinds = [e["t"] for e in writer.events]
        self.assertEqual(["start", "model_start"], kinds)
        self.assertNotIn("error", kinds)
        self.assertNotIn("done", kinds)

    def test_manual_run_executes_once_and_requests_feedback(self):
        token = self.token()
        self.app.agent.pending_source = "print(1)"
        self.app.agent.feedback_code = "print(2)"
        writer = self.call(request("POST", "/api/run", {}, token=token))
        kinds = [event["t"] for event in writer.events]
        self.assertEqual("start", kinds[0])
        self.assertIn("execution", kinds)
        self.assertIn("feedback", kinds)
        self.assertIn("code", kinds)
        self.assertEqual("code", writer.events[-1]["pending"])
        self.assertEqual(1, self.app.agent.executed)
        self.assertEqual(1, self.app.agent.feedback_calls)
        self.assertEqual("print(2)", self.app.agent.pending_source)
        self.assertEqual(1, self.app.prepare_calls)

        self.app.agent.recover = True
        writer = self.call(request("POST", "/api/run", {}, token=token))
        self.assertIn("execution", [event["t"] for event in writer.events])
        self.assertIn("notice", [event["t"] for event in writer.events])
        self.assertEqual(1, self.app.agent.feedback_calls)

    def test_run_rejects_non_code_pending(self):
        token = self.token()
        self.app.agent.pending_skill = {"name": "s", "description": "", "body": ""}
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/run", {}, token=token))
        self.assertEqual(409, raised.exception.status)

    def test_save_skill_and_discard(self):
        token = self.token()
        self.app.agent.pending_skill = {
            "name": "demo-skill", "description": "demo", "body": "# demo"
        }
        writer = self.call(request("POST", "/api/save-skill", {}, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual("demo-skill", writer.json["name"])
        self.assertIsNone(self.app.agent.pending_skill)

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/save-skill", {}, token=token))
        self.assertEqual(400, raised.exception.status)

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/save-tool", {}, token=token))
        self.assertEqual(404, raised.exception.status)

        self.app.agent.pending_source = "print(1)"
        writer = self.call(request("POST", "/api/discard", {}, token=token))
        self.assertTrue(writer.json["had_pending"])
        self.assertIsNone(self.app.agent.pending_source)
        writer = self.call(request("POST", "/api/discard", {}, token=token))
        self.assertFalse(writer.json["had_pending"])

    def test_wifi_connect_saves_and_defers(self):
        token = self.token()
        writer = self.call(request("POST", "/api/wifi/connect", {
            "ssid": "new-home", "password": "new-password",
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertTrue(writer.json["connecting"])
        self.assertEqual(1, self.app.start_ap_calls)
        self.assertEqual(("new-home", "new-password"), self.app.pending_wifi)
        self.assertEqual("new-home", self.app.config.secret("wifi_ssid"))
        self.assertEqual("new-password", self.app.config.secret("wifi_password"))
        self.assertGreaterEqual(self.app.http.closed, 1)

        writer = self.call(request("POST", "/api/wifi/connect", {
            "ssid": "new-home", "password": "-",
        }, token=token))
        self.assertEqual(200, writer.status)
        self.assertEqual(("new-home", ""), self.app.pending_wifi)

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/wifi/connect",
                              {"ssid": ""}, token=token))
        self.assertEqual(400, raised.exception.status)

        self.app.start_ap_error = RuntimeError("no radio")
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/wifi/connect", {
                "ssid": "other", "password": "pw",
            }, token=token))
        self.assertEqual(503, raised.exception.status)
        self.assertEqual("new-home", self.app.config.secret("wifi_ssid"))

    def test_test_endpoint(self):
        token = self.token()
        writer = self.call(request("POST", "/api/test",
                                   {"kind": "api"}, token=token))
        self.assertEqual(200, writer.status)
        self.assertTrue(writer.json["ok"])
        self.assertEqual(2, writer.json["chars"])
        self.assertTrue(writer.json["streamed"])
        writer = self.call(request("POST", "/api/test",
                                   {"kind": "wifi"}, token=token))
        self.assertTrue(writer.json["ok"])
        self.assertEqual(1, self.app.prepare_network_calls)
        self.assertEqual(1, self.app.prepare_calls)
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/test",
                              {"kind": "other"}, token=token))
        self.assertEqual(400, raised.exception.status)

        self.app.prepare_error = RuntimeError("no wifi")
        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/test",
                              {"kind": "wifi"}, token=token))
        self.assertEqual(503, raised.exception.status)

    def test_static_assets_and_unknown_routes(self):
        writer = self.call(request("GET", "/"))
        self.assertEqual("text/html; charset=utf-8", writer.content_type)
        self.assertIn("ESP32 Harness", writer.body)
        self.assertIn('id="stopBtn"', writer.body)
        self.assertIn('value="max">Max</option>', writer.body)
        self.assertNotIn('value="medium"', writer.body)
        writer = self.call(request("GET", "/app.css"))
        self.assertEqual("text/css; charset=utf-8", writer.content_type)
        self.assertIn("[hidden]{display:none !important}", writer.body)
        writer = self.call(request("GET", "/app.js"))
        self.assertIn("function renderStatus", writer.body)
        self.assertIn("function stopStream", writer.body)
        self.assertIn("function renderQuestion", writer.body)
        self.assertIn("已开始的代码不会中断", writer.body)
        self.assertIn("status.busy", writer.body)
        self.assertIn("data.ok===false", writer.body)
        self.assertIn("ap_off_in_s", writer.body)
        writer = self.call(request("GET", "/favicon.ico"))
        self.assertEqual(204, writer.status)
        with self.assertRaises(WebError) as raised:
            self.call(request("GET", "/missing"))
        self.assertEqual(404, raised.exception.status)


    def test_schedule_api_crud_run_and_code_confirmation(self):
        token = self.token()
        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual(200, status.status)
        self.assertEqual([], status.json["schedules"])

        created = self.call(request("POST", "/api/schedules", {
            "action": "create",
            "type": "prompt",
            "name": "sensor",
            "prompt": "Check the sensor",
            "when": "once",
            "delay_s": 600,
        }, token=token))
        self.assertEqual(200, created.status)
        self.assertEqual(1, created.json["task"]["id"])
        self.assertEqual("sensor", created.json["task"]["name"])
        self.assertEqual("prompt", created.json["task"]["type"])
        self.assertEqual(1, len(created.json["tasks"]))

        full = self.call(request(
            "GET", "/api/schedules", token=token, query={"id": "1"}))
        self.assertEqual(200, full.status)
        self.assertEqual("Check the sensor", full.json["tasks"][0]["prompt"])

        updated = self.call(request("POST", "/api/schedules", {
            "action": "update", "id": 1, "name": "renamed",
        }, token=token))
        self.assertEqual(200, updated.status)
        self.assertEqual("renamed", updated.json["task"]["name"])

        queued = self.call(request("POST", "/api/schedules", {
            "action": "run", "id": 1,
        }, token=token))
        self.assertEqual(200, queued.status)
        self.assertIn("queued", queued.json["message"])
        self.assertEqual(
            self.app.schedule_now,
            self.app.schedules.get(1)["next_run"])

        disabled = self.call(request("POST", "/api/schedules", {
            "action": "disable", "id": 1,
        }, token=token))
        self.assertEqual(200, disabled.status)
        self.assertFalse(disabled.json["task"]["enabled"])

        deleted = self.call(request("POST", "/api/schedules", {
            "action": "delete", "id": 1,
        }, token=token))
        self.assertEqual(200, deleted.status)
        self.assertEqual([], deleted.json["tasks"])

        with self.assertRaises(WebError) as raised:
            self.call(request("POST", "/api/schedules", {
                "action": "create",
                "type": "code",
                "name": "blink",
                "source": "print(1)",
                "when": "once",
                "delay_s": 30,
            }, token=token))
        self.assertEqual(400, raised.exception.status)
        self.assertEqual("confirm", raised.exception.code)

        confirmed = self.call(request("POST", "/api/schedules", {
            "action": "create",
            "type": "code",
            "name": "blink",
            "source": "print(1)",
            "when": "once",
            "delay_s": 30,
            "confirm": True,
        }, token=token))
        self.assertEqual(200, confirmed.status)
        self.assertEqual("code", confirmed.json["task"]["type"])

        status = self.call(request("GET", "/api/status", token=token))
        self.assertEqual(1, len(status.json["schedules"]))
        self.assertEqual(
            "blink", status.json["schedules"][0]["name"])


class WebTurnSinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = FakeApp(self.temp.name)
        self.app.config.values["web_output_limit"] = 10
        self.writer = FakeWriter()
        self.sink = WebTurnSink(self.writer, self.app)

    def tearDown(self):
        self.temp.cleanup()

    def test_batching_truncation_and_json_events(self):
        self.sink.start()
        self.sink.reasoning("abcdef")
        self.sink.content("ghijkl")
        self.sink.flush()
        self.assertEqual("start", self.writer.events[0]["t"])
        reasoning = [e for e in self.writer.events if e["t"] == "reasoning"]
        content = [e for e in self.writer.events if e["t"] == "content"]
        self.assertEqual("abcdef", reasoning[0]["d"])
        self.assertEqual("ghijkl", content[0]["d"])
        self.sink.notice("hello")
        self.assertEqual("notice", self.writer.events[-1]["t"])
        self.assertEqual(6, self.sink.size)
        self.assertEqual(6, self.sink.reasoning_size)

        self.sink.tool_result("demo", FakeResult(
            ok=False, output="o" * 3000, error="e" * 1000, elapsed_ms=5))
        tool = self.writer.events[-1]
        self.assertEqual(2048, len(tool["output"]))
        self.assertTrue(tool["output"].endswith("[truncated]"))
        self.assertEqual(512, len(tool["error"]))

    def test_question_event_is_structured(self):
        self.sink.question({
            "header": "Approach",
            "question": "Which one?",
            "options": [{"label": "A", "description": "first"}],
            "allow_custom": True,
        })
        event = self.writer.events[-1]
        self.assertEqual("question", event["t"])
        self.assertEqual("Approach", event["header"])
        self.assertEqual("Which one?", event["question"])
        self.assertEqual("A", event["options"][0]["label"])
        self.assertTrue(event["allow_custom"])

    def test_non_streamed_response_is_chunked_without_limit(self):
        self.app.config.values["web_output_limit"] = 5
        sink = WebTurnSink(self.writer, self.app)
        text = "x" * 5000
        sink.response(text, True, False)
        contents = [e for e in self.writer.events if e["t"] == "content"]
        self.assertEqual([2048, 2048, 904], [len(e["d"]) for e in contents])
        self.assertEqual(5000, sink.size)
        self.assertEqual("response", self.writer.events[-1]["t"])
        self.assertEqual("", self.writer.events[-1]["text"])

    def test_stream_batches_at_configured_char_count(self):
        self.app.config.values["web_output_limit"] = 1000
        self.app.config.values["web_stream_batch_chars"] = 4
        sink = WebTurnSink(self.writer, self.app)
        sink.reasoning("ab")
        sink.reasoning("cd")
        sink.reasoning("ef")
        sink.content("gh")
        sink.flush()
        reasoning = [e for e in self.writer.events if e["t"] == "reasoning"]
        content = [e for e in self.writer.events if e["t"] == "content"]
        self.assertEqual(["abcd", "ef"], [e["d"] for e in reasoning])
        self.assertEqual(["gh"], [e["d"] for e in content])

    def test_reasoning_and_content_are_not_limited(self):
        self.app.config.values["web_output_limit"] = 5
        sink = WebTurnSink(self.writer, self.app)
        sink.reasoning("abcdefghij")
        sink.content("klmnopqrst")
        sink.flush()
        reasoning = [e for e in self.writer.events if e["t"] == "reasoning"]
        content = [e for e in self.writer.events if e["t"] == "content"]
        self.assertEqual("abcdefghij", reasoning[0]["d"])
        self.assertEqual("klmnopqrst", content[0]["d"])
        self.assertEqual(10, sink.reasoning_size)
        self.assertEqual(10, sink.size)

    def test_closed_writer_cancels_turn(self):
        self.writer.fail_after = 0
        with self.assertRaises(LLMStreamCancelled):
            self.sink.start()
        self.assertTrue(self.sink.closed)
        self.sink.stop()
        self.assertFalse(self.sink.notice("ignored"))
        self.assertEqual([], self.writer.events)


class CommandTests(unittest.TestCase):
    def test_command_sink_confirmation(self):
        sink = CommandCaptureSink(128)
        with self.assertRaises(WebCommandConfirm):
            sink.ask_yes_no("Proceed?")
        confirmed = CommandCaptureSink(128, confirmed=True)
        self.assertTrue(confirmed.ask_yes_no("Proceed?"))
        self.assertIn("yes (confirmed)", confirmed.getvalue())

    def test_command_is_exclusive(self):
        self.assertFalse(command_is_exclusive("/status"))
        self.assertFalse(command_is_exclusive("/history"))
        self.assertFalse(command_is_exclusive("/thinking"))
        self.assertFalse(command_is_exclusive("/auto"))
        self.assertFalse(command_is_exclusive("/memory"))
        self.assertFalse(command_is_exclusive("/workspace list"))
        self.assertFalse(command_is_exclusive("/workspace read notes.txt"))
        self.assertTrue(command_is_exclusive("/code"))
        self.assertFalse(command_is_exclusive("/web"))
        self.assertFalse(command_is_exclusive("/background"))
        self.assertFalse(command_is_exclusive("/background list"))
        self.assertFalse(command_is_exclusive("/bg status 2"))
        self.assertTrue(command_is_exclusive("/background stop"))
        self.assertTrue(command_is_exclusive("/background note 2 dashboard"))
        self.assertFalse(command_is_exclusive("/schedule"))
        self.assertFalse(command_is_exclusive("/schedule list"))
        self.assertFalse(command_is_exclusive("/schedule status 1"))
        self.assertTrue(command_is_exclusive("/schedule run 1"))
        self.assertTrue(command_is_exclusive("/schedule delete 1"))
        self.assertTrue(command_is_exclusive("/clear"))
        self.assertTrue(command_is_exclusive("/discard"))
        self.assertTrue(command_is_exclusive("/run"))
        self.assertTrue(command_is_exclusive("/auto on"))
        self.assertTrue(command_is_exclusive("/thinking show on"))
        self.assertTrue(command_is_exclusive("/workspace run x.py"))
        self.assertTrue(command_is_exclusive("/web reset-password"))
        self.assertTrue(command_is_exclusive("/unknown"))


if __name__ == "__main__":
    unittest.main()
