import time


class HTTPError(Exception):
    pass


class HTTPCancelled(Exception):
    """Raised by a cancel_check callback to abort an HTTP request."""
    pass


class HTTPResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body


# Poll the socket at most this often while waiting for SSE body data. The
# socket timeout must stay well below watchdog_ms: the serial main thread
# feeds the watchdog around each recv, and a recv that sleeps for the whole
# idle timeout would trip the WDT before the idle check can run. A short
# poll also yields to the web thread while a serial model turn is waiting
# for TLS data; otherwise status requests can stall for many seconds.
SOCKET_POLL_S = 1


class HTTPBodyReader:
    """Incremental HTTP response body reader for SSE."""

    def __init__(self, stream, headers, initial, max_response,
                 idle_timeout, feed, trace, cancel_check=None,
                 first_byte_timeout=0):
        self.stream = stream
        self.headers = headers
        self.raw = bytearray(initial)
        self.body = bytearray()
        self.idle_timeout = idle_timeout
        # Some gateways flush response headers immediately but only start the
        # event stream after a long prefill/queue. The first body byte may take
        # much longer than the gap allowed between later tokens.
        try:
            self.first_byte_timeout = max(0, int(first_byte_timeout or 0))
        except (TypeError, ValueError):
            self.first_byte_timeout = 0
        self.initial_bytes = len(initial)
        self.max_response = max_response
        self.feed = feed
        self.trace = trace
        self.cancel_check = cancel_check
        self.last_data = self._ticks()
        self.eof = False
        self.bytes_read = len(initial)
        try:
            stream.settimeout(max(1, min(idle_timeout, SOCKET_POLL_S)))
        except (AttributeError, OSError):
            pass
        transfer = headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer:
            self.mode = "chunked"
            self.chunk_state = "size"
            self.chunk_remaining = 0
        elif "content-length" in headers:
            try:
                self.remaining = int(headers["content-length"])
            except ValueError:
                raise HTTPError("Invalid Content-Length")
            if self.remaining < 0:
                raise HTTPError("Invalid Content-Length")
            self.mode = "length"
        else:
            self.remaining = None
            self.mode = "close"
        status_line = headers.get("_status_line", "")
        parts = status_line.split(" ", 2)
        try:
            self.status = int(parts[1])
        except (IndexError, ValueError):
            raise HTTPError("Invalid HTTP status")

    def readline(self, max_line=8192):
        while True:
            index = self.body.find(b"\n")
            if index >= 0:
                line = bytes(self.body[:index + 1])
                self.body = bytearray(self.body[index + 1:])
                return line
            if index < 0 and self.body and self.eof:
                line = bytes(self.body)
                self.body = bytearray()
                return line
            if self.eof:
                return b""
            self._fill()
            if len(self.body) > max_line and self.body.find(b"\n") < 0:
                raise HTTPError("SSE line exceeds limit")

    def read_all(self, limit=4096):
        while not self.eof and len(self.body) < limit:
            self._fill()
        return bytes(self.body[:limit])

    def has_buffered_line(self):
        """Return True if a complete SSE line is already buffered."""
        return self.body.find(b"\n") >= 0 or self.raw.find(b"\n") >= 0

    def close(self):
        try:
            self.stream.close()
        except Exception:
            pass
        self.stream = None

    def _fill(self):
        if self.eof:
            return
        if self.mode == "chunked":
            self._fill_chunked()
        elif self.mode == "length":
            self._fill_length()
        else:
            self._fill_close()

    def _fill_chunked(self):
        while True:
            if self.chunk_state == "size":
                line = self._read_raw_line()
                size_text = line.rstrip(b"\r\n").split(b";", 1)[0]
                try:
                    size = int(size_text.strip(), 16)
                except ValueError:
                    raise HTTPError("Invalid chunk size")
                if size == 0:
                    self.chunk_state = "trailers"
                else:
                    self.chunk_remaining = size
                    self.chunk_state = "data"
            if self.chunk_state == "data":
                if not self.raw:
                    if not self._recv_more():
                        raise HTTPError("Stream ended inside chunk")
                    continue
                count = min(self.chunk_remaining, len(self.raw))
                self.body.extend(self.raw[:count])
                self.raw = bytearray(self.raw[count:])
                self.chunk_remaining -= count
                if self.chunk_remaining == 0:
                    self.chunk_state = "data_crlf"
                return
            if self.chunk_state == "data_crlf":
                if len(self.raw) < 2:
                    if not self._recv_more():
                        raise HTTPError("Stream ended before chunk CRLF")
                    continue
                if self.raw[:2] != b"\r\n":
                    raise HTTPError("Invalid chunk terminator")
                self.raw = bytearray(self.raw[2:])
                self.chunk_state = "size"
                continue
            if self.chunk_state == "trailers":
                line = self._read_raw_line()
                if line in (b"\r\n", b"\n"):
                    self.eof = True
                    return
                continue

    def _fill_length(self):
        if self.remaining == 0:
            self.eof = True
            return
        if not self.raw:
            if not self._recv_more():
                raise HTTPError("Stream ended before Content-Length")
            return
        count = min(self.remaining, len(self.raw))
        self.body.extend(self.raw[:count])
        self.raw = bytearray(self.raw[count:])
        self.remaining -= count
        if self.remaining == 0:
            self.eof = True

    def _fill_close(self):
        if not self.raw:
            if not self._recv_more():
                self.eof = True
                return
            return
        self.body.extend(self.raw)
        self.raw = bytearray()

    def _read_raw_line(self):
        while True:
            index = self.raw.find(b"\r\n")
            if index >= 0:
                line = bytes(self.raw[:index + 2])
                self.raw = bytearray(self.raw[index + 2:])
                return line
            if len(self.raw) > 8192:
                raise HTTPError("Chunk line exceeds limit")
            if not self._recv_more():
                raise HTTPError("Stream ended inside chunk line")

    def _recv_more(self):
        if self.eof:
            return False
        if self.cancel_check is not None:
            self.cancel_check()
        self.feed()
        now = self._ticks()
        idle_ms = self._elapsed(self.last_data, now)
        # Streaming responses are bounded by idle time, not by a fixed total:
        # every received chunk refreshes last_data, so a long answer that keeps
        # producing tokens is allowed to continue. stream_response_limit still
        # bounds total bytes, and the response-header wait is bounded by the
        # request-level timeout before this reader is created.
        limit = self.idle_timeout
        if self.first_byte_timeout and self.bytes_read <= self.initial_bytes:
            limit = max(limit, self.first_byte_timeout)
        if idle_ms > limit * 1000:
            raise HTTPError("SSE idle timeout")
        try:
            data = self.stream.recv(1024)
        except OSError as exc:
            if self._is_timeout(exc):
                return True
            raise
        if not data:
            self.eof = True
            return False
        self.last_data = self._ticks()
        self.bytes_read += len(data)
        self.raw.extend(data)
        if self.bytes_read > self.max_response + 65536:
            raise HTTPError("SSE response exceeds configured limit")
        return True

    @staticmethod
    def _is_timeout(exc):
        code = getattr(exc, "errno", None)
        if code is None and getattr(exc, "args", None):
            code = exc.args[0]
        if code in (11, 110, 116):
            return True
        message = str(exc).lower()
        return "timed out" in message or "etimedout" in message

    @staticmethod
    def _ticks():
        if hasattr(time, "ticks_ms"):
            return time.ticks_ms()
        return int(time.monotonic() * 1000)

    @staticmethod
    def _elapsed(started, now):
        if hasattr(time, "ticks_diff"):
            return time.ticks_diff(now, started)
        return now - started


