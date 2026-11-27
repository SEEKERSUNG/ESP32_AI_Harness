"""Bounded, atomic conversation persistence for reset/reboot survival.

Only the committed chat history, the compaction summary and the first user
request anchor are stored. Pending generated code is handled separately by
recovery.py and is never auto-run.
"""

import os

from .config import ConfigError, join_path, read_json, remove_file, write_json


DEFAULT_PATH = "/state/conversation.json"
DEFAULT_BACKGROUND_PATH = "/state/background.json"


class ConversationStore:
    def __init__(self, root="/", config=None):
        self.config = config
        self.enabled = True
        self.max_chars = 200000
        self.max_messages = 120
        path = DEFAULT_PATH
        if config is not None:
            self.enabled = bool(config.get("persist_conversation", True))
            try:
                self.max_chars = int(config.get("conversation_max_chars", 200000))
            except (TypeError, ValueError):
                self.max_chars = 200000
            try:
                self.max_messages = int(config.get("conversation_max_messages", 120))
            except (TypeError, ValueError):
                self.max_messages = 120
            path = str(config.get("conversation_file", DEFAULT_PATH) or DEFAULT_PATH)
        if not path.startswith("/") or ".." in path:
            path = DEFAULT_PATH
        self.max_chars = max(4096, min(self.max_chars, 900000))
        self.max_messages = max(4, min(self.max_messages, 1000))
        self.path = join_path(root, path.lstrip("/"))
        self.last_error = ""
        self.last_messages = 0
        self.last_chars = 0

    def load(self):
        if not self.enabled:
            return None
        try:
            data = read_json(self.path, None)
        except (ConfigError, OSError, ValueError) as exc:
            self.last_error = str(exc)
            return None
        if not isinstance(data, dict):
            return None
        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            return None
        messages = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            if not content:
                continue
            message = {"role": role, "content": content}
            kind = item.get("kind")
            if kind in ("user", "assistant", "observation"):
                message["kind"] = kind
            reasoning = item.get("reasoning")
            if role == "assistant" and isinstance(reasoning, str) and reasoning:
                message["reasoning"] = reasoning[:4096]
            messages.append(message)
        summary = data.get("summary", "")
        if not isinstance(summary, str):
            summary = ""
        original_request = data.get("original_request", "")
        if not isinstance(original_request, str):
            original_request = ""
        original_request = original_request[:4096]
        messages = self._bounded(messages)
        self.last_messages = len(messages)
        self.last_chars = self._chars(messages)
        return {
            "messages": messages,
            "summary": summary,
            "original_request": original_request,
            "saved_at": data.get("saved_at", 0),
        }

    def save(self, messages, summary="", original_request=""):
        if not self.enabled:
            return False
        bounded = self._bounded(messages)
        if not isinstance(summary, str):
            summary = ""
        if not isinstance(original_request, str):
            original_request = ""
        payload = {
            "version": 1,
            "saved_at": self._now(),
            "summary": summary,
            "original_request": original_request[:4096],
            "messages": bounded,
        }
        self._ensure_directory()
        try:
            write_json(self.path, payload)
        except Exception:
            raise
        self.last_error = ""
        self.last_messages = len(bounded)
        self.last_chars = self._chars(bounded)
        return True

    def clear(self):
        remove_file(self.path)
        self.last_messages = 0
        self.last_chars = 0
        self.last_error = ""

    def exists(self):
        try:
            os.stat(self.path)
            return True
        except OSError:
            return False

    def status(self):
        return {
            "enabled": self.enabled,
            "path": self.path,
            "exists": self.exists(),
            "messages": self.last_messages,
            "chars": self.last_chars,
        }

    def _bounded(self, messages):
        selected = []
        total = 0
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            if len(selected) >= self.max_messages:
                break
            reasoning = message.get("reasoning")
            if role != "assistant" or not isinstance(reasoning, str):
                reasoning = ""
            reasoning = reasoning[:4096]
            if total + len(content) + len(reasoning) > self.max_chars:
                if selected:
                    break
                # A single oversized message: keep its most recent content
                # tail and drop the optional reasoning drawer.
                content = content[-self.max_chars:]
                reasoning = ""
            kind = message.get("kind")
            stored = {"role": role, "content": content}
            if kind in ("user", "assistant", "observation"):
                stored["kind"] = kind
            if reasoning:
                stored["reasoning"] = reasoning
            selected.append(stored)
            total += len(content) + len(reasoning)
        selected.reverse()
        return selected

    @staticmethod
    def _chars(messages):
        total = 0
        for message in messages:
            total += len(message.get("content", ""))
            total += len(message.get("reasoning", ""))
        return total

    def _ensure_directory(self):
        directory = self.path.rsplit("/", 1)[0] if "/" in self.path else ""
        if not directory or directory == "/":
            return
        parts = directory.strip("/").split("/")
        current = "/" if directory.startswith("/") else ""
        for part in parts:
            if not part:
                continue
            current = (current + part) if current.endswith("/") else (
                current + "/" + part if current else part
            )
            try:
                os.mkdir(current)
            except OSError:
                pass

    @staticmethod
    def _now():
        try:
            import time
            if hasattr(time, "time"):
                return int(time.time())
            if hasattr(time, "ticks_ms"):
                return int(time.ticks_ms())
        except Exception:
            pass
        return 0


