"""Small HTTP/1.1 server and request/response helpers for the web console."""

import json
import select
import socket
import sys
import time

try:
    import _thread
except ImportError:
    _thread = None


STATUS_TEXT = {
    200: "OK", 201: "Created", 204: "No Content", 302: "Found",
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 408: "Request Timeout", 409: "Conflict",
    411: "Length Required", 413: "Payload Too Large",
    415: "Unsupported Media Type", 421: "Misdirected Request",
    429: "Too Many Requests", 431: "Request Header Fields Too Large",
    500: "Internal Server Error", 503: "Service Unavailable",
}

SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy",
     "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
     "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
     "form-action 'none'; frame-ancestors 'none'"),
)


class WebError(Exception):
    status = 400
    code = "invalid"

    def __init__(self, message, status=None, code=None, retry_after=None):
        self.message = message
        self.retry_after = retry_after
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code

    def __str__(self):
        return str(self.message)


class WebRequest:
    def __init__(self, method, path, query, headers, body, peer_ip):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.peer_ip = peer_ip
        self.host = headers.get("host", "")

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)

    def host_name(self):
        value = self.host.strip()
        if value.startswith("["):
            return value
        if ":" in value:
            return value.rsplit(":", 1)[0]
        return value


def _hex_value(char):
    if "0" <= char <= "9":
        return ord(char) - 48
    if "a" <= char <= "f":
        return ord(char) - 87
    if "A" <= char <= "F":
        return ord(char) - 55
    return -1


def unquote(value):
    out = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char == "+":
            out.append(32)
            index += 1
        elif char == "%" and index + 2 < len(value):
            high = _hex_value(value[index + 1])
            low = _hex_value(value[index + 2])
            if high >= 0 and low >= 0:
                out.append(high * 16 + low)
                index += 3
            else:
                out.extend(char.encode())
                index += 1
        else:
            out.extend(char.encode())
            index += 1
    try:
        return bytes(out).decode()
    except (UnicodeError, ValueError):
        return value


def parse_query(target):
    path, separator, query = target.partition("?")
    values = {}
    if separator:
        for part in query.split("&"):
            key, sep, value = part.partition("=")
            if key:
                values[unquote(key)] = unquote(value) if sep else ""
    return path, values


