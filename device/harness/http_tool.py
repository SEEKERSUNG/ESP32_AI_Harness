"""Public HTTP fetch/download tool with SSRF and size guards.

The model may request public web pages, JSON APIs and small files. Private,
loopback, link-local, CGNAT and multicast addresses are blocked before the
connection is opened, the validated DNS address is pinned for that socket to
prevent rebinding, and every redirect is validated again. Caller headers are
dropped on cross-origin redirects, and HTTPS-to-HTTP downgrades are rejected.
This remains a best-effort guard, not a sandbox.
"""

import socket
import time

from .http_client import HTTPError, split_url


class HttpToolError(Exception):
    pass


class HttpToolResult:
    def __init__(self, ok, output, error, elapsed_ms):
        self.ok = ok
        self.output = output
        self.error = error
        self.elapsed_ms = elapsed_ms


def _ticks():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.monotonic() * 1000)


def _elapsed(started):
    now = _ticks()
    if hasattr(time, "ticks_diff"):
        return max(0, time.ticks_diff(now, started))
    return max(0, now - started)


def _int_config(config, name, default, minimum=0, maximum=0):
    try:
        value = int(config.get(name, default))
    except (TypeError, ValueError):
        value = default
    if minimum and value < minimum:
        value = minimum
    if maximum and value > maximum:
        value = maximum
    return value


def is_ipv4_literal(host):
    parts = str(host or "").split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part or len(part) > 3:
            return False
        for char in part:
            if char < "0" or char > "9":
                return False
        if int(part) > 255:
            return False
    return True


def is_public_ipv4(host):
    if not is_ipv4_literal(host):
        return False
    a, b, c, _ = [int(part) for part in host.split(".")]
    if a in (0, 10, 127):
        return False
    if a == 100 and 64 <= b <= 127:
        return False
    if a == 169 and b == 254:
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    if a == 192 and b == 168:
        return False
    # 192.0.0.0/24 is IETF protocol assignment space; 192.0.2.0/24 is
    # TEST-NET-1. Neither is a useful public fetch target.
    if a == 192 and b == 0 and c in (0, 2):
        return False
    if a == 198 and b in (18, 19, 51):
        return False
    if a == 192 and b == 88 and c == 99:
        return False
    if a == 203 and b == 0 and c == 113:
        return False
    if a >= 224:
        return False
    return True


def _blocked_hostname(host):
    host = str(host or "").lower().rstrip(".")
    if host in ("localhost", "metadata.google.internal"):
        return True
    for suffix in (
        ".localhost", ".local", ".internal", ".lan", ".home",
        ".corp", ".localdomain",
    ):
        if host.endswith(suffix):
            return True
    return False


def resolve_public_host(host, port):
    host = str(host or "").strip()
    if not host:
        raise HttpToolError("URL host is empty")
    if is_ipv4_literal(host):
        if not is_public_ipv4(host):
            raise HttpToolError(
                "Private, loopback, link-local or reserved addresses are blocked"
            )
        return [host]
    if _blocked_hostname(host):
        raise HttpToolError("Local hostnames are blocked")
    try:
        infos = socket.getaddrinfo(host, port)
    except Exception as exc:
        raise HttpToolError("DNS resolution failed: %s" % exc)
    addresses = []
    for info in infos:
        address = info[-1]
        if not address:
            continue
        ip = address[0]
        if not is_ipv4_literal(ip):
            continue
        if not is_public_ipv4(ip):
            raise HttpToolError("Host resolves to a blocked address: %s" % ip)
        addresses.append(ip)
    if not addresses:
        raise HttpToolError("No public IPv4 address found")
    return addresses


def validate_target(url, config):
    """Validate a URL and return a DNS-pinned connection address."""
    if not isinstance(url, str) or not url.strip():
        raise HttpToolError("url is required")
    url = url.strip()
    max_url = _int_config(config, "http_tool_max_url_chars", 512, 64, 4096)
    if len(url) > max_url:
        raise HttpToolError("URL exceeds %s characters" % max_url)
    if "#" in url:
        url = url.split("#", 1)[0]
    authority = url.split("://", 1)[-1].split("/", 1)[0]
    if "@" in authority:
        raise HttpToolError("URLs with embedded credentials are not supported")
    try:
        scheme, host, port, path = split_url(url)
    except HTTPError as exc:
        raise HttpToolError(str(exc))
    if scheme == "http" and not config.get("http_tool_allow_http", False):
        raise HttpToolError(
            "Plain HTTP is disabled; use https:// or enable http_tool_allow_http"
        )
    ports = config.get("http_tool_allowed_ports", [80, 443])
    if not isinstance(ports, list):
        ports = [80, 443]
    allowed = []
    for value in ports:
        try:
            allowed.append(int(value))
        except (TypeError, ValueError):
            pass
    if port not in allowed:
        raise HttpToolError("Port %s is not allowed" % port)
    addresses = resolve_public_host(host, port)
    return scheme, host, port, path, addresses[0]