def split_url(url):
    for char in url:
        if ord(char) < 32 or ord(char) == 127:
            raise HTTPError("URL contains control characters")
    if url.startswith("https://"):
        scheme = "https"
        rest = url[8:]
        default_port = 443
    elif url.startswith("http://"):
        scheme = "http"
        rest = url[7:]
        default_port = 80
    else:
        raise HTTPError("URL must start with http:// or https://")
    slash = rest.find("/")
    if slash < 0:
        authority = rest
        path = "/"
    else:
        authority = rest[:slash]
        path = rest[slash:]
    if not authority or authority.startswith("[") or authority.count(":") > 1:
        raise HTTPError("Only IPv4/DNS HTTP endpoints are supported")
    if ":" in authority:
        host, port_text = authority.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            raise HTTPError("Invalid URL port")
    else:
        host = authority
        port = default_port
    if not host:
        raise HTTPError("URL host is empty")
    return scheme, host, port, path


def decode_chunked(data, limit):
    output = bytearray()
    position = 0
    while True:
        line_end = data.find(b"\r\n", position)
        if line_end < 0:
            raise HTTPError("Incomplete chunk size")
        size_text = bytes(data[position:line_end]).split(b";", 1)[0]
        try:
            size = int(size_text.strip(), 16)
        except ValueError:
            raise HTTPError("Invalid chunk size")
        position = line_end + 2
        if size == 0:
            return bytes(output)
        end = position + size
        if end + 2 > len(data) or data[end:end + 2] != b"\r\n":
            raise HTTPError("Incomplete chunk data")
        if len(output) + size > limit:
            raise HTTPError("HTTP response exceeds configured limit")
        output.extend(data[position:end])
        position = end + 2


