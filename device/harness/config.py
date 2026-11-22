import json
import os


DEFAULTS = {
    "api_provider": "openai",
    "base_url": "https://api.openai.com/v1",
    "model": "",
    "ca_file": "/ca.pem",
    "tls_verify": True,
    "ntp_server": "pool.ntp.org",
    "max_tokens": 32768,
    "truncation_retry_max_tokens": 49152,
    "temperature": 0.2,
    "stream": True,
    "stream_header_timeout": 300,
    "stream_idle_timeout": 300,
    "stream_event_idle_timeout": 300,
    "stream_first_token_timeout": 600,
    "thinking_fallback": "low",
    "thinking_level": "high",
    "show_thinking": True,
    "show_model_meta": True,
    "thinking_print_limit": 8192,
    "response_limit": 65536,
    "stream_response_limit": 16777216,
    "history_messages": 200,
    "history_chars": 900000,
    "persist_conversation": True,
    "conversation_file": "/state/conversation.json",
    "conversation_max_chars": 200000,
    "conversation_max_messages": 120,
    "context_tokens": 200000,
    "auto_compact": True,
    "auto_compact_tokens": 150000,
    "compact_keep_messages": 4,
    "summary_max_tokens": 2048,
    "summary_char_limit": 8192,
    "summary_input_chars": 120000,
    "summary_timeout": 300,
    "max_tool_calls": 16,
    "native_tools": True,
    "max_format_retries": 1,
    "api_retries": 3,
    "api_retry_base_ms": 1000,
    "api_retry_max_ms": 30000,
    "api_retry_after_max_ms": 300000,
    "api_retry_jitter_pct": 25,
    "tool_output_limit": 4096,
    "tool_args_limit": 2048,
    "skills_in_prompt": 20,
    "skill_char_limit": 16384,
    "skill_list_limit": 50,
    "workspace_file_limit": 32768,
    "workspace_total_limit": 262144,
    "workspace_files_limit": 64,
    "workspace_read_limit": 16384,
    "workspace_list_limit": 100,
    "workspace_tmp_cleanup": True,
    "memory_char_limit": 8192,
    "memory_append_limit": 512,
    "search_enabled": True,
    "search_provider": "auto",
    "http_tool_enabled": True,
    "http_tool_timeout": 30,
    "http_tool_response_limit": 262144,
    "http_tool_output_limit": 8192,
    "http_tool_max_redirects": 3,
    "http_tool_allow_http": False,
    "http_tool_allowed_ports": [80, 443],
    "http_tool_max_url_chars": 512,
    "http_tool_max_headers": 8,
    "http_tool_header_chars": 512,
    "http_tool_download_limit": 65536,
    "search_bing_url": "https://cn.bing.com/search?q=",
    "search_bing_rss": True,
    "search_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/120.0 Safari/537.36",
    "search_html_limit": 262144,
    "search_count": 5,
    "search_max_results": 10,
    "search_timeout": 60,
    "search_response_limit": 65536,
    "search_output_limit": 8192,
    "search_answer_limit": 2048,
    "search_anthropic_tool": "web_search_20250305",
    "search_openai_tool": "web_search",
    "search_native_max_tokens": 1024,
    "mcp_enabled": True,
    "mcp_file": "/mcp.json",
    "mcp_refresh_s": 600,
    "mcp_refresh_budget_s": 60,
    "mcp_prompt_chars": 12000,
    "mcp_args_limit": 8192,
    "mcp_output_limit": 8192,
    "mcp_response_limit": 262144,
    "mcp_tool_limit": 32,
    "mcp_max_servers": 8,
    "mcp_timeout_s": 60,
    "input_limit": 2000,
    "code_limit": 12288,
    "output_limit": 4096,
    "code_timeout_s": 20,
    "code_timeout_max_s": 300,
    "max_turns": 32,
    "request_timeout": 120,
    "wifi_timeout": 25,
    "watchdog_ms": 30000,
    "http_trace": False,
    "auto_feedback": True,
    "background_execution": True,
    "background_max_jobs": 4,
    "background_max_records": 8,
    "background_thread_stack_kb": 32,
    "auto_run": False,
    "web_enabled": True,
    "web_port": 80,
    "web_boot_timeout_s": 10,
    "web_session_ttl_s": 43200,
    "web_max_sessions": 8,
    "web_login_fail_limit": 5,
    "web_login_lock_ms": 30000,
    "web_body_limit": 32768,
    "web_request_timeout_s": 30,
    "web_command_limit": 4096,
    "web_output_limit": 16384,
    "web_stream_batch_chars": 32,
    "web_history_limit": 200,
    "web_history_chars": 131072,
    "ap_ssid_prefix": "ESP32-Harness",
    "ap_channel": 6,
    "ap_grace_ms": 300000,
    "ap_fallback_enabled": True,
    "wifi_lost_grace_ms": 30000,
    "wifi_reconnect_interval": 60000,
    "web_setup_ap_only": True,
}