def validate_url(url, config):
    scheme, host, port, path, _ = validate_target(url, config)
    return scheme, host, port, path


_BLOCKED_HEADERS = (
    "host", "content-length", "connection", "transfer-encoding",
    "accept-encoding",
)
_HEADER_NAME_PUNCTUATION = "!#$%&'*+-.^_`|~"


def validate_headers(value, config):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HttpToolError("headers must be an object")
    maximum = _int_config(config, "http_tool_max_headers", 8, 1, 32)
    if len(value) > maximum:
        raise HttpToolError("At most %s headers are allowed" % maximum)
    limit = _int_config(config, "http_tool_header_chars", 512, 32, 4096)
    result = {}
    for name, header_value in value.items():
        if not isinstance(name, str) or not isinstance(header_value, str):
            raise HttpToolError("Header names and values must be strings")
        if name != name.strip() or not name:
            raise HttpToolError("Header names may not have surrounding whitespace")
        for char in name:
            if not ("a" <= char <= "z" or "A" <= char <= "Z"
                    or "0" <= char <= "9"
                    or char in _HEADER_NAME_PUNCTUATION):
                raise HttpToolError("Invalid header name: %s" % name)
        lower = name.lower()
        if lower in _BLOCKED_HEADERS:
            raise HttpToolError("Header %s is managed by the harness" % name)
        for char in header_value:
            if (char in "\r\n"
                    or (ord(char) < 32 and char != "\t") or ord(char) == 127):
                raise HttpToolError("Headers may not contain control characters")
        if len(name) + len(header_value) > limit:
            raise HttpToolError("Header %s is too long" % name)
        result[name] = header_value
    return result


def _resolve_location(base_url, location):
    location = str(location or "").strip()
    if not location:
        raise HttpToolError("Redirect is missing a Location header")
    if location.startswith("http://") or location.startswith("https://"):
        return location
    scheme, host, port, path = split_url(base_url)
    authority = host if port in (80, 443) else "%s:%s" % (host, port)
    if location.startswith("//"):
        return scheme + ":" + location
    if location.startswith("/"):
        return scheme + "://" + authority + location
    base_path = path.split("?", 1)[0]
    if location.startswith("?"):
        return scheme + "://" + authority + base_path + location
    directory = base_path.rsplit("/", 1)[0] + "/"
    return scheme + "://" + authority + directory + location


_TEXT_TYPES = (
    "text/", "application/json", "application/xml", "application/javascript",
    "application/x-www-form-urlencoded", "application/xhtml+xml",
    "application/ld+json",
)


def is_text_content(content_type):
    value = str(content_type or "").lower().strip()
    if not value:
        return True
    for prefix in _TEXT_TYPES:
        if value.startswith(prefix):
            return True
    return False


def _header(response, name, default=""):
    try:
        return response.headers.get(name, default)
    except AttributeError:
        return default


def fetch(http, config, url, method="GET", headers=None, timeout=None):
    if http is None:
        raise HttpToolError("HTTP client is unavailable")
    max_redirects = _int_config(config, "http_tool_max_redirects", 3, 0, 10)
    redirects = 0
    current = str(url or "").strip().split("#", 1)[0]
    request_timeout = _int_config(
        config, "http_tool_timeout", 30, 1, 120
    )
    if timeout is not None:
        try:
            request_timeout = min(request_timeout, max(1, int(timeout)))
        except (TypeError, ValueError):
            pass
    max_response = _int_config(
        config, "http_tool_response_limit", 262144, 1024, 1048576
    )
    request_headers = headers or {}
    while True:
        scheme, host, port, _, address = validate_target(current, config)
        try:
            response = http.request(
                method,
                current,
                headers=request_headers,
                timeout=request_timeout,
                max_response=max_response,
                tls_verify=config.get("tls_verify"),
                ca_file=config.get("ca_file"),
                connect_address=(address, port),
            )
        except Exception as exc:
            raise HttpToolError("HTTP request failed: %s" % exc)
        if response.status in (301, 302, 303, 307, 308):
            location = _header(response, "location", "")
            redirects += 1
            if redirects > max_redirects:
                raise HttpToolError("Too many redirects")
            next_url = _resolve_location(current, location).strip()
            try:
                next_scheme, next_host, next_port, _ = split_url(next_url)
            except HTTPError as exc:
                raise HttpToolError(str(exc))
            if scheme == "https" and next_scheme == "http":
                raise HttpToolError("Redirect from HTTPS to plain HTTP is blocked")
            # Headers may contain caller-provided credentials. They belong to
            # the original origin and must not follow a cross-origin redirect.
            if (next_scheme, next_host.lower(), next_port) != (
                    scheme, host.lower(), port):
                request_headers = {}
            current = next_url
            continue
        return current, response


