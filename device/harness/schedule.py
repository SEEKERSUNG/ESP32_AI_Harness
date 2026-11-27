"""Wall-clock scheduled tasks persisted on the board.

A task is either a prompt for the agent or a stored MicroPython source that is
started as a background job.  Times are stored as UTC epoch seconds; input
such as ``2026-11-27 08:00`` or ``08:00`` is interpreted in the configured
fixed UTC offset.  Execution is driven by the serial main thread
(``Application.service_schedules``); this module only owns validation, state
and persistence, so it stays testable on CPython.
"""

import os

try:
    import _thread
except ImportError:
    _thread = None

from . import clock
from .config import join_path, read_json, remove_file, write_json

DEFAULT_PATH = "/state/schedules.json"
DEFAULT_MAX_TASKS = 8
MAX_TASKS_LIMIT = 16
MAX_NAME = 48
MAX_PROMPT = 2000
MAX_SOURCE = 8192
MAX_ERROR = 512
MAX_AT_TEXT = 32
MIN_INTERVAL_S = 30
MAX_INTERVAL_S = 30 * 86400
MAX_DELAY_S = 366 * 86400
RETRY_DELAY_S = 60
MAX_ATTEMPTS = 3
TYPES = ("prompt", "code")
WHENS = ("once", "interval", "daily")
FINISHED_STATUS = ("", "ok", "error")


def _bounded_int(value, default, minimum, maximum):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if result < minimum:
        result = minimum
    if result > maximum:
        result = maximum
    return result


def _clip(value, limit):
    text = str(value or "")
    if len(text) > limit:
        return text[:limit]
    return text


def _one_line(value, limit):
    return _clip(" ".join(str(value or "").split()), limit)


def _as_bool(value, default=True):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    return bool(default)