THINKING_LEVELS = ("disabled", "low", "high", "max")
THINKING_ALIASES = {
    "disable": "disabled",
    "off": "disabled",
    "none": "disabled",
    # Retired levels keep working as aliases so old configs and scripts do
    # not break; both used to resolve to the high-effort request shape.
    "auto": "high",
    "default": "high",
    "enabled": "high",
    "middle": "high",
    "medium": "high",
}


def normalize_thinking_level(value, default=None):
    level = str(value or "").strip().lower()
    level = THINKING_ALIASES.get(level, level)
    return level if level in THINKING_LEVELS else default


SECRET_DEFAULTS = {
    "wifi_ssid": "",
    "wifi_password": "",
    "api_key": "",
    "web_password_hash": "",
    "web_password_salt": "",
    "ap_password": "esp32setup",
}


class ConfigError(Exception):
    pass


def join_path(root, name):
    if not root or root == "/":
        return "/" + name
    return root.rstrip("/") + "/" + name


def read_json(path, default=None):
    try:
        with open(path, "r") as handle:
            return json.load(handle)
    except OSError:
        return {} if default is None else default
    except ValueError as exc:
        raise ConfigError("Invalid JSON in %s: %s" % (path, exc))


def write_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(value, handle)
    _replace(temporary, path)


def write_text(path, text):
    temporary = path + ".tmp"
    with open(temporary, "w") as handle:
        handle.write(text)
    _replace(temporary, path)


def read_text(path):
    with open(path, "r") as handle:
        return handle.read()