class BackgroundStore:
    """Persist bounded background-job records until their turns resume.

    The source itself is not stored: it already exists in the assistant
    message. Only the bounded result (and a failure reason) is needed to
    continue the conversation after a reboot. Jobs that were actively running
    when the device restarted are restored as visible error records rather
    than silently executed again.
    """

    OUTPUT_LIMIT = 4096
    ERROR_LIMIT = 1024
    MAX_JOBS = 16

    def __init__(self, root="/", config=None):
        self.config = config
        self.enabled = True
        if config is not None:
            self.enabled = bool(config.get("persist_conversation", True))
        self.path = join_path(
            root, DEFAULT_BACKGROUND_PATH.lstrip("/"))
        self.last_error = ""

    def _serialize_result(self, result):
        if result is None:
            return None
        if isinstance(result, dict):
            output = result.get("output", "")
            error = result.get("error", "")
            ok = result.get("ok")
            timed_out = result.get("timed_out")
            cancelled = result.get("cancelled")
            elapsed_ms = result.get("elapsed_ms", 0)
        else:
            output = getattr(result, "output", "")
            error = getattr(result, "error", "")
            ok = getattr(result, "ok", False)
            timed_out = getattr(result, "timed_out", False)
            cancelled = getattr(result, "cancelled", False)
            elapsed_ms = getattr(result, "elapsed_ms", 0)
        if not isinstance(output, str):
            output = str(output)
        if not isinstance(error, str):
            error = str(error)
        try:
            elapsed_ms = int(elapsed_ms or 0)
        except (TypeError, ValueError):
            elapsed_ms = 0
        return {
            "ok": bool(ok),
            "timed_out": bool(timed_out),
            "cancelled": bool(cancelled),
            "output": output[-self.OUTPUT_LIMIT:],
            "error": error[:self.ERROR_LIMIT],
            "elapsed_ms": max(0, elapsed_ms),
        }

    def _serialize_job(self, job):
        return {
            "id": int(job.get("id", 0) or 0),
            "token": str(job.get("token", ""))[:48],
            "phase": job.get("phase", "ready"),
            "auto": bool(job.get("auto", False)),
            "recovered": bool(job.get("recovered", False)),
            "driver": str(job.get("driver", "web"))[:16],
            "timeout": int(job.get("timeout", 0) or 0),
            "source_chars": int(job.get("source_chars", 0) or 0),
            "task": str(job.get("task", ""))[:200],
            "note": str(job.get("note", ""))[:200],
            "turns": int(job.get("turns", 0) or 0),
            "created_ms": int(job.get("created_ms", 0) or 0),
            "started_ms": int(job.get("started_ms", 0) or 0),
            "finished_ms": int(job.get("finished_ms", 0) or 0),
            "stop_reason": str(job.get("stop_reason", ""))[:120],
            "discard_on_finish": bool(job.get("discard_on_finish", False)),
            "error": str(job.get("error", ""))[:self.ERROR_LIMIT],
            "result": self._serialize_result(job.get("result")),
        }

    def _normalize_job(self, job):
        if not isinstance(job, dict):
            return None
        phase = job.get("phase")
        error = str(job.get("error", ""))[:self.ERROR_LIMIT]
        if phase not in ("ready", "error"):
            # The worker thread did not survive the restart; it must not be
            # resumed automatically. Show it as an error the user can discard.
            phase = "error"
            error = "Interrupted by a device restart before the job finished."
        try:
            job_id = int(job.get("id", 0))
            timeout = int(job.get("timeout", 0) or 0)
            source_chars = int(job.get("source_chars", 0) or 0)
            turns = int(job.get("turns", 0) or 0)
            created_ms = int(job.get("created_ms", 0) or 0)
            started_ms = int(job.get("started_ms", 0) or 0)
            finished_ms = int(job.get("finished_ms", 0) or 0)
        except (TypeError, ValueError):
            return None
        normalized = {
            "id": job_id,
            "token": str(job.get("token", ""))[:48],
            "phase": phase,
            "auto": bool(job.get("auto", False)),
            "recovered": bool(job.get("recovered", False)),
            "driver": str(job.get("driver", "web"))[:16],
            "timeout": timeout,
            "source_chars": max(0, source_chars),
            "task": str(job.get("task", ""))[:200],
            "note": str(job.get("note", ""))[:200],
            "turns": max(0, turns),
            "created_ms": max(0, created_ms),
            "started_ms": 0,
            "finished_ms": 0,
            "stop_requested": False,
            "stop_reason": str(job.get("stop_reason", ""))[:120],
            "discard_on_finish": bool(job.get("discard_on_finish", False)),
            "error": error,
            "result": None,
        }
        result = job.get("result")
        if isinstance(result, dict):
            output = result.get("output", "")
            result_error = result.get("error", "")
            if not isinstance(output, str):
                output = str(output)
            if not isinstance(result_error, str):
                result_error = str(result_error)
            try:
                elapsed_ms = int(result.get("elapsed_ms", 0) or 0)
            except (TypeError, ValueError):
                elapsed_ms = 0
            normalized["result"] = {
                "ok": bool(result.get("ok")),
                "timed_out": bool(result.get("timed_out")),
                "cancelled": bool(result.get("cancelled")),
                "output": output[-self.OUTPUT_LIMIT:],
                "error": result_error[:self.ERROR_LIMIT],
                "elapsed_ms": max(0, elapsed_ms),
            }
        if normalized["phase"] == "ready" and normalized["result"] is None:
            normalized["phase"] = "error"
            normalized["error"] = (
                "Background result was unavailable after a device restart.")
        return normalized

    def load_jobs(self):
        if not self.enabled:
            return []
        try:
            data = read_json(self.path, None)
        except (ConfigError, OSError, ValueError) as exc:
            self.last_error = str(exc)
            return []
        if not isinstance(data, dict):
            return []
        raw = data.get("jobs")
        if not isinstance(raw, list):
            legacy = data.get("job")
            raw = [legacy] if isinstance(legacy, dict) else []
        jobs = []
        for item in raw[:self.MAX_JOBS]:
            normalized = self._normalize_job(item)
            if normalized is not None:
                jobs.append(normalized)
        return jobs

    def load(self):
        jobs = self.load_jobs()
        return jobs[0] if jobs else None

    def save_jobs(self, jobs):
        if not self.enabled:
            return False
        payload = {
            "version": 2,
            "saved_at": ConversationStore._now(),
            "jobs": [
                self._serialize_job(job)
                for job in list(jobs or [])[:self.MAX_JOBS]
            ],
        }
        self._ensure_directory()
        write_json(self.path, payload)
        self.last_error = ""
        return True

    def save(self, job):
        return self.save_jobs([job])

    def clear(self):
        remove_file(self.path)
        self.last_error = ""

    def exists(self):
        try:
            os.stat(self.path)
            return True
        except OSError:
            return False

    def _ensure_directory(self):
        directory = self.path.rsplit("/", 1)[0] if "/" in self.path else ""
        if not directory or directory == "/":
            return
        parts = directory.strip("/").split("/")
        current = "/" if directory.startswith("/") else ""
        for part in parts:
            if not part:
                continue
            current = (current + part) if current.endswith("/") else (
                current + "/" + part if current else part
            )
            try:
                os.mkdir(current)
            except OSError:
                pass