def _parse_clock_text(text):
    """Parse 'HH:MM' or 'HH:MM:SS' and return (hour, minute) or None."""
    value = str(text or "").strip()
    parts = value.split(":")
    if len(parts) < 2 or len(parts) > 3:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        if len(parts) == 3:
            second = int(parts[2])
            if second != 0:
                return None
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _human_seconds(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "?"
    if value >= 86400 and value % 86400 == 0:
        return "%s day(s)" % (value // 86400)
    if value >= 3600 and value % 3600 == 0:
        return "%s hour(s)" % (value // 3600)
    if value >= 60 and value % 60 == 0:
        return "%s minute(s)" % (value // 60)
    return "%s second(s)" % value


class ScheduleStore:
    """Bounded JSON persistence for scheduled tasks.

    Unlike the conversation store this stays enabled even when conversation
    persistence is off: schedules are user configuration with side effects,
    not a resumable chat transcript.
    """

    def __init__(self, root="/", config=None):
        path = DEFAULT_PATH
        if config is not None:
            path = str(config.get("schedule_file", DEFAULT_PATH) or DEFAULT_PATH)
        if not path.startswith("/") or ".." in path:
            path = DEFAULT_PATH
        self.path = join_path(root, path.lstrip("/"))
        self.last_error = ""

    def load(self):
        try:
            data = read_json(self.path, {})
        except Exception as exc:
            self.last_error = str(exc)
            return []
        if not isinstance(data, dict):
            return []
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list):
            return []
        return raw_tasks

    def save(self, tasks):
        self._ensure_directory()
        payload = {"version": 1, "tasks": tasks}
        try:
            write_json(self.path, payload)
        except Exception as exc:
            self.last_error = str(exc)
            raise
        self.last_error = ""
        return True

    def clear(self):
        remove_file(self.path)
        self.last_error = ""

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


class Scheduler:
    def __init__(self, config, store=None):
        self.config = config
        self.store = store
        self.lock = _thread.allocate_lock() if _thread is not None else None
        self.tasks = []
        self.seq = 0
        self.last_error = ""
        self.max_tasks = _bounded_int(
            config.get("schedule_max_tasks", DEFAULT_MAX_TASKS),
            DEFAULT_MAX_TASKS, 1, MAX_TASKS_LIMIT)
        self.min_interval = _bounded_int(
            config.get("schedule_min_interval_s", MIN_INTERVAL_S),
            MIN_INTERVAL_S, 5, MAX_INTERVAL_S)

    # -- locking ---------------------------------------------------------
    def _acquire(self):
        if self.lock is not None:
            self.lock.acquire()

    def _release(self):
        if self.lock is not None:
            self.lock.release()

    # -- persistence -----------------------------------------------------
    def _save_locked(self):
        if self.store is None:
            return
        try:
            self.store.save([dict(task) for task in self.tasks])
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
        if getattr(self.store, "last_error", ""):
            self.last_error = self.store.last_error

    def restore(self):
        raw = self.store.load() if self.store is not None else []
        restored = []
        highest = 0
        for item in raw:
            task = self._normalize(item)
            if task is None:
                continue
            if task["id"] > highest:
                highest = task["id"]
            restored.append(task)
        self._acquire()
        try:
            self.tasks = restored
            if highest > self.seq:
                self.seq = highest
            if restored:
                self._save_locked()
        finally:
            self._release()
        return restored

    def _normalize(self, raw):
        if not isinstance(raw, dict):
            return None
        task_type = str(raw.get("type", "prompt") or "prompt").lower()
        if task_type not in TYPES:
            return None
        when = str(raw.get("when", "") or "").lower()
        if when not in WHENS:
            return None
        try:
            task_id = int(raw.get("id", 0) or 0)
        except (TypeError, ValueError):
            return None
        if task_id <= 0:
            return None
        prompt = str(raw.get("prompt", "") or "")
        source = str(raw.get("source", "") or "")
        if task_type == "prompt" and not prompt:
            return None
        if task_type == "code" and not source:
            return None
        name = _clip(str(raw.get("name", "") or "").strip(), MAX_NAME)
        if not name:
            name = "Scheduled task"
        try:
            next_run = int(raw.get("next_run", 0) or 0)
            last_run = int(raw.get("last_run", 0) or 0)
            created = int(raw.get("created", 0) or 0)
            run_count = int(raw.get("run_count", 0) or 0)
            attempts = int(raw.get("attempts", 0) or 0)
        except (TypeError, ValueError):
            return None
        if next_run <= 0:
            return None
        last_status = raw.get("last_status", "")
        if last_status not in FINISHED_STATUS:
            last_status = ""
        timeout = raw.get("timeout")
        if timeout is not None:
            timeout = _bounded_int(timeout, 0, 0, 86400)
        task = {
            "id": task_id,
            "name": name,
            "type": task_type,
            "prompt": _clip(prompt, MAX_PROMPT),
            "source": _clip(source, MAX_SOURCE),
            "timeout": timeout,
            "when": when,
            "delay_s": _bounded_int(
                raw.get("delay_s", 0) or 0, 0, 0, MAX_DELAY_S),
            "at_text": _clip(str(raw.get("at_text", "") or "").strip(),
                             MAX_AT_TEXT),
            "every_s": _bounded_int(
                raw.get("every_s", 0) or 0, 0, 0, MAX_INTERVAL_S),
            "enabled": _as_bool(raw.get("enabled", True), True),
            "created": max(0, created),
            "next_run": next_run,
            "last_run": max(0, last_run),
            "last_status": last_status,
            "last_error": _clip(raw.get("last_error", ""), MAX_ERROR),
            "run_count": max(0, run_count),
            "attempts": max(0, attempts),
            # A run that was interrupted by a reboot is retried at the next
            # opportunity; at-least-once is safer than silently skipping a
            # user's scheduled action.
            "running_since": 0,
        }
        if when == "daily" and _parse_clock_text(task["at_text"]) is None:
            return None
        return task

    # -- CRUD ------------------------------------------------------------
    def create(self, fields, now_epoch):
        fields = fields or {}
        task_type = str(fields.get("type", "prompt") or "prompt").strip().lower()
        if task_type not in TYPES:
            raise ValueError("type must be prompt or code")
        prompt = str(fields.get("prompt", "") or "").strip()
        source = str(fields.get("source", "") or "").strip()
        timeout = None
        if task_type == "prompt":
            if not prompt:
                raise ValueError("prompt is required for a prompt task")
            if len(prompt) > MAX_PROMPT:
                raise ValueError("prompt exceeds %s characters" % MAX_PROMPT)
            source = ""
        else:
            if not source:
                raise ValueError("source is required for a code task")
            if len(source) > MAX_SOURCE:
                raise ValueError("source exceeds %s characters" % MAX_SOURCE)
            try:
                compile(source, "<scheduled-code>", "exec")
            except Exception as exc:
                raise ValueError("source has a syntax error: %s" % exc)
            prompt = ""
            if fields.get("timeout") is not None:
                timeout = _bounded_int(fields.get("timeout"), 0, 0, 86400)
        when = str(fields.get("when", "once") or "once").strip().lower()
        if when not in WHENS:
            raise ValueError("when must be once, interval or daily")
        schedule = self._schedule_fields(when, fields, now_epoch)
        name = _clip(str(fields.get("name", "") or "").strip(), MAX_NAME)
        if not name:
            seed = prompt if task_type == "prompt" else source
            name = _one_line(seed, 40) or "Scheduled task"
        enabled = _as_bool(fields.get("enabled", True), True)
        self._acquire()
        try:
            if len(self.tasks) >= self.max_tasks:
                raise ValueError(
                    "Too many scheduled tasks (max %s); delete one first"
                    % self.max_tasks)
            self.seq += 1
            task = {
                "id": self.seq,
                "name": name,
                "type": task_type,
                "prompt": prompt,
                "source": source,
                "timeout": timeout,
                "when": when,
                "delay_s": schedule["delay_s"],
                "at_text": schedule["at_text"],
                "every_s": schedule["every_s"],
                "enabled": enabled,
                "created": int(now_epoch),
                "next_run": schedule["next_run"],
                "last_run": 0,
                "last_status": "",
                "last_error": "",
                "run_count": 0,
                "attempts": 0,
                "running_since": 0,
            }
            self.tasks.append(task)
        finally:
            self._release()
        self._acquire()
        try:
            self._save_locked()
        finally:
            self._release()
        return dict(task)

    def _schedule_fields(self, when, fields, now_epoch):
        delay_s = 0
        every_s = 0
        at_text = ""
        if when == "once":
            at_text = str(fields.get("at", "") or "").strip()
            raw_delay = fields.get("delay_s")
            if at_text:
                next_run = clock.parse_local_datetime(at_text, self.config)
                if next_run is None:
                    raise ValueError(
                        "at must be local YYYY-MM-DD HH:MM")
            elif raw_delay is not None and str(raw_delay) != "":
                delay_s = _bounded_int(raw_delay, -1, -1, MAX_DELAY_S)
                if delay_s < 1:
                    raise ValueError("delay_s must be at least 1 second")
                next_run = int(now_epoch) + delay_s
            else:
                raise ValueError(
                    "A one-time task needs delay_s or at (YYYY-MM-DD HH:MM)")
        elif when == "interval":
            raw_every = fields.get("every_s")
            if raw_every is None or str(raw_every) == "":
                raise ValueError("every_s is required for an interval task")
            every_s = _bounded_int(raw_every, -1, -1, MAX_INTERVAL_S)
            if every_s < self.min_interval:
                raise ValueError(
                    "every_s must be at least %s seconds" % self.min_interval)
            next_run = int(now_epoch) + every_s
        else:
            at_text = str(fields.get("at", "") or "").strip()
            parsed = _parse_clock_text(at_text)
            if parsed is None:
                raise ValueError("at must be local HH:MM for a daily task")
            next_run = clock.next_daily_epoch(
                int(now_epoch), parsed[0], parsed[1], self.config)
        return {
            "delay_s": delay_s,
            "at_text": at_text,
            "every_s": every_s,
            "next_run": next_run,
        }

    def get(self, task_id):
        target = self._task_id(task_id)
        if target is None:
            return None
        self._acquire()
        try:
            for task in self.tasks:
                if task.get("id") == target:
                    return dict(task)
            return None
        finally:
            self._release()

    @staticmethod
    def _task_id(task_id):
        try:
            return int(task_id)
        except (TypeError, ValueError):
            return None

    def snapshot(self):
        self._acquire()
        try:
            return [dict(task) for task in self.tasks]
        finally:
            self._release()

    def update(self, task_id, fields, now_epoch):
        target = self._task_id(task_id)
        if target is None:
            raise ValueError("id must be an integer")
        fields = fields or {}
        self._acquire()
        try:
            task = None
            for candidate in self.tasks:
                if candidate.get("id") == target:
                    task = candidate
                    break
            if task is None:
                raise ValueError("No scheduled task #%s" % target)
            changed = False
            if "name" in fields:
                name = _clip(str(fields.get("name") or "").strip(), MAX_NAME)
                if name:
                    task["name"] = name
                    changed = True
            task_type = task["type"]
            if "type" in fields:
                new_type = str(fields.get("type") or "").strip().lower()
                if new_type not in TYPES:
                    raise ValueError("type must be prompt or code")
                task_type = new_type
                task["type"] = new_type
                changed = True
            if "prompt" in fields and task_type == "prompt":
                prompt = str(fields.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError("prompt cannot be empty")
                if len(prompt) > MAX_PROMPT:
                    raise ValueError(
                        "prompt exceeds %s characters" % MAX_PROMPT)
                task["prompt"] = prompt
                changed = True
            if "source" in fields and task_type == "code":
                source = str(fields.get("source") or "").strip()
                if not source:
                    raise ValueError("source cannot be empty")
                if len(source) > MAX_SOURCE:
                    raise ValueError(
                        "source exceeds %s characters" % MAX_SOURCE)
                try:
                    compile(source, "<scheduled-code>", "exec")
                except Exception as exc:
                    raise ValueError(
                        "source has a syntax error: %s" % exc)
                task["source"] = source
                changed = True
            if "timeout" in fields and task_type == "code":
                task["timeout"] = (
                    None if fields.get("timeout") is None
                    else _bounded_int(fields.get("timeout"), 0, 0, 86400))
                changed = True
            if task_type == "prompt" and not task.get("prompt"):
                raise ValueError("prompt is required for a prompt task")
            if task_type == "code" and not task.get("source"):
                raise ValueError("source is required for a code task")
            schedule_keys = ("when", "delay_s", "at", "every_s")
            schedule_changed = False
            for key in schedule_keys:
                if key in fields:
                    schedule_changed = True
                    break
            if schedule_changed:
                when = str(fields.get("when", task["when"]) or "").strip().lower()
                if when not in WHENS:
                    raise ValueError("when must be once, interval or daily")
                schedule = self._schedule_fields(when, fields, now_epoch)
                task["when"] = when
                task["delay_s"] = schedule["delay_s"]
                task["at_text"] = schedule["at_text"]
                task["every_s"] = schedule["every_s"]
                task["next_run"] = schedule["next_run"]
                task["attempts"] = 0
                task["last_error"] = ""
                if "enabled" not in fields:
                    task["enabled"] = True
                changed = True
            if "enabled" in fields:
                task["enabled"] = _as_bool(fields.get("enabled"), True)
                if task["enabled"]:
                    task["attempts"] = 0
                changed = True
            if changed:
                self._save_locked()
            return dict(task)
        finally:
            self._release()

    def delete(self, task_id):
        target = self._task_id(task_id)
        if target is None:
            raise ValueError("id must be an integer")
        removed = None
        self._acquire()
        try:
            for task in list(self.tasks):
                if task.get("id") == target:
                    if task.get("running_since"):
                        raise ValueError(
                            "Scheduled task #%s is running; wait for it to "
                            "finish or disable it first" % target)
                    removed = task
                    self.tasks.remove(task)
                    break
            if removed is None:
                raise ValueError("No scheduled task #%s" % target)
            self._save_locked()
        finally:
            self._release()
        return dict(removed)

    def set_enabled(self, task_id, enabled):
        target = self._task_id(task_id)
        if target is None:
            raise ValueError("id must be an integer")
        self._acquire()
        try:
            for task in self.tasks:
                if task.get("id") == target:
                    task["enabled"] = bool(enabled)
                    if task["enabled"]:
                        task["attempts"] = 0
                        task["last_error"] = ""
                    self._save_locked()
                    return dict(task)
            raise ValueError("No scheduled task #%s" % target)
        finally:
            self._release()

    def request_run(self, task_id, now_epoch):
        target = self._task_id(task_id)
        if target is None:
            raise ValueError("id must be an integer")
        self._acquire()
        try:
            for task in self.tasks:
                if task.get("id") != target:
                    continue
                if task.get("running_since"):
                    raise ValueError(
                        "Scheduled task #%s is already running" % target)
                task["enabled"] = True
                task["next_run"] = int(now_epoch)
                task["attempts"] = 0
                task["last_error"] = ""
                self._save_locked()
                return dict(task)
            raise ValueError("No scheduled task #%s" % target)
        finally:
            self._release()

    # -- execution -------------------------------------------------------
    def due(self, now_epoch):
        """Oldest enabled task whose next_run is due, or None."""
        self._acquire()
        try:
            candidate = None
            for task in self.tasks:
                if not task.get("enabled"):
                    continue
                if task.get("running_since"):
                    continue
                next_run = int(task.get("next_run", 0) or 0)
                if next_run <= 0 or next_run > now_epoch:
                    continue
                if candidate is None or next_run < candidate["next_run"]:
                    candidate = task
            return dict(candidate) if candidate is not None else None
        finally:
            self._release()

    def begin_run(self, task_id, now_epoch):
        """Mark a due task running and return a copy, or None if not due."""
        target = self._task_id(task_id)
        if target is None:
            return None
        self._acquire()
        try:
            for task in self.tasks:
                if task.get("id") != target:
                    continue
                if not task.get("enabled") or task.get("running_since"):
                    return None
                if int(task.get("next_run", 0) or 0) > now_epoch:
                    return None
                task["running_since"] = int(now_epoch)
                task["last_run"] = int(now_epoch)
                task["run_count"] = int(task.get("run_count", 0) or 0) + 1
                self._save_locked()
                return dict(task)
            return None
        finally:
            self._release()

    def finish_run(self, task_id, ok, detail, now_epoch):
        target = self._task_id(task_id)
        if target is None:
            return None
        self._acquire()
        try:
            for task in self.tasks:
                if task.get("id") != target:
                    continue
                task["running_since"] = 0
                if ok:
                    task["last_status"] = "ok"
                    task["last_error"] = ""
                    task["attempts"] = 0
                    if task["when"] == "once":
                        task["enabled"] = False
                    else:
                        task["next_run"] = self._next_regular(task, now_epoch)
                else:
                    task["last_status"] = "error"
                    task["last_error"] = _clip(detail, MAX_ERROR)
                    attempts = int(task.get("attempts", 0) or 0) + 1
                    if attempts < MAX_ATTEMPTS:
                        task["attempts"] = attempts
                        task["next_run"] = int(now_epoch) + RETRY_DELAY_S
                        if task["when"] == "once":
                            task["enabled"] = True
                    else:
                        task["attempts"] = 0
                        if task["when"] == "once":
                            task["enabled"] = False
                        else:
                            task["next_run"] = self._next_regular(
                                task, now_epoch)
                self._save_locked()
                return dict(task)
            return None
        finally:
            self._release()

    def _next_regular(self, task, now_epoch):
        if task["when"] == "interval":
            every_s = max(1, int(task.get("every_s", 0) or 0))
            base = int(task.get("next_run", 0) or now_epoch)
            next_run = base + every_s
            if next_run <= now_epoch:
                next_run = int(now_epoch) + every_s
            return next_run
        if task["when"] == "daily":
            parsed = _parse_clock_text(task.get("at_text", ""))
            if parsed is None:
                return int(now_epoch) + 86400
            return clock.next_daily_epoch(
                int(now_epoch), parsed[0], parsed[1], self.config)
        return int(now_epoch) + 86400

    def rebase_timezone(self, now_epoch):
        """Recompute daily next-run instants after a timezone change."""
        changed = False
        self._acquire()
        try:
            for task in self.tasks:
                if task.get("when") != "daily" or not task.get("enabled"):
                    continue
                if task.get("running_since"):
                    continue
                parsed = _parse_clock_text(task.get("at_text", ""))
                if parsed is None:
                    continue
                task["next_run"] = clock.next_daily_epoch(
                    int(now_epoch), parsed[0], parsed[1], self.config)
                changed = True
            if changed:
                self._save_locked()
        finally:
            self._release()
        return changed

    # -- views -----------------------------------------------------------
    def views(self):
        return [self._view(task) for task in self.snapshot()]

    def view(self, task_id, full=False):
        task = self.get(task_id)
        if task is None:
            return None
        return self._view(task, full=full)

    def _view(self, task, full=False):
        task_type = task.get("type", "prompt")
        when = task.get("when", "once")
        seed = task.get("prompt") if task_type == "prompt" else task.get("source")
        view = {
            "id": int(task.get("id", 0) or 0),
            "name": _clip(task.get("name", ""), MAX_NAME),
            "type": task_type,
            "enabled": bool(task.get("enabled")),
            "when": when,
            "delay_s": int(task.get("delay_s", 0) or 0),
            "at_text": _clip(task.get("at_text", ""), MAX_AT_TEXT),
            "every_s": int(task.get("every_s", 0) or 0),
            "timeout_s": task.get("timeout"),
            "created": int(task.get("created", 0) or 0),
            "next_run": int(task.get("next_run", 0) or 0),
            "next_local": clock.format_local_epoch(
                int(task.get("next_run", 0) or 0), self.config),
            "next_input": clock.format_local_input(
                int(task.get("next_run", 0) or 0), self.config),
            "last_run": int(task.get("last_run", 0) or 0),
            "last_local": (
                clock.format_local_epoch(int(task.get("last_run", 0) or 0),
                                         self.config)
                if int(task.get("last_run", 0) or 0) else ""),
            "last_status": task.get("last_status", ""),
            "last_error": _clip(task.get("last_error", ""), MAX_ERROR),
            "run_count": int(task.get("run_count", 0) or 0),
            "attempts": int(task.get("attempts", 0) or 0),
            "running": bool(task.get("running_since")),
            "preview": _one_line(seed, 120),
            "prompt_chars": len(task.get("prompt", "") or ""),
            "source_chars": len(task.get("source", "") or ""),
            "when_label": self.when_label(task),
        }
        if full:
            view["prompt"] = task.get("prompt", "")
            view["source"] = task.get("source", "")
        return view

    @staticmethod
    def when_label(task):
        when = task.get("when")
        if when == "once":
            at_text = str(task.get("at_text", "") or "")
            if at_text:
                return "once at %s" % at_text
            return "once after %ss" % int(task.get("delay_s", 0) or 0)
        if when == "interval":
            return "every %s" % _human_seconds(task.get("every_s", 0))
        return "daily %s" % str(task.get("at_text", "") or "")

    def format_task(self, task_id, full=False):
        view = self.view(task_id, full=full)
        if view is None:
            return None
        lines = [
            "id: %s" % view["id"],
            "name: %s" % view["name"],
            "type: %s" % view["type"],
            "enabled: %s" % ("true" if view["enabled"] else "false"),
            "schedule: %s" % view["when_label"],
            "next_run: %s" % view["next_local"],
            "last_run: %s" % (view["last_local"] or "<never>"),
            "last_status: %s" % (view["last_status"] or "<never>"),
            "run_count: %s" % view["run_count"],
            "running: %s" % ("true" if view["running"] else "false"),
        ]
        if view["last_error"]:
            lines.append("last_error: %s" % view["last_error"])
        if full:
            if view["type"] == "prompt":
                lines.append("prompt:\n%s" % view.get("prompt", "")[:2000])
            else:
                lines.append("source:\n%s" % view.get("source", "")[:2000])
                lines.append("timeout_s: %s" % (
                    "default" if view["timeout_s"] is None
                    else ("infinite" if view["timeout_s"] == 0
                          else view["timeout_s"])))
        else:
            lines.append("preview: %s" % view["preview"])
        return "\n".join(lines)

    def summary_lines(self):
        tasks = self.snapshot()
        if not tasks:
            return ["No scheduled tasks."]
        enabled = 0
        for task in tasks:
            if task.get("enabled"):
                enabled += 1
        lines = ["Scheduled tasks: %s (%s enabled, %s disabled)." % (
            len(tasks), enabled, len(tasks) - enabled)]
        for task in tasks:
            lines.append(
                "#%s [%s] %s type=%s schedule=%s next=%s last=%s runs=%s" % (
                    task.get("id", "?"),
                    "enabled" if task.get("enabled") else "disabled",
                    _clip(task.get("name", ""), 60),
                    task.get("type", "?"),
                    self.when_label(task),
                    clock.format_local_epoch(
                        int(task.get("next_run", 0) or 0), self.config),
                    task.get("last_status") or "never",
                    task.get("run_count", 0),
                ))
        return lines

    def clear(self):
        self._acquire()
        try:
            self.tasks = []
            self.seq = 0
            if self.store is not None:
                try:
                    self.store.clear()
                except Exception as exc:
                    self.last_error = str(exc)
        finally:
            self._release()