class WebWriter:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False
        self.headers_sent = False

    def send_all(self, data):
        if self.closed:
            raise OSError("client closed")
        sent = 0
        size = len(data)
        while sent < size:
            try:
                count = self.conn.send(data[sent:])
            except OSError:
                self.closed = True
                raise
            if not count:
                self.closed = True
                raise OSError("client closed")
            sent += count

    def _headers(self, status, content_type, length, extra=None):
        lines = [
            "HTTP/1.1 %s %s" % (status, STATUS_TEXT.get(status, "Error")),
            "Content-Type: %s" % content_type,
            "Connection: close",
            "Cache-Control: no-store",
        ]
        if length is not None:
            lines.append("Content-Length: %s" % length)
        for name, value in SECURITY_HEADERS:
            lines.append("%s: %s" % (name, value))
        if extra:
            for name, value in extra:
                lines.append("%s: %s" % (name, value))
        return ("\r\n".join(lines) + "\r\n\r\n").encode()

    def send_response(self, status, body, content_type="application/json; charset=utf-8",
                      extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.conn.settimeout(10)
        self.send_all(self._headers(status, content_type, len(body), extra))
        self.headers_sent = True
        if body:
            self.send_all(body)

    def send_json(self, status, value, extra=None):
        self.send_response(status, json.dumps(value), extra=extra)

    def start_stream(self, content_type="application/x-ndjson; charset=utf-8"):
        if self.closed:
            return False
        self.conn.settimeout(30)
        try:
            self.send_all(self._headers(200, content_type, None))
            self.headers_sent = True
            return True
        except OSError:
            self.closed = True
            return False

    def event(self, value):
        if self.closed:
            return False
        try:
            self.send_all((json.dumps(value) + "\n").encode())
            return True
        except OSError:
            self.closed = True
            return False

    def client_closed(self):
        """Non-blocking check for a browser that closed the NDJSON stream."""
        if self.closed:
            return True
        try:
            poller = select.poll()
            poller.register(self.conn, select.POLLIN)
            if not poller.poll(0):
                return False
            data = self.conn.recv(1)
            if not data:
                self.closed = True
                return True
            # A pipelined byte is unexpected on Connection: close; consuming
            # it is harmless and the next write will detect a real close.
            return False
        except (AttributeError, OSError, ValueError):
            # Fake sockets used by host tests may not support poll/recv.
            return False

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.conn.close()
        except OSError:
            pass


def _ticks_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


def _elapsed_ms(started):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(_ticks_ms(), started)
    return _ticks_ms() - started


def _check_request_timeout(started, timeout_ms):
    if _elapsed_ms(started) >= timeout_ms:
        raise WebError("Request timed out", 408, "timeout")


def _is_timeout(exc):
    code = getattr(exc, "errno", None)
    if code is None and getattr(exc, "args", None):
        code = exc.args[0]
    if code in (11, 110, 116):
        return True
    message = str(exc).lower()
    return "timed out" in message or "etimedout" in message


def _recv(conn, size, started, timeout_ms):
    _check_request_timeout(started, timeout_ms)
    try:
        data = conn.recv(size)
    except OSError as exc:
        if _is_timeout(exc):
            _check_request_timeout(started, timeout_ms)
            return None
        raise
    _check_request_timeout(started, timeout_ms)
    return data


def _read_until(conn, marker, limit, started, timeout_ms):
    data = b""
    while marker not in data:
        if len(data) > limit:
            raise WebError("Request headers are too large", 431, "invalid")
        chunk = _recv(conn, 512, started, timeout_ms)
        if chunk is None:
            continue
        if not chunk:
            raise WebError("Connection closed before headers", 400, "invalid")
        data += chunk
    return data


def read_request(conn, peer_ip, body_limit=65536, timeout=30):
    started = _ticks_ms()
    timeout_ms = max(0, int(timeout * 1000))
    conn.settimeout(5)
    raw = _read_until(conn, b"\r\n\r\n", 8192, started, timeout_ms)
    head, separator, rest = raw.partition(b"\r\n\r\n")
    lines = head.decode("utf-8", "replace").split("\r\n")
    if not lines or len(lines[0]) > 2048:
        raise WebError("Invalid request line", 400, "invalid")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise WebError("Invalid request line", 400, "invalid")
    method, target, version = parts
    method = method.upper()
    if method not in ("GET", "POST"):
        raise WebError("Method not allowed", 405, "invalid")
    if not version.startswith("HTTP/1."):
        raise WebError("Unsupported HTTP version", 400, "invalid")
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        if len(line) > 1024:
            raise WebError("Header line is too long", 431, "invalid")
        name, separator, value = line.partition(":")
        if not separator:
            raise WebError("Invalid header", 400, "invalid")
        name = name.strip().lower()
        value = value.strip()
        if name == "content-length" and name in headers:
            raise WebError("Duplicate Content-Length", 400, "invalid")
        if name == "transfer-encoding":
            raise WebError("Chunked request bodies are not supported", 411, "invalid")
        headers[name] = value
    if len(headers) > 32:
        raise WebError("Too many headers", 431, "invalid")
    path, query = parse_query(target)
    try:
        path.encode("ascii")
    except UnicodeEncodeError:
        raise WebError("Invalid path", 400, "invalid")
    body = b""
    length = 0
    if method == "POST":
        raw_length = headers.get("content-length")
        if raw_length is None:
            raise WebError("Content-Length is required", 411, "invalid")
        try:
            length = int(raw_length)
        except ValueError:
            raise WebError("Invalid Content-Length", 400, "invalid")
        if length < 0 or length > body_limit:
            raise WebError("Request body is too large", 413, "invalid")
        if headers.get("expect", "").lower() == "100-continue":
            conn.send(b"HTTP/1.1 100 Continue\r\n\r\n")
        body = rest
        while len(body) < length:
            chunk = _recv(
                conn, min(1024, length - len(body)), started, timeout_ms
            )
            if chunk is None:
                continue
            if not chunk:
                raise WebError("Request body ended early", 400, "invalid")
            body += chunk
        body = body[:length]
    return WebRequest(method, path, query, headers, body, peer_ip)


class WebServer:
    def __init__(self, app, host="0.0.0.0", port=80):
        self.app = app
        self.host = host
        self.port = int(port)
        self.server = None
        self.alive = False
        self.running = False
        self.error = ""

    def start(self):
        if _thread is None:
            self.error = "_thread is unavailable"
            return False
        if self.alive:
            if self.running and self.server is not None:
                return True
            self.error = "previous web server thread is still stopping"
            return False
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except OSError:
                pass
            server.bind((self.host, self.port))
            server.listen(4)
            server.settimeout(1.0)
            self.server = server
            if self.port == 0:
                try:
                    self.port = server.getsockname()[1]
                except (AttributeError, OSError):
                    pass
            self.running = True
            self.alive = True
            self.error = ""
            try:
                if hasattr(_thread, "stack_size"):
                    _thread.stack_size(32 * 1024)
            except Exception:
                pass
            _thread.start_new_thread(self._serve, ())
            return True
        except Exception as exc:
            self.error = str(exc)
            self.alive = False
            self.running = False
            if self.server is not None:
                try:
                    self.server.close()
                except OSError:
                    pass
                self.server = None
            return False

    def stop(self):
        self.running = False
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
            self.server = None
        deadline = time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.monotonic() * 1000)
        while self.alive:
            now = time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.monotonic() * 1000)
            elapsed = time.ticks_diff(now, deadline) if hasattr(time, "ticks_diff") else now - deadline
            if elapsed > 1000:
                break
            if hasattr(time, "sleep_ms"):
                time.sleep_ms(20)
            else:
                time.sleep(0.02)
        return not self.alive

    def _tick(self):
        try:
            self.app.tick()
        except Exception as exc:
            try:
                self.app.queue_notice("Web tick error: %s" % exc)
            except Exception:
                pass

    def _serve(self):
        try:
            while self.running:
                try:
                    conn, address = self.server.accept()
                except OSError:
                    self._tick()
                    continue
                writer = WebWriter(conn)
                try:
                    timeout_fn = getattr(self.app, "request_timeout", None)
                    request_timeout = timeout_fn() if timeout_fn is not None else 30
                    request = read_request(
                        conn, address[0],
                        body_limit=int(self.app.body_limit()),
                        timeout=request_timeout,
                    )
                    self.app.handle(request, writer)
                except WebError as exc:
                    if writer.headers_sent:
                        writer.event({"t": "error", "code": exc.code, "message": exc.message})
                    else:
                        extra = None
                        if exc.retry_after:
                            extra = [("Retry-After", str(exc.retry_after))]
                        try:
                            writer.send_json(exc.status, {
                                "ok": False, "error": exc.message, "code": exc.code,
                            }, extra=extra)
                        except OSError:
                            pass
                except Exception as exc:
                    if writer.headers_sent:
                        writer.event({"t": "error", "code": "internal",
                                      "message": "Internal error"})
                    else:
                        try:
                            writer.send_json(500, {
                                "ok": False, "error": "Internal error", "code": "internal",
                            })
                        except OSError:
                            pass
                    try:
                        self.app.queue_notice("Web request error: %s" % exc)
                    except Exception:
                        pass
                finally:
                    writer.close()
                self._tick()
        except BaseException as exc:
            self.error = str(exc)
            try:
                self.app.queue_notice("Web server stopped: %s" % exc)
            except Exception:
                pass
        finally:
            self.alive = False
            self.running = False
            if self.server is not None:
                try:
                    self.server.close()
                except OSError:
                    pass
                self.server = None