class HttpTool:
    def __init__(self, config, http, workspace=None):
        self.config = config
        self.http = http
        self.workspace = workspace

    def run(self, args):
        started = _ticks()
        if not self.config.get("http_tool_enabled", True):
            return HttpToolResult(False, "", "HTTP tool is disabled", 0)
        if not isinstance(args, dict):
            return HttpToolResult(False, "", "args must be an object", 0)
        action = str(args.get("action", "get") or "get").lower()
        if action not in ("get", "head", "download"):
            return HttpToolResult(
                False, "", "action must be get, head or download", 0
            )
        try:
            headers = validate_headers(args.get("headers"), self.config)
        except HttpToolError as exc:
            return HttpToolResult(False, "", str(exc), _elapsed(started))
        url = args.get("url")
        try:
            if action == "download":
                return self._download(url, args, headers, started)
            final_url, response = fetch(
                self.http, self.config, url,
                "HEAD" if action == "head" else "GET",
                headers, args.get("timeout"),
            )
        except HttpToolError as exc:
            return HttpToolResult(False, "", str(exc), _elapsed(started))
        if action == "head":
            return self._head_result(final_url, response, started)
        return self._get_result(final_url, response, args, started)

    def _head_result(self, url, response, started):
        if response.status < 200 or response.status >= 300:
            return HttpToolResult(
                False, "",
                "HTTP %s" % response.status,
                _elapsed(started),
            )
        lines = [
            "url: %s" % url,
            "http_status: %s" % response.status,
        ]
        for name in (
            "content-type", "content-length", "last-modified", "etag",
            "location", "server",
        ):
            value = _header(response, name, "")
            if value:
                lines.append("%s: %s" % (name, value))
        return HttpToolResult(True, "\n".join(lines), "", _elapsed(started))

    def _get_result(self, url, response, args, started):
        body = response.body or b""
        content_type = _header(response, "content-type", "")
        if response.status < 200 or response.status >= 300:
            snippet = body[:600].decode("utf-8", "replace")
            return HttpToolResult(
                False, "",
                "HTTP %s: %s" % (response.status, snippet),
                _elapsed(started),
            )
        if not is_text_content(content_type):
            return HttpToolResult(
                False, "",
                "Binary content (%s); use action=download with a workspace path"
                % (content_type or "unknown"),
                _elapsed(started),
            )
        text = body.decode("utf-8", "replace")
        limit = _int_config(
            self.config, "http_tool_output_limit", 8192, 1, 65536
        )
        requested = args.get("max_chars")
        if requested is not None:
            try:
                limit = min(limit, max(1, int(requested)))
            except (TypeError, ValueError):
                pass
        truncated = len(text) > limit
        if truncated:
            text = text[:limit] + "\n...[response truncated]"
        lines = [
            "url: %s" % url,
            "http_status: %s" % response.status,
            "content_type: %s" % (content_type or "unknown"),
            "bytes: %s" % len(body),
            "truncated: %s" % ("true" if truncated else "false"),
            "body:",
            text,
        ]
        return HttpToolResult(True, "\n".join(lines), "", _elapsed(started))

    def _download(self, url, args, headers, started):
        if self.workspace is None:
            return HttpToolResult(
                False, "", "Workspace is unavailable", _elapsed(started)
            )
        final_url, response = fetch(
            self.http, self.config, url, "GET", headers, args.get("timeout")
        )
        body = response.body or b""
        content_type = _header(response, "content-type", "")
        if response.status < 200 or response.status >= 300:
            snippet = body[:600].decode("utf-8", "replace")
            return HttpToolResult(
                False, "",
                "HTTP %s: %s" % (response.status, snippet),
                _elapsed(started),
            )
        limit = _int_config(
            self.config, "http_tool_download_limit", 65536, 1, 1048576
        )
        if len(body) > limit:
            return HttpToolResult(
                False, "",
                "Download is %s bytes; the limit is %s bytes"
                % (len(body), limit),
                _elapsed(started),
            )
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            path = self._default_download_path(final_url)
        try:
            metadata = self.workspace.write_bytes(
                path.strip(), body, limit=limit
            )
        except Exception as exc:
            return HttpToolResult(
                False, "", "Download save failed: %s" % exc,
                _elapsed(started),
            )
        lines = [
            "url: %s" % final_url,
            "http_status: %s" % response.status,
            "content_type: %s" % (content_type or "unknown"),
            "bytes: %s" % len(body),
            "path: %s" % metadata["path"],
            "text: %s" % ("true" if is_text_content(content_type) else "false"),
        ]
        return HttpToolResult(True, "\n".join(lines), "", _elapsed(started))

    @staticmethod
    def _default_download_path(url):
        try:
            _, _, _, path = split_url(url)
        except HTTPError:
            path = "/"
        name = path.split("?", 1)[0].rsplit("/", 1)[-1] or "download.bin"
        safe = []
        for char in name:
            if ("a" <= char <= "z" or "A" <= char <= "Z"
                    or "0" <= char <= "9" or char in "._-"):
                safe.append(char)
            else:
                safe.append("_")
        name = "".join(safe).strip("._") or "download.bin"
        return "downloads/" + name[:64]