def chunked_complete(data):
    position = 0
    while True:
        line_end = data.find(b"\r\n", position)
        if line_end < 0:
            return False
        size_text = bytes(data[position:line_end]).split(b";", 1)[0]
        try:
            size = int(size_text.strip(), 16)
        except ValueError:
            raise HTTPError("Invalid chunk size")
        position = line_end + 2
        if size == 0:
            while True:
                line_end = data.find(b"\r\n", position)
                if line_end < 0:
                    return False
                if line_end == position:
                    return True
                position = line_end + 2
        end = position + size
        if end + 2 > len(data):
            return False
        if data[end:end + 2] != b"\r\n":
            raise HTTPError("Incomplete chunk data")
        position = end + 2


class HttpClient:
    def __init__(self, feed=None, trace=None, cancel_check=None):
        self.feed = feed or (lambda: None)
        self.trace = trace or (lambda message: None)
        self.cancel_check = cancel_check
        self.stream = None
        self.sock = None
        self.connection_key = None

    def close(self):
        stream = self.stream
        sock = self.sock
        self.stream = None
        self.sock = None
        self.connection_key = None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        elif sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def post(self, url, body, headers, timeout=60, max_response=24576,
             tls_verify=True, ca_file="/ca.pem"):
        return self.request(
            "POST", url, body, headers, timeout, max_response, tls_verify, ca_file
        )

    def post_stream(self, url, body, headers, timeout=120, max_response=65536,
                    tls_verify=True, ca_file="/ca.pem", idle_timeout=30,
                    first_byte_timeout=0):
        scheme, host, port, path = split_url(url)
        if isinstance(body, str):
            body = body.encode()
        request_headers = {
            "Host": host if port in (80, 443) else "%s:%s" % (host, port),
            "Content-Length": str(len(body)),
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "User-Agent": "esp32-harness/0.4",
        }
        request_headers.update(headers or {})
        lines = ["POST %s HTTP/1.1" % path]
        for name in request_headers:
            lines.append("%s: %s" % (name, request_headers[name]))
        head = ("\r\n".join(lines) + "\r\n\r\n").encode()

        self.close()
        stream = None
        try:
            self._open_connection(scheme, host, port, tls_verify, ca_file)
            stream = self.stream
            self._send_all(stream, head)
            self._send_all(stream, body)
            response_headers, initial = self._read_stream_headers(stream, timeout)
            reader = HTTPBodyReader(
                stream,
                response_headers,
                initial,
                max_response,
                idle_timeout,
                self.feed,
                self.trace,
                self.cancel_check,
                first_byte_timeout=first_byte_timeout,
            )
            self.stream = None
            self.sock = None
            self.connection_key = None
            return reader
        except Exception:
            self.close()
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            raise

    def _read_stream_headers(self, stream, timeout):
        raw = bytearray()
        started = self._ticks()
        try:
            stream.settimeout(SOCKET_POLL_S)
        except (AttributeError, OSError):
            pass
        while True:
            index = raw.find(b"\r\n\r\n")
            if index >= 0:
                if index > 8192:
                    raise HTTPError("HTTP headers are too large")
                return self._parse_headers(bytes(raw[:index])), raw[index + 4:]
            if self._elapsed(started) > timeout * 1000:
                raise HTTPError("HTTP response headers timed out")
            if self.cancel_check is not None:
                self.cancel_check()
            self.feed()
            try:
                data = stream.recv(1024)
            except OSError as exc:
                if self._is_timeout(exc):
                    continue
                raise
            if not data:
                raise HTTPError("Connection closed before HTTP headers")
            raw.extend(data)
            if len(raw) > 16384:
                raise HTTPError("HTTP headers are too large")

    def request(self, method, url, body=b"", headers=None, timeout=60,
                max_response=24576, tls_verify=True, ca_file="/ca.pem",
                connect_address=None):
        scheme, host, port, path = split_url(url)
        if isinstance(body, str):
            body = body.encode()
        request_headers = {
            "Host": host if port in (80, 443) else "%s:%s" % (host, port),
            "Content-Length": str(len(body)),
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
            "User-Agent": "esp32-harness/0.4",
        }
        if method == "POST":
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        return self._request(method, scheme, host, port, path, body, request_headers,
                             timeout, max_response, tls_verify, ca_file,
                             connect_address)

    def _request(self, method, scheme, host, port, path, body, headers, timeout,
                 max_response, tls_verify, ca_file, connect_address=None):
        key = (scheme, host, port, tls_verify, ca_file, connect_address)
        lines = ["%s %s HTTP/1.1" % (method, path)]
        for name in headers:
            lines.append("%s: %s" % (name, headers[name]))
        head = ("\r\n".join(lines) + "\r\n\r\n").encode()

        last_error = None
        for attempt in range(2):
            reused = self.stream is not None and self.connection_key == key
            try:
                if not reused:
                    self._open_connection(
                        scheme, host, port, tls_verify, ca_file,
                        connect_address=connect_address)
                    self.connection_key = key
                self.trace("HTTP send")
                self.feed()
                self._send_all(self.stream, head)
                self._send_all(self.stream, body)
                raw, reusable = self._receive(
                    self.stream, timeout, max_response,
                    header_only=(method == "HEAD"),
                )
                response = self._parse_response(
                    raw, max_response, header_only=(method == "HEAD")
                )
                if not reusable:
                    self.close()
                return response
            except HTTPCancelled:
                self.close()
                raise
            except Exception as exc:
                last_error = exc
                self.close()
                if reused and attempt == 0:
                    self.trace("HTTP stale keep-alive; reconnecting")
                    continue
                break

        if isinstance(last_error, HTTPError):
            raise last_error
        raise HTTPError("HTTPS request failed: %s" % last_error)

    def _open_connection(self, scheme, host, port, tls_verify, ca_file,
                         connect_address=None):
        import gc
        import socket
        import ssl

        self.close()
        if connect_address is None:
            self.trace("HTTP resolve %s:%s" % (host, port))
            if self.cancel_check is not None:
                self.cancel_check()
            self.feed()
            address = socket.getaddrinfo(host, port)[0][-1]
        else:
            # A URL fetch validates the DNS answer before opening the socket.
            # Passing that exact address here prevents a second DNS lookup from
            # being rebound to a private address after validation.
            address = connect_address
        if self.cancel_check is not None:
            self.cancel_check()
        self.feed()
        self.trace("HTTP connect")
        sock = socket.socket()
        try:
            sock.settimeout(10)
            sock.connect(address)
            if self.cancel_check is not None:
                self.cancel_check()
            stream = sock
            if scheme == "https":
                gc.collect()
                self.trace("HTTP TLS handshake; heap=%s" % gc.mem_free())
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.verify_mode = ssl.CERT_REQUIRED if tls_verify else ssl.CERT_NONE
                if tls_verify:
                    context.load_verify_locations(cafile=ca_file)
                self.trace("HTTP TLS wrapping")
                stream = context.wrap_socket(sock, server_hostname=host)
                self.trace("HTTP TLS ready")
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
            raise
        self.sock = sock
        self.stream = stream

    def _send_all(self, stream, data):
        view = memoryview(data)
        sent = 0
        while sent < len(data):
            if self.cancel_check is not None:
                self.cancel_check()
            count = stream.send(view[sent:])
            if not count:
                raise HTTPError("Connection closed while sending")
            sent += count
            self.feed()

    def _receive(self, stream, timeout, max_response, header_only=False):
        raw = bytearray()
        started = self._ticks()
        header_end = -1
        expected_end = None
        is_chunked = False
        ended_by_close = False
        headers = {}
        wire_limit = max_response + 16384
        self.trace("HTTP receive")
        while True:
            if self.cancel_check is not None:
                self.cancel_check()
            self.feed()
            if self._elapsed(started) > timeout * 1000:
                raise HTTPError("HTTP response timed out")
            try:
                data = stream.recv(1024)
            except OSError as exc:
                if self._is_timeout(exc) and self._elapsed(started) <= timeout * 1000:
                    continue
                raise
            if not data:
                ended_by_close = True
                break
            self.trace("HTTP recv %s" % len(data))
            raw.extend(data)
            if len(raw) > wire_limit:
                raise HTTPError("HTTP response exceeds configured limit")
            if header_end < 0:
                header_end = raw.find(b"\r\n\r\n")
                if header_end >= 0:
                    if header_end > 8192:
                        raise HTTPError("HTTP headers are too large")
                    headers = self._parse_headers(bytes(raw[:header_end]))
                    content_length = headers.get("content-length")
                    is_chunked = "chunked" in headers.get("transfer-encoding", "").lower()
                    if content_length is not None:
                        try:
                            content_length = int(content_length)
                        except ValueError:
                            raise HTTPError("Invalid Content-Length")
                        if content_length < 0:
                            raise HTTPError("Invalid Content-Length")
                        if content_length > max_response:
                            raise HTTPError("HTTP response exceeds configured limit")
                        expected_end = header_end + 4 + content_length
                    elif not is_chunked:
                        # No framing: the body is delimited by connection close.
                        expected_end = None
            if header_end >= 0:
                if header_only:
                    # A HEAD response advertises Content-Length but has no body;
                    # do not wait for bytes that will never arrive.
                    break
                if expected_end is not None and len(raw) >= expected_end:
                    break
                if is_chunked and chunked_complete(raw[header_end + 4:]):
                    break
        if header_end < 0:
            raise HTTPError("Invalid HTTP response")
        if header_only:
            return bytes(raw), False
        if expected_end is not None and len(raw) < expected_end:
            raise HTTPError("HTTP response ended before Content-Length")
        if is_chunked and not chunked_complete(raw[header_end + 4:]):
            raise HTTPError("HTTP response ended inside chunked body")
        connection_close = ended_by_close or "close" in headers.get("connection", "").lower()
        reusable = not connection_close and (is_chunked or expected_end is not None)
        self.trace("HTTP received %s bytes; reusable=%s close=%s" % (
            len(raw), reusable, connection_close))
        return raw, reusable

    @staticmethod
    def _parse_headers(head):
        lines = head.decode().split("\r\n")
        headers = {"_status_line": lines[0]}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        return headers

    def _parse_response(self, raw, max_response, header_only=False):
        header_end = raw.find(b"\r\n\r\n")
        if header_end < 0:
            raise HTTPError("Invalid HTTP response")
        headers = self._parse_headers(bytes(raw[:header_end]))
        parts = headers["_status_line"].split(" ", 2)
        if len(parts) < 2:
            raise HTTPError("Invalid HTTP status line")
        try:
            status = int(parts[1])
        except ValueError:
            raise HTTPError("Invalid HTTP status")
        self.trace("HTTP status %s" % status)
        if header_only:
            return HTTPResponse(status, headers, b"")
        body = bytes(raw[header_end + 4:])
        if "chunked" in headers.get("transfer-encoding", "").lower():
            body = decode_chunked(body, max_response)
        elif "content-length" in headers:
            try:
                content_length = int(headers["content-length"])
            except ValueError:
                raise HTTPError("Invalid Content-Length")
            if content_length < 0:
                raise HTTPError("Invalid Content-Length")
            if len(body) < content_length:
                raise HTTPError("HTTP response ended before Content-Length")
            body = body[:content_length]
        if len(body) > max_response:
            raise HTTPError("HTTP response exceeds configured limit")
        encoding = headers.get("content-encoding", "identity").lower()
        if encoding not in ("", "identity"):
            raise HTTPError("Compressed HTTP responses are not supported")
        return HTTPResponse(status, headers, body)

    @staticmethod
    def _is_timeout(exc):
        code = getattr(exc, "errno", None)
        if code is None and getattr(exc, "args", None):
            code = exc.args[0]
        if code in (11, 110, 116):
            return True
        message = str(exc).lower()
        return "timed out" in message or "etimedout" in message

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