def remove_file(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _replace(source, destination):
    try:
        os.rename(source, destination)
    except OSError:
        remove_file(destination)
        os.rename(source, destination)


class Config:
    def __init__(self, root="/"):
        self.root = root
        self.config_path = join_path(root, "config.json")
        self.secrets_path = join_path(root, "secrets.json")
        stored = read_json(self.config_path, {})
        self.values = dict(DEFAULTS)
        self.values.update(stored)
        # The old 120 s body idle limit aborted long thinking/prefill gaps that
        # are normal on queueing gateways. Migrate configs written before the
        # first-token budget existed; an explicit later 120 s choice is kept.
        if ("stream_first_token_timeout" not in stored
                and stored.get("stream_idle_timeout") == 120):
            self.values["stream_idle_timeout"] = 300
        # The old 50-message / 32 KB web window hid more than half of a long
        # conversation and looked like lost context. Migrate configurations
        # that still carry both old defaults; later explicit choices are kept.
        if (stored.get("web_history_limit") == 50
                and stored.get("web_history_chars") == 32768):
            self.values["web_history_limit"] = 200
            self.values["web_history_chars"] = 131072
        # Drop settings from removed features so old config.json files do not
        # keep advertising them in /status. The standalone DeepSeek Search API
        # and its dedicated key were removed; AUTO now uses provider-native
        # search or keyless Bing.
        legacy_config = False
        for key in ("auto_save_tools", "tools_in_prompt",
                    "tool_data_file_limit", "tool_data_total_limit",
                    "tool_data_files", "search_url", "search_content_limit",
                    "search_include_content"):
            if self.values.pop(key, None) is not None and key.startswith(
                    "search_"):
                legacy_config = True
        # The /peripherals allowlist was removed. Generated code now uses
        # MicroPython's machine/neopixel modules directly; drop the old keys
        # so they do not linger in config.json or /status.
        for key in (
            "peripherals_enabled", "allowed_gpio_pins", "i2c_buses",
            "spi_buses", "uart_buses", "neopixel_strips", "adc_pins",
            "pwm_pins", "touch_pins", "rtc_write", "peripheral_max_transfer",
        ):
            if self.values.pop(key, None) is not None:
                legacy_config = True
        if str(self.values.get("search_provider", "auto")).lower() not in (
                "auto", "bing"):
            self.values["search_provider"] = "auto"
            legacy_config = True
        # thinking_level is a user preference that survives reboots. Migrate
        # retired levels (default/medium) to the current four-level set on
        # load so /status, the web UI and the next request agree.
        self.values["thinking_level"] = normalize_thinking_level(
            self.values.get("thinking_level"), "high"
        )
        for name, default, minimum, maximum in (
            ("background_max_jobs", 4, 1, 4),
            ("background_max_records", 8, 2, 16),
            ("background_thread_stack_kb", 32, 8, 64),
        ):
            try:
                value = int(self.values.get(name, default))
            except (TypeError, ValueError):
                value = default
            self.values[name] = max(minimum, min(maximum, value))
        self.secrets = dict(SECRET_DEFAULTS)
        self.secrets.update(read_json(self.secrets_path, {}))
        # The old DeepSeek Search key has no consumer anymore. Remove it from
        # memory and rewrite the files once so it does not linger on disk.
        if self.secrets.pop("search_api_key", None) is not None:
            legacy_config = True
        if legacy_config:
            try:
                self.save()
                self.save_secrets()
            except Exception:
                pass

    def get(self, name, default=None):
        return self.values.get(name, default)

    def set(self, name, value, save=True):
        self.values[name] = value
        if save:
            self.save()

    def secret(self, name, default=""):
        return self.secrets.get(name, default)

    def set_secret(self, name, value, save=True):
        if name not in SECRET_DEFAULTS:
            raise ConfigError("Unknown secret: %s" % name)
        self.secrets[name] = value
        if save:
            self.save_secrets()

    def save(self):
        write_json(self.config_path, self.values)

    def save_secrets(self):
        write_json(self.secrets_path, self.secrets)

    def save_all(self):
        """Save both files and restore their previous contents on failure.

        Each file still uses atomic replacement. The rollback covers exceptions
        during a two-file update; it cannot make a power cut between the two
        writes globally atomic.
        """
        previous = []
        for path in (self.config_path, self.secrets_path):
            try:
                previous.append((path, read_text(path)))
            except OSError:
                previous.append((path, None))
        try:
            write_json(self.config_path, self.values)
            write_json(self.secrets_path, self.secrets)
        except Exception as exc:
            rollback_errors = []
            for path, text in previous:
                try:
                    if text is None:
                        remove_file(path)
                    else:
                        write_text(path, text)
                except Exception as rollback_error:
                    rollback_errors.append("%s: %s" % (path, rollback_error))
            if rollback_errors:
                raise ConfigError(
                    "Configuration save failed: %s; rollback failed: %s"
                    % (exc, "; ".join(rollback_errors))
                )
            raise

    def reset_memory(self):
        """Restore defaults in RAM without writing either file."""
        self.values = dict(DEFAULTS)
        self.secrets = dict(SECRET_DEFAULTS)

    def public_values(self):
        return dict(self.values)

    def validate_model(self):
        missing = []
        if not self.get("base_url"):
            missing.append("base_url")
        if not self.get("model"):
            missing.append("model")
        if not self.secret("api_key"):
            missing.append("api_key")
        if self.get("tls_verify") and not self.get("ca_file"):
            missing.append("ca_file")
        if missing:
            raise ConfigError("Missing model settings: %s (use /model)" % ", ".join(missing))
        if self.get("tls_verify"):
            try:
                os.stat(self.get("ca_file"))
            except OSError:
                raise ConfigError("CA file is missing: %s (use /ca)" % self.get("ca_file"))

    def validate_wifi(self):
        if not self.secret("wifi_ssid"):
            raise ConfigError("Wi-Fi is not configured (use /wifi)")
