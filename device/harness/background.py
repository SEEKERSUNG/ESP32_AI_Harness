"""Multiple detached code-execution jobs.

Each background job runs in its own MicroPython ``_thread``. The serial main
thread stays free for the REPL, foreground execution and the model handoff;
the web thread keeps serving requests. Jobs are cooperative: a stop request is
observed at the executor checkpoints, never by a hard kill.

This module owns the job table, scheduling, bounded views and persistence. The
Application supplies the executable callback and the Agent still owns result
commit/deduplication.
"""

import time

try:
    import _thread
except ImportError:
    _thread = None

from .executor import ExecutionResult

ACTIVE_PHASES = ("queued", "running")
FINISHED_PHASES = ("ready", "error")

EMPTY_VIEW = {
    "active": False,
    "phase": "none",
    "id": 0,
    "token": "",
    "auto": False,
    "recovered": False,
    "driver": "",
    "source_chars": 0,
    "timeout_s": None,
    "task": "",
    "note": "",
    "turns": 0,
    "created_ms": 0,
    "started_ms": 0,
    "finished_ms": 0,
    "elapsed_ms": 0,
    "stop_requested": False,
    "stop_reason": "",
    "error": "",
    "result": None,
}


def _ticks_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else int(
        time.monotonic() * 1000)


def _ticks_diff(now, then):
    if not then:
        return 0
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(now, then)
    return now - then


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


