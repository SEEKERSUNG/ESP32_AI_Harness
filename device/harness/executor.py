import gc
import io
import os
import sys
import time

try:
    import _thread
except ImportError:
    _thread = None

try:
    import builtins
except ImportError:
    builtins = None


class ExecutionTimeout(BaseException):
    """Raised by cooperative deadline hooks; BaseException is deliberate.

    Generated code often wraps its body in ``except Exception``. A timeout is
    a harness control signal, not an application error, so it must not be
    swallowed by that pattern.
    """


class ExecutionCancelled(BaseException):
    """Raised when a running background program is asked to stop.

    Like ExecutionTimeout this is a control signal, not an application error.
    It only works at cooperative checkpoints.
    """


class BufferWriter(io.IOBase):
    def __init__(self, limit):
        self.limit = limit
        self.parts = []
        self.size = 0
        self.truncated = False

    def write(self, text):
        if isinstance(text, (bytes, bytearray)):
            raw = bytes(text)
            consumed = len(raw)
            text = raw.decode()
        else:
            text = str(text)
            # os.dupterm() normally passes bytes; keep the character count
            # for a direct str write.
            consumed = len(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        remaining = self.limit - self.size
        if remaining > 0:
            part = text[:remaining]
            self.parts.append(part)
            self.size += len(part)
        if len(text) > remaining:
            self.truncated = True
        # IOBase.write must report the number of input bytes consumed. Using
        # the decoded character count truncates multi-byte UTF-8 output.
        return consumed

    def readinto(self, buffer):
        return None

    def ioctl(self, request, argument=0):
        if request in (1, 3, 4):
            return 0
        return -1

    def flush(self):
        pass

    def getvalue(self):
        value = "".join(self.parts)
        if self.truncated:
            value += "\n...[output truncated]"
        return value


class ExecutionResult:
    def __init__(self, ok, output, error, elapsed_ms, live=False,
                 timed_out=False, cancelled=False):
        self.ok = ok
        self.output = output
        self.error = error
        self.elapsed_ms = elapsed_ms
        self.live = live
        self.timed_out = timed_out
        self.cancelled = cancelled

    def observation(self, limit=0):
        if self.cancelled:
            status = "cancelled"
        elif self.timed_out:
            status = "timeout"
        elif self.ok:
            status = "ok"
        else:
            status = "error"
        text = "status=%s\nelapsed_ms=%s\n" % (status, self.elapsed_ms)
        if self.error:
            text += "error=%s\n" % self.error
        if self.cancelled:
            text += (
                "note=The harness stopped this program because a stop was "
                "requested. The device did not reboot; the program did not finish. "
                "Stop is cooperative: blocking C/SPI calls and code that never "
                "reaches a harness hook cannot be interrupted this way.\n"
            )
        elif self.timed_out:
            text += (
                "note=The harness stopped this program at its execution deadline. "
                "The device did not reboot; you may return a corrected, shorter "
                "program or a final explanation. To request more time, put "
                "timeout=SECONDS on the opening fence line (for example the opening "
                "line ```python timeout=45); a bare trailing timeout= line after the "
                "closing fence is only a compatibility fallback. Other trailing text "
                "is ignored. The timeout stops only this exec; Python threads started "
                "earlier may still be running.\n"
            )
        text += "output:\n%s" % (self.output or "<empty>")
        if limit and len(text) > limit:
            marker = "\n...[observation truncated]"
            text = text[:max(0, limit - len(marker))] + marker
        return text


class DeadlineRange:
    """A range-compatible iterator that checks the harness deadline.

    Replacing range covers the most common long-running generated loop without
    rewriting source. while loops remain cooperative via __harness_check().
    """

    def __init__(self, check, *args):
        self._check = check
        self._range = range(*args)

    def __iter__(self):
        index = 0
        for value in self._range:
            if (index & 255) == 0:
                self._check()
            index += 1
            yield value

    def __len__(self):
        return len(self._range)

    def __getitem__(self, index):
        return self._range[index]

    def __bool__(self):
        return bool(self._range)

    def __contains__(self, value):
        return value in self._range

    def __repr__(self):
        return "range(%s)" % self._range


def _is_identifier_char(char):
    # MicroPython 1.29 does not implement str.isalnum().
    return (
        "a" <= char <= "z"
        or "A" <= char <= "Z"
        or "0" <= char <= "9"
        or char == "_"
    )


def _skip_spaces(source, index):
    length = len(source)
    while index < length and source[index] in " \t":
        index += 1
    return index


def _is_true_while_condition(source, index):
    length = len(source)
    if source.startswith("True", index):
        cursor = _skip_spaces(source, index + 4)
        return cursor < length and source[cursor] == ":"
    if source.startswith("1", index):
        cursor = _skip_spaces(source, index + 1)
        return cursor < length and source[cursor] == ":"
    if index < length and source[index] == "(":
        cursor = _skip_spaces(source, index + 1)
        if source.startswith("True", cursor):
            cursor = _skip_spaces(source, cursor + 4)
        elif source.startswith("1", cursor):
            cursor = _skip_spaces(source, cursor + 1)
        else:
            return False
        if cursor < length and source[cursor] == ")":
            cursor = _skip_spaces(source, cursor + 1)
            return cursor < length and source[cursor] == ":"
    return False


def instrument_source(source):
    """Rewrite range() calls to the deadline-aware wrapper.

    MicroPython compiles ``for i in range(...)`` against the builtin range even
    when a globals dict shadows it, so a globals override is not enough. The
    scanner skips strings and comments and leaves attribute/method names alone.
    """
    out = []
    index = 0
    length = len(source)
    quote = None
    triple = False
    while index < length:
        char = source[index]
        if quote is not None:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(source[index + 1])
                index += 2
                continue
            if triple:
                if source.startswith(quote * 3, index):
                    out.append(source[index + 1:index + 3])
                    index += 3
                    quote = None
                    triple = False
                    continue
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            if source.startswith(char * 3, index):
                quote = char
                triple = True
                out.append(source[index:index + 3])
                index += 3
                continue
            quote = char
            triple = False
            out.append(char)
            index += 1
            continue
        if char == "#":
            end = source.find("\n", index)
            if end < 0:
                out.append(source[index:])
                break
            out.append(source[index:end])
            index = end
            continue
        if source.startswith("range", index):
            previous = source[index - 1] if index > 0 else ""
            if not (_is_identifier_char(previous) or previous == "."):
                cursor = index + 5
                while cursor < length and source[cursor] in " \t":
                    cursor += 1
                if cursor < length and source[cursor] == "(":
                    out.append("__harness_range")
                    index += 5
                    continue
        if source.startswith("while", index):
            previous = source[index - 1] if index > 0 else ""
            if not _is_identifier_char(previous):
                cursor = index + 5
                if cursor < length and source[cursor] in " \t(":
                    cursor = _skip_spaces(source, cursor)
                    if _is_true_while_condition(source, cursor):
                        out.append("while __harness_check() or ")
                        index = cursor
                        continue
        out.append(char)
        index += 1
    return "".join(out)


class RoutingTime:
    """Module-like time proxy that routes sleeps to the calling thread.

    MicroPython's built-in modules reject setattr, so the executor temporarily
    replaces sys.modules['time']/['utime'] instead of patching module members.
    Several executions may run at once (background jobs on worker threads plus
    foreground code on the main thread), so the proxy looks up the active
    execution context by thread id instead of capturing one closure.
    """

    def __init__(self, runtime):
        self._runtime = runtime

    def __getattr__(self, name):
        return getattr(self._runtime.real_time, name)

    def sleep(self, seconds):
        return self._runtime.sleep(seconds)

    def sleep_ms(self, milliseconds):
        return self._runtime.sleep_ms(milliseconds)

    def sleep_us(self, microseconds):
        return self._runtime.sleep_us(microseconds)


class ExecutionRuntime:
    """Process-wide cooperative-execution registry.

    One time proxy and one ``__harness_check`` builtin are installed while any
    execution is active. Each thread has a stack of contexts so a nested
    Executor (for example a workspace script started by generated code) can
    temporarily replace the outer deadline and restore it afterwards.
    """

    def __init__(self):
        self.real_time = time
        self.lock = _thread.allocate_lock() if _thread is not None else None
        self.stacks = {}
        self.installed = 0
        self.proxy = RoutingTime(self)
        self.saved_modules = []
        self.builtin_dict = None
        self.builtin_previous = None
        self.builtin_had = False
        self.builtin_patched = False
        self.print_previous = None
        self.print_patched = False

    @staticmethod
    def _ident():
        return _thread.get_ident() if _thread is not None else 0

    def _acquire(self):
        if self.lock is not None:
            self.lock.acquire()

    def _release(self):
        if self.lock is not None:
            self.lock.release()

    def register(self, context):
        self._acquire()
        try:
            if self.installed == 0:
                self._install()
            thread_id = self._ident()
            stack = self.stacks.get(thread_id)
            if stack is None:
                stack = []
                self.stacks[thread_id] = stack
            stack.append(context)
            self.installed += 1
        finally:
            self._release()

    def unregister(self, context=None):
        self._acquire()
        try:
            thread_id = self._ident()
            stack = self.stacks.get(thread_id)
            if not stack:
                return
            if context is None:
                stack.pop()
            else:
                removed = False
                for index in range(len(stack) - 1, -1, -1):
                    if stack[index] is context:
                        stack.pop(index)
                        removed = True
                        break
                if not removed:
                    return
            if not stack:
                self.stacks.pop(thread_id, None)
            if self.installed > 0:
                self.installed -= 1
            if self.installed == 0:
                self._restore()
        finally:
            self._release()

    def current(self):
        stack = self.stacks.get(self._ident())
        if not stack:
            return None
        return stack[-1]

    def check(self):
        context = self.current()
        if context is not None:
            context["check"]()

    def sleep(self, seconds):
        context = self.current()
        if context is None:
            return self.real_time.sleep(seconds)
        return context["sleep"](seconds)

    def sleep_ms(self, milliseconds):
        context = self.current()
        if context is None:
            if hasattr(self.real_time, "sleep_ms"):
                return self.real_time.sleep_ms(milliseconds)
            return self.real_time.sleep(milliseconds / 1000.0)
        return context["sleep_ms"](milliseconds)

    def sleep_us(self, microseconds):
        context = self.current()
        if context is None:
            if hasattr(self.real_time, "sleep_us"):
                return self.real_time.sleep_us(microseconds)
            return self.real_time.sleep(microseconds / 1000000.0)
        return context["sleep_us"](microseconds)

    def print(self, *args, **kwargs):
        """Route print() to an isolated worker's writer when one is active."""
        context = self.current()
        if context is not None and context.get("print") is not None:
            return context["print"](*args, **kwargs)
        if self.print_previous is not None:
            return self.print_previous(*args, **kwargs)
        return None

    def _install(self):
        self.saved_modules = []
        for key in ("time", "utime"):
            try:
                original = sys.modules.get(key)
            except Exception:
                original = None
            self.saved_modules.append((key, original))
            try:
                sys.modules[key] = self.proxy
            except Exception:
                pass
        self.builtin_dict = None
        self.builtin_previous = None
        self.builtin_had = False
        self.builtin_patched = False
        self.print_previous = None
        self.print_patched = False
        if builtins is None:
            return
        try:
            candidate = getattr(builtins, "__dict__", None)
            if isinstance(candidate, dict):
                self.builtin_dict = candidate
        except Exception:
            self.builtin_dict = None
        if self.builtin_dict is not None:
            self.builtin_had = "__harness_check" in self.builtin_dict
            self.builtin_previous = self.builtin_dict.get("__harness_check")
        else:
            try:
                self.builtin_previous = builtins.__harness_check
                self.builtin_had = True
            except Exception:
                pass
        if self.builtin_dict is not None:
            self.print_previous = self.builtin_dict.get("print")
        else:
            try:
                self.print_previous = builtins.print
            except Exception:
                self.print_previous = None
        try:
            if self.builtin_dict is not None:
                # CPython exposes the live module dict here; assigning the
                # module attribute alone can be invisible to nested exec.
                self.builtin_dict["__harness_check"] = self.check
                self.builtin_dict["print"] = self.print
            builtins.__harness_check = self.check
            builtins.print = self.print
            self.builtin_patched = True
            self.print_patched = True
        except Exception:
            try:
                builtins.__harness_check = self.check
                builtins.print = self.print
                self.builtin_patched = True
                self.print_patched = True
            except Exception:
                self.builtin_patched = False
                self.print_patched = False

    def _restore(self):
        for key, original in self.saved_modules:
            try:
                if original is None:
                    try:
                        del sys.modules[key]
                    except KeyError:
                        pass
                else:
                    sys.modules[key] = original
            except Exception:
                pass
        self.saved_modules = []
        if self.builtin_patched:
            if self.builtin_dict is not None:
                try:
                    if self.builtin_had:
                        self.builtin_dict["__harness_check"] = self.builtin_previous
                    else:
                        del self.builtin_dict["__harness_check"]
                except Exception:
                    pass
            try:
                if self.builtin_had:
                    builtins.__harness_check = self.builtin_previous
                else:
                    del builtins.__harness_check
            except Exception:
                pass
        if self.print_patched:
            if self.builtin_dict is not None:
                try:
                    if self.print_previous is not None:
                        self.builtin_dict["print"] = self.print_previous
                except Exception:
                    pass
            try:
                if self.print_previous is not None:
                    builtins.print = self.print_previous
            except Exception:
                pass
        self.builtin_patched = False
        self.print_patched = False


_RUNTIME = None


def execution_runtime():
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = ExecutionRuntime()
    return _RUNTIME


def _writer_print(writer):
    """print() replacement for isolated worker executions.

    Background jobs capture their output through this injected builtin instead
    of replacing sys.stdout/os.dupterm, which are process-wide and would mix
    output from concurrent jobs and foreground code.
    """
    def harness_print(*args, **kwargs):
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        if sep is None:
            sep = " "
        if end is None:
            end = "\n"
        parts = []
        for value in args:
            parts.append(str(value))
        text = sep.join(parts) + end
        target = kwargs.get("file")
        try:
            if target is not None:
                target.write(text)
            else:
                writer.write(text)
        except Exception:
            try:
                writer.write(text)
            except Exception:
                pass
    return harness_print


class Executor:
    def __init__(self, device=None, output_limit=4096, recovery=None,
                 timeout_s=20, max_timeout_s=300, feed=None,
                 cancel_check=None, isolated_output=False):
        self.device = device
        self.output_limit = output_limit
        self.recovery = recovery
        self.timeout_s = timeout_s
        self.max_timeout_s = max_timeout_s
        self.feed = feed
        self.cancel_check = cancel_check
        self.main_runner = None
        # Worker-thread executions must not touch os.dupterm/sys.stdout: those
        # are process-wide, and a background job would steal the foreground
        # terminal. They capture print() through a per-job writer instead.
        self.isolated_output = isolated_output

    def execute(self, source, filename="<agent>", timeout_s=None):
        if (self.main_runner is not None
                and not self.main_runner.on_main_thread()):
            return self.main_runner.run_on_main(
                lambda: self._execute_local(source, filename, timeout_s)
            )
        return self._execute_local(source, filename, timeout_s)

    def _execute_local(self, source, filename="<agent>", timeout_s=None):
        writer = BufferWriter(self.output_limit)
        isolated = bool(self.isolated_output)
        duplicate_terminal = (not isolated) and hasattr(os, "dupterm")
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        previous_terminal = None
        started = self._ticks()
        timeout = self._resolve_timeout(timeout_s)
        deadline = None
        if timeout > 0:
            deadline = self._ticks_add(started, timeout * 1000)
        error = ""
        ok = False
        timed_out = False
        cancelled = False
        marked = False
        runtime = execution_runtime()
        registered = False

        def check():
            if self.cancel_check is not None:
                try:
                    stop = self.cancel_check()
                except Exception:
                    stop = False
                if stop:
                    raise ExecutionCancelled(
                        "Background execution stop requested")
            if deadline is not None and self._elapsed(deadline) >= 0:
                raise ExecutionTimeout(
                    "Execution exceeded %s second(s)" % timeout
                )
            self._feed()

        # Always use the real time module captured by the runtime. During
        # concurrent executions sys.modules['time'] is the routing proxy, and
        # sleeping through it here would recurse.
        original_sleep = getattr(runtime.real_time, "sleep", None)
        original_sleep_ms = getattr(runtime.real_time, "sleep_ms", None)
        original_sleep_us = getattr(runtime.real_time, "sleep_us", None)

        def sleep_ms(milliseconds):
            remaining_ms = int(milliseconds)
            while remaining_ms > 0:
                check()
                if deadline is None:
                    # Even without a deadline, chunk long sleeps so cancel
                    # requests and watchdog feeds are observed periodically.
                    step = remaining_ms if remaining_ms < 1000 else 1000
                else:
                    available = -self._elapsed(deadline)
                    if available <= 0:
                        raise ExecutionTimeout(
                            "Execution exceeded %s second(s)" % timeout
                        )
                    step = remaining_ms if remaining_ms < available else available
                    if step > 1000:
                        step = 1000
                if original_sleep_ms is not None:
                    original_sleep_ms(step)
                elif original_sleep is not None:
                    original_sleep(step / 1000.0)
                else:
                    runtime.real_time.sleep(step / 1000.0)
                remaining_ms -= step

        def sleep(seconds):
            check()
            if deadline is None and self.cancel_check is None:
                if original_sleep is not None:
                    return original_sleep(seconds)
                return None
            return sleep_ms(int(float(seconds) * 1000))

        def sleep_us(microseconds):
            check()
            if deadline is None and self.cancel_check is None:
                if original_sleep_us is not None:
                    return original_sleep_us(microseconds)
                if original_sleep is not None:
                    return original_sleep(float(microseconds) / 1000000.0)
                return None
            return sleep_ms(max(1, int(microseconds) // 1000))

        instrumented = deadline is not None or self.cancel_check is not None
        if instrumented:
            context = {
                "check": check,
                "sleep": sleep,
                "sleep_ms": sleep_ms,
                "sleep_us": sleep_us,
                "print": _writer_print(writer) if isolated else None,
            }
            try:
                runtime.register(context)
                registered = True
            except Exception:
                registered = False

        if duplicate_terminal:
            previous_terminal = os.dupterm(writer)
        elif not isolated:
            sys.stdout = writer
            sys.stderr = writer
        try:
            try:
                # Instrument when there is a deadline or a cooperative cancel
                # source. Infinite background jobs (timeout=0) still need
                # while/range checkpoints for stop requests and watchdog feeds.
                compiled_source = (
                    instrument_source(source) if instrumented else source)
                code = compile(compiled_source, filename, "exec")
                if self.recovery is not None:
                    self.recovery.begin(source)
                    marked = True
                gc.collect()
                scope = {
                    "__name__": "__agent__",
                    "device": self.device,
                    "__harness_check": check,
                }
                if isolated:
                    scope["print"] = _writer_print(writer)
                if instrumented:
                    scope["__harness_range"] = (
                        lambda *args: DeadlineRange(check, *args)
                    )
                exec(code, scope)
                ok = True
            except ExecutionTimeout as exc:
                timed_out = True
                error = "ExecutionTimeout: %s" % exc
            except ExecutionCancelled as exc:
                cancelled = True
                error = "ExecutionCancelled: %s" % exc
            except BaseException as exc:
                error = "%s: %s" % (type(exc).__name__, exc)
                error_limit = min(512, self.output_limit)
                if len(error) > error_limit:
                    error = error[:max(0, error_limit - 22)] + "\n...[error truncated]"
                self._print_exception(exc, writer)
        finally:
            if marked and self.recovery is not None:
                self.recovery.end()
            if duplicate_terminal:
                os.dupterm(previous_terminal)
            elif not isolated:
                sys.stdout = original_stdout
                sys.stderr = original_stderr
            if registered:
                try:
                    runtime.unregister(context)
                except Exception:
                    pass
            gc.collect()
        return ExecutionResult(
            ok,
            writer.getvalue(),
            error,
            self._elapsed(started),
            duplicate_terminal,
            timed_out,
            cancelled,
        )

    def _feed(self):
        if self.feed is None:
            return
        try:
            self.feed()
        except Exception:
            pass

    def _resolve_timeout(self, timeout_s):
        raw = self.timeout_s if timeout_s is None else timeout_s
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            try:
                numeric = float(self.timeout_s or 0)
            except (TypeError, ValueError):
                numeric = 0.0
        if numeric <= 0:
            return 0
        try:
            value = int(numeric)
        except (OverflowError, ValueError):
            value = 0
        # A positive fractional timeout must not silently become 0 (infinite).
        if numeric > value:
            value += 1
        if value < 1:
            value = 1
        try:
            maximum = int(self.max_timeout_s or 0)
        except (TypeError, ValueError):
            maximum = 0
        if maximum > 0 and value > maximum:
            value = maximum
        return value

    @staticmethod
    def _ticks_add(value, delta):
        if hasattr(time, "ticks_add"):
            return time.ticks_add(value, delta)
        return value + delta

    @staticmethod
    def _print_exception(exc, writer):
        if hasattr(sys, "print_exception"):
            sys.print_exception(exc, writer)
        else:
            import traceback
            traceback.print_exc(file=writer)

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