class BackgroundPool:
    def __init__(self, config, store, execute, notify=None,
                 thread_starter=None, clock=None):
        self.config = config
        self.store = store
        self.execute = execute
        self.notify = notify
        self.clock = clock or _ticks_ms
        self.thread_starter = thread_starter or self._start_thread
        self.lock = _thread.allocate_lock() if _thread is not None else None
        self.save_lock = _thread.allocate_lock() if _thread is not None else None
        self.jobs = []
        self.seq = 0
        self.max_jobs = _bounded_int(
            config.get("background_max_jobs", 4), 4, 1, 4)
        self.max_records = _bounded_int(
            config.get("background_max_records", 8), 8, 2, 16)
        self.stack_kb = _bounded_int(
            config.get("background_thread_stack_kb", 32), 32, 8, 64)

    def refresh_limits(self):
        self.max_jobs = _bounded_int(
            self.config.get("background_max_jobs", 4), 4, 1, 4)
        self.max_records = _bounded_int(
            self.config.get("background_max_records", 8), 8, 2, 16)
        self.stack_kb = _bounded_int(
            self.config.get("background_thread_stack_kb", 32), 32, 8, 64)
        self._launch_next()

    # -- locking ---------------------------------------------------------
    def _acquire(self):
        if self.lock is not None:
            self.lock.acquire()

    def _release(self):
        if self.lock is not None:
            self.lock.release()

    def _save_acquire(self):
        if self.save_lock is not None:
            self.save_lock.acquire()

    def _save_release(self):
        if self.save_lock is not None:
            self.save_lock.release()

    # -- job creation / scheduling --------------------------------------
    def create(self, source, timeout, auto=False, driver="web",
               task="", note="", turns=0, recovered=False):
        if not source:
            raise RuntimeError("No source for background execution")
        if timeout is None:
            # No annotation means the configured default deadline, not an
            # infinite job. Only an explicit 0/infinite disables it.
            timeout = self.config.get("code_timeout_s", 20)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = int(self.config.get("code_timeout_s", 20))
        if timeout < 0:
            timeout = 0
        now = self.clock()
        with_note = _clip(note or task, 200)
        job = None
        self._acquire()
        try:
            if len(self.jobs) >= self.max_records:
                self._prune_locked()
            if len(self.jobs) >= self.max_records:
                raise RuntimeError(
                    "Too many retained background jobs; continue or discard "
                    "some results first")
            self.seq += 1
            job = {
                "id": self.seq,
                "token": "%s@%s" % (self.seq, now),
                "phase": "queued",
                "source": source,
                "source_chars": len(source),
                "timeout": timeout,
                "auto": bool(auto),
                "recovered": bool(recovered),
                "driver": _clip(driver or "web", 16),
                "task": _clip(task, 200),
                "note": with_note,
                "turns": max(0, int(turns or 0)),
                "created_ms": now,
                "started_ms": 0,
                "finished_ms": 0,
                "stop_requested": False,
                "stop_reason": "",
                "result": None,
                "error": "",
            }
            self.jobs.append(job)
        finally:
            self._release()
        self.save()
        if self.running_count() < self.max_jobs and job["phase"] == "queued":
            try:
                self._launch(job)
            except BaseException:
                self._remove(job)
                self.save()
                raise
        return job

    def _prune_locked(self):
        # Error records are the least useful; never silently drop a ready
        # result that the user can still continue.
        for job in list(self.jobs):
            if job.get("phase") == "error":
                self.jobs.remove(job)
                if len(self.jobs) < self.max_records:
                    return

    def _remove(self, job):
        self._acquire()
        try:
            if job in self.jobs:
                self.jobs.remove(job)
        finally:
            self._release()

    def _launch(self, job):
        self._acquire()
        try:
            if job.get("phase") != "queued":
                return False
            if self._running_count_locked() >= self.max_jobs:
                return False
            job["phase"] = "running"
            job["started_ms"] = self.clock()
            job["stop_requested"] = False
        finally:
            self._release()
        self.save()
        try:
            self.thread_starter(lambda: self._work(job))
        except BaseException:
            self._acquire()
            try:
                job["phase"] = "queued"
                job["started_ms"] = 0
            finally:
                self._release()
            raise
        return True

    def _work(self, job):
        result = None
        try:
            result = self.execute(job)
            if result is None:
                result = ExecutionResult(
                    False, "", "Background execution returned no result", 0)
        except BaseException as exc:
            result = ExecutionResult(
                False, "", "%s: %s" % (type(exc).__name__, exc), 0)
        self._finish(job, result)

    def _finish(self, job, result):
        self._acquire()
        try:
            if job not in self.jobs:
                return
            job["result"] = result
            job["phase"] = "ready"
            job["finished_ms"] = self.clock()
            job["stop_requested"] = False
            job["source"] = None
        finally:
            self._release()
        self.save()
        self._notify_finished(job, result)
        self._launch_next()

    def _notify_finished(self, job, result):
        if self.notify is None:
            return
        if getattr(result, "cancelled", False):
            state = "stopped"
        elif getattr(result, "timed_out", False):
            state = "timed out"
        elif getattr(result, "ok", False):
            state = "finished"
        else:
            state = "failed"
        text = (
            "Background job #%s %s (%sms); use /background or the web console "
            "to review it." % (
                job.get("id", "?"), state,
                getattr(result, "elapsed_ms", 0)))
        try:
            self.notify(text)
        except Exception:
            pass

    def _launch_next(self):
        while True:
            job = None
            self._acquire()
            try:
                if self._running_count_locked() >= self.max_jobs:
                    return
                for candidate in self.jobs:
                    if candidate.get("phase") == "queued":
                        job = candidate
                        break
            finally:
                self._release()
            if job is None:
                return
            try:
                started = self._launch(job)
                if not started:
                    return
            except BaseException as exc:
                self._acquire()
                try:
                    job["phase"] = "error"
                    job["error"] = _clip(
                        "Could not start background thread: %s" % exc, 1024)
                    job["finished_ms"] = self.clock()
                    job["source"] = None
                finally:
                    self._release()
                self.save()
                self._notify_finished(
                    job, ExecutionResult(False, "", job["error"], 0))

    def _candidate_stacks(self):
        sizes = []
        for value in (self.stack_kb, 24, 16, 12, 8):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value >= 4 and value not in sizes:
                sizes.append(value)
        return sizes

    def _start_thread(self, func):
        if _thread is None:
            raise RuntimeError("_thread is unavailable")
        last_error = None
        for stack_kb in self._candidate_stacks():
            try:
                if hasattr(_thread, "stack_size"):
                    _thread.stack_size(stack_kb * 1024)
            except Exception:
                pass
            try:
                _thread.start_new_thread(func, ())
                return
            except Exception as exc:
                last_error = exc
                try:
                    if hasattr(time, "sleep_ms"):
                        time.sleep_ms(50)
                    else:
                        time.sleep(0.05)
                except Exception:
                    pass
        raise RuntimeError(
            "can't create thread after retries; lower "
            "background_thread_stack_kb or background_max_jobs (%s)"
            % last_error)

    # -- queries ---------------------------------------------------------
    def _active_count_locked(self):
        count = 0
        for job in self.jobs:
            if job.get("phase") in ACTIVE_PHASES:
                count += 1
        return count

    def _running_count_locked(self):
        count = 0
        for job in self.jobs:
            if job.get("phase") == "running":
                count += 1
        return count

    def active_count(self):
        self._acquire()
        try:
            return self._active_count_locked()
        finally:
            self._release()

    def running_count(self):
        self._acquire()
        try:
            return self._running_count_locked()
        finally:
            self._release()

    def has_active(self):
        return self.active_count() > 0

    def snapshot(self):
        self._acquire()
        try:
            return list(self.jobs)
        finally:
            self._release()

    def get(self, job_id=None):
        target = self._job_id(job_id)
        if job_id is not None and target is None:
            return None
        self._acquire()
        try:
            for job in self.jobs:
                if target is None or job.get("id") == target:
                    return job
            return None
        finally:
            self._release()

    @staticmethod
    def _job_id(job_id):
        if job_id is None or job_id == "":
            return None
        try:
            return int(job_id)
        except (TypeError, ValueError):
            return None

    def ready_jobs(self):
        self._acquire()
        try:
            return [job for job in self.jobs if job.get("phase") == "ready"]
        finally:
            self._release()

    def error_jobs(self):
        self._acquire()
        try:
            return [job for job in self.jobs if job.get("phase") == "error"]
        finally:
            self._release()

    def primary(self):
        """Return the active job, else the oldest ready, else the oldest error."""
        self._acquire()
        try:
            for phase_group in (ACTIVE_PHASES, ("ready",), ("error",)):
                for job in self.jobs:
                    if job.get("phase") in phase_group:
                        return job
            return None
        finally:
            self._release()

    # -- stop / take / discard ------------------------------------------
    def stop(self, job_id=None, reason=""):
        # Without an id, stop every active job (the web/API and AI tool have no
        # separate single-job default). With an id, touch only that job.
        target = self._job_id(job_id)
        if job_id is not None and target is None:
            return 0
        stopped = []
        cancel_queued = []
        now = self.clock()
        self._acquire()
        try:
            for job in self.jobs:
                if job.get("phase") not in ACTIVE_PHASES:
                    continue
                if target is not None and job.get("id") != target:
                    continue
                if job.get("phase") == "queued":
                    # Mark it finished under the same lock so _launch_next
                    # cannot start it between selection and cancellation.
                    result = ExecutionResult(
                        False, "",
                        "ExecutionCancelled: stop requested before start",
                        0, False, False, True)
                    job["result"] = result
                    job["phase"] = "ready"
                    job["finished_ms"] = now
                    job["stop_requested"] = False
                    job["source"] = None
                    cancel_queued.append(job)
                else:
                    job["stop_requested"] = True
                    job["stop_reason"] = _clip(reason, 120)
                stopped.append(job)
        finally:
            self._release()
        for job in cancel_queued:
            self._notify_finished(job, job["result"])
        if stopped:
            self.save()
        if cancel_queued:
            self._launch_next()
        return len(stopped)

    def take(self, job_id=None, commit=None):
        target = self._job_id(job_id)
        if job_id is not None and target is None:
            raise RuntimeError("Invalid background job id")
        job = None
        self._acquire()
        try:
            for candidate in self.jobs:
                if candidate.get("phase") != "ready":
                    continue
                if target is not None and candidate.get("id") != target:
                    continue
                job = candidate
                break
            if job is None:
                if target is not None:
                    other = None
                    for candidate in self.jobs:
                        if candidate.get("id") == target:
                            other = candidate
                            break
                    if other is not None:
                        raise RuntimeError(
                            "Background job #%s is not ready (phase=%s)"
                            % (target, other.get("phase", "?")))
                raise RuntimeError("No completed background execution")
            # Commit while the job is still in the table and the result file
            # still exists. A crash after this point is deduplicated by the
            # persisted (job=token) marker on the next boot.
            if commit is not None:
                commit(job)
            if job in self.jobs:
                self.jobs.remove(job)
        finally:
            self._release()
        self.save()
        return job

    def discard(self, job_id=None, all_finished=False):
        target = self._job_id(job_id)
        if job_id is not None and target is None:
            raise RuntimeError("Invalid background job id")
        removed = []
        self._acquire()
        try:
            finished = [
                job for job in self.jobs
                if job.get("phase") in FINISHED_PHASES]
            if target is not None:
                for job in finished:
                    if job.get("id") == target:
                        removed.append(job)
                        break
                if not removed:
                    for job in self.jobs:
                        if job.get("id") == target:
                            raise RuntimeError(
                                "Background job #%s is still %s"
                                % (target, job.get("phase", "?")))
                    raise RuntimeError("No background job #%s" % target)
            elif all_finished:
                removed.extend(finished)
            elif len(finished) == 1:
                removed.append(finished[0])
            elif len(finished) > 1:
                raise RuntimeError(
                    "Multiple finished background jobs; specify an id")
            else:
                return []
            for job in removed:
                if job in self.jobs:
                    self.jobs.remove(job)
        finally:
            self._release()
        if removed:
            self.save()
        return removed

    # -- persistence -----------------------------------------------------
    def save(self):
        self._save_acquire()
        try:
            self.store.save_jobs(self.snapshot())
        except Exception as exc:
            try:
                self.store.last_error = str(exc)
            except Exception:
                pass
        finally:
            self._save_release()

    def restore(self, committed=None):
        jobs = self.store.load_jobs()
        had_jobs = bool(jobs)
        restored = []
        highest = 0
        for job in jobs:
            token = job.get("token") or job.get("id")
            if committed is not None and committed(token):
                continue
            result = job.get("result")
            if isinstance(result, dict):
                job["result"] = ExecutionResult(
                    bool(result.get("ok")),
                    result.get("output", ""),
                    result.get("error", ""),
                    int(result.get("elapsed_ms", 0) or 0),
                    False,
                    bool(result.get("timed_out")),
                    bool(result.get("cancelled")),
                )
            else:
                job["result"] = None
            job["source"] = None
            job["stop_requested"] = False
            try:
                job_id = int(job.get("id", 0) or 0)
            except (TypeError, ValueError):
                job_id = 0
            if job_id > highest:
                highest = job_id
            restored.append(job)
        if len(restored) > self.max_records:
            # Never silently drop a ready result: after a config change lowers
            # max_records, keep every ready record (bounded by the store's own
            # MAX_JOBS) and trim only the less useful non-ready records.
            ready = [job for job in restored if job.get("phase") == "ready"]
            remaining = self.max_records - len(ready)
            if remaining <= 0:
                restored = ready
            else:
                keep = {}
                for job in [job for job in restored
                            if job.get("phase") != "ready"][-remaining:]:
                    keep[id(job)] = True
                restored = [
                    job for job in restored
                    if job.get("phase") == "ready" or id(job) in keep
                ]
        self._acquire()
        try:
            self.jobs = restored
            if highest > self.seq:
                self.seq = highest
        finally:
            self._release()
        if restored:
            self.save()
        elif had_jobs:
            # All records were already committed before the reboot; do not
            # leave stale entries that would be re-checked every boot.
            try:
                self.store.clear()
            except Exception:
                pass
        return restored

    def clear(self):
        self._acquire()
        try:
            self.jobs = []
        finally:
            self._release()
        try:
            self.store.clear()
        except Exception:
            pass

    # -- views -----------------------------------------------------------
    def view(self, job_id=None, result_limit=4096):
        if job_id is None:
            job = self.primary()
        else:
            job = self.get(job_id)
        if job is None:
            return dict(EMPTY_VIEW)
        return self._view(job, result_limit)

    def views(self, result_limit=4096):
        jobs = self.snapshot()
        return [self._view(job, result_limit) for job in jobs]

    def _view(self, job, result_limit):
        phase = job.get("phase", "queued")
        result = job.get("result")
        elapsed_ms = 0
        if result is not None:
            elapsed_ms = int(getattr(result, "elapsed_ms", 0) or 0)
        elif phase == "running" and job.get("started_ms"):
            elapsed_ms = _ticks_diff(self.clock(), job["started_ms"])
        elif job.get("started_ms") and job.get("finished_ms"):
            elapsed_ms = _ticks_diff(
                job["finished_ms"], job["started_ms"])
        view = {
            "active": phase in ACTIVE_PHASES,
            "phase": phase,
            "id": job.get("id", 0),
            "token": job.get("token", ""),
            "auto": bool(job.get("auto")),
            "recovered": bool(job.get("recovered")),
            "driver": job.get("driver", ""),
            "source_chars": int(job.get("source_chars", 0) or 0),
            "timeout_s": job.get("timeout"),
            "task": _clip(job.get("task", ""), 200),
            "note": _clip(job.get("note", ""), 200),
            "turns": int(job.get("turns", 0) or 0),
            "created_ms": int(job.get("created_ms", 0) or 0),
            "started_ms": int(job.get("started_ms", 0) or 0),
            "finished_ms": int(job.get("finished_ms", 0) or 0),
            "elapsed_ms": max(0, elapsed_ms),
            "stop_requested": bool(job.get("stop_requested")),
            "stop_reason": _clip(job.get("stop_reason", ""), 120),
            "error": _clip(job.get("error", ""), 1024),
            "result": None,
        }
        if result is not None:
            output = getattr(result, "output", "") or ""
            error = getattr(result, "error", "") or ""
            limit = max(0, int(result_limit or 0))
            if limit and len(output) > limit:
                output = output[-limit:]
            view["result"] = {
                "ok": bool(getattr(result, "ok", False)),
                "timed_out": bool(getattr(result, "timed_out", False)),
                "cancelled": bool(getattr(result, "cancelled", False)),
                "output": output,
                "error": _clip(error, 1024),
                "elapsed_ms": int(
                    getattr(result, "elapsed_ms", 0) or 0),
            }
        return view

    def note(self, job_id, text):
        target = self._job_id(job_id)
        if target is None:
            raise RuntimeError("Background job id is required")
        job = self.get(target)
        if job is None:
            raise RuntimeError("No background job #%s" % target)
        self._acquire()
        try:
            job["note"] = _clip(text, 200)
        finally:
            self._release()
        self.save()
        return job

    @staticmethod
    def timeout_label(value):
        if value == 0:
            return "infinite"
        if value is None:
            return "?"
        return "%ss" % value

    def summary_lines(self, include_output=False):
        jobs = self.snapshot()
        if not jobs:
            return ["No background jobs."]
        active = 0
        ready = 0
        errors = 0
        for job in jobs:
            phase = job.get("phase")
            if phase in ACTIVE_PHASES:
                active += 1
            elif phase == "ready":
                ready += 1
            elif phase == "error":
                errors += 1
        lines = ["Background jobs: %s active, %s ready, %s error." % (
            active, ready, errors)]
        now = self.clock()
        for job in jobs:
            phase = job.get("phase", "?")
            elapsed = 0
            result = job.get("result")
            if result is not None:
                elapsed = int(getattr(result, "elapsed_ms", 0) or 0)
            elif phase == "running" and job.get("started_ms"):
                elapsed = _ticks_diff(now, job["started_ms"])
            note = job.get("note") or job.get("task") or "<no note>"
            line = "#%s %s elapsed=%sms timeout=%s note=%s" % (
                job.get("id", "?"), phase, max(0, elapsed),
                self.timeout_label(job.get("timeout")), _clip(note, 120))
            if phase in ACTIVE_PHASES and job.get("stop_requested"):
                line += " stop=requested"
            if result is not None:
                if getattr(result, "cancelled", False):
                    state = "cancelled"
                elif getattr(result, "timed_out", False):
                    state = "timeout"
                elif getattr(result, "ok", False):
                    state = "ok"
                else:
                    state = "error"
                line += " result=%s" % state
            elif job.get("error"):
                line += " error=%s" % _clip(job.get("error"), 160)
            lines.append(line)
            if include_output and result is not None:
                output = getattr(result, "output", "") or ""
                if output:
                    lines.append("  output_tail: %s" % output[-800:])
                error = getattr(result, "error", "") or ""
                if error:
                    lines.append("  error: %s" % _clip(error, 400))
        return lines
