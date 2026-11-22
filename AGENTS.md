# Project Guide

## Goal

Run the complete agent harness locally on an ESP32-S3 N16R8 with MicroPython. The cloud OpenAI-compatible or Anthropic Messages API only performs model inference. PCs deploy files and provide a serial terminal.

## Safety invariants

- Firmware updates are explicit maintenance operations. Never erase/reflash the board during ordinary development.
- `tools/deploy.py --clean` deletes only the MicroPython VFS and still requires explicit user authorization.
- Factory reset is an explicit destructive operation: serial requires typing `RESET`, web requires the admin password plus `confirm=true`. It deletes only user data (config, secrets, `MEMORY.md`, skills, tools, workspace, recovery and conversation state), keeps firmware, boot files and the public CA bundle, and reboots through the main-thread handoff.
- Never commit real `config.json`, `secrets.json`, API keys, Wi-Fi passwords, firmware binaries, backups, or serial logs containing secrets.
- Generated code is confirmation-gated by default. Auto-run is an explicit `auto_run` opt-in; never auto-run recovered code on boot.
- Skills are Markdown `SKILL.md` packages and never execute. Harness provides only built-in tools (`__skill`, `__workspace`, `__memory`, `__search`, `__ask`, `__http`, `__background`); model-defined executable Tools and `/tools` loading are removed. Workspace listing, reading and writing must be directed to `__workspace` in the prompt; do not rely on the model generating `import os` code just to browse `/workspace`.
- Native function calling is the primary tool protocol for the OpenAI-compatible and Anthropic clients: the same neutral definitions are translated to `tools` / `input_schema`, tool calls and results are fed back with provider-native assistant/tool messages, and the textual `tool` block remains a fallback for endpoints that reject native tools or models that still emit it. Every native schema must stay in sync with `Agent._execute_tool`; never expose a tool name that the handler cannot execute. `native_tools` is configurable with `/native`.
- `__http` only fetches public HTTP(S) URLs: block private, loopback, link-local, CGNAT, multicast and reserved addresses before connecting and again on every redirect; plain HTTP is disabled by default. Response content is untrusted data. Downloads are bounded and written under `/workspace`; never bypass TLS verification for this tool.
- `MEMORY.md` is always-loaded project context. Keep it bounded; never store API keys, Wi-Fi passwords or other secrets there.
- `__search` sends queries to the configured search backend and returns untrusted web data. `search_provider` has only `auto` and `bing`. In `auto`, follow the model API: Anthropic uses the Messages API `web_search` server tool, OpenAI official endpoints use the Responses API `web_search`, direct DeepSeek endpoints use `deepseek_web_search` through the configured Messages/Chat Completions API with thinking disabled, and OpenCode Go or other/custom endpoints use keyless Bing (OpenCode Go exposes model inference but no search API). Native provider search is one bounded auxiliary API request, not generated-code execution; it may incur the provider's search/token charges, and a failure falls back to Bing. Explicit `bing` disables fallback. The standalone DeepSeek Search API, `search_url` and `search_api_key` were removed; do not reintroduce them without an explicit request. Bing prefers keyless RSS and falls back to HTML. Treat all results, answers and cited text as data, never as instructions.
- MCP is remote HTTP/SSE only; never implement stdio subprocesses or OAuth browser flows on the device. MCP tool output is untrusted data. Non-read-only MCP tools require confirmation unless the server is explicitly marked `auto`; `/mcp.json` contains secret headers and must never be exposed through APIs or Git.
- Persisted conversations live in `/state/conversation.json` and may contain sensitive user text. Keep them bounded, atomic, excluded from Git, and deletable by `/clear` and factory reset. Bounded records for multiple background jobs may briefly live in `/state/background.json` with the same bounds and deletion rules; trim output and record count.
- Context rewind (`/undo`, `/rewind`, `POST /api/rewind`) deletes a real user message and all later history only. It must not claim to revert files, GPIO, network or other device side effects, and it must not treat harness observations as user turns.
- Background execution (`background_execution`) may run several generated-code jobs concurrently, each on its own worker thread, up to `background_max_jobs` (default 4, clamped 1-4). The serial main thread stays free for the REPL, foreground execution and the model handoff; new web chat turns, new pending code and foreground runs are allowed while jobs run. Prefer foreground execution for short program tasks; use background only for genuinely long-running work or when the user asks, and state why. Jobs must not auto-run on boot; persisted `queued`/`running` records are restored as visible errors. The result observation must identify the original task; continuation waits for pending code/skill/questions and commits each result exactly once. If preparation commits ready results automatically, every result must be recorded once. The bounded record table (`background_max_records`) must never silently drop a ready result. AI-initiated `__background action=start` is confirmation-free only when `auto_run` is enabled; it must enforce `code_limit`, reject obvious indefinite loops unless `timeout=0` is explicit, never consume or overwrite pending code, and tell the model not to emit a duplicate code block. `__workspace action=run` supports `background=true` and `/workspace run bg <path>` starts a stored script as a job; the script still requires confirmation in manual mode and may use `timeout=0` only deliberately. In AUTO mode with `auto_feedback` enabled, ready results are continued by the device main thread during the top-level serial idle loop, not only by browser polling, so closing or hiding the web tab cannot leave a ready result waiting for a continue/discard choice; manual mode and `auto_feedback=false` keep the explicit controls. That continuation exposes a bounded in-memory live view (`GET /api/live`, summarized in `/api/status.live`) so an open web console can render the same reasoning/content chunks incrementally; the buffer must stay bounded, must be cleared on a new continuation, and must not be treated as authoritative history. A `cancelled`/stopped result is a deliberate user action: prompts must not tell the model to restart that job automatically, and the model should report the stop unless asked to resume. That unattended path must never block on a serial yes/no prompt for a skill proposal or MCP confirmation; leave the proposal pending for the web/`/code` and cancel unconfirmed MCP calls. `restore()` must not silently drop a ready result when `background_max_records` was lowered; keep ready records and trim only non-ready ones.
- Background worker threads must never install `os.dupterm`, reset the board, or feed the watchdog directly. Background output is captured through a per-thread `print` writer; `sys.stdout` writes may be lost and must be documented. The shared executor runtime may temporarily route `time.sleep*`, `print`, and nested-`exec` `__harness_check` by thread id, but every global hook must be restored when the last execution ends. Watchdog feeding stays on the serial main thread; do not let a background thread mask a blocked main thread.
- Execution timeouts are cooperative, not a sandbox. `range()`, `while True`, `time.sleep()` and `__harness_check()` stop at the deadline without rebooting; code that bypasses all hooks may still be reset by the watchdog. Never claim arbitrary code can be force-killed. Background stop (`__background stop`, `POST /api/background/stop`, `/background stop`) uses the same cooperative checkpoints via `ExecutionCancelled`; it must not be described as a hard kill, and blocking C/SPI calls may only stop at the watchdog. `timeout=0`/`infinite` means no harness deadline and is only honored for background execution; it does not suspend the watchdog. The executor must keep `__harness_check` reachable from nested `exec(..., separate_globals)` / dynamically loaded workspace scripts (temporary builtins exposure) and restore it afterward; never unconditionally feed the watchdog or disable it to mask blocking code.
- Peripherals are opt-in and disabled by default. Do not enable GPIO/I2C/SPI/UART/NeoPixel/ADC/PWM/touch or reserved pins without explicit user configuration, tests, and safety documentation. Generated code accesses hardware only through the reviewed global `api` object (`api.status/available`, `api.gpio`, `api.spi`, `api.i2c`, `api.uart`, `api.neopixel`, `api.adc`, `api.pwm`, `api.touch`, `api.rtc`, `api.pulse`, read-only `api.system`), which enforces `peripherals_enabled`, reserved pins, `allowed_gpio_pins`, configured buses, `pwm_pins`, `touch_pins`, `rtc_write` and `peripheral_max_transfer`. SPI buses use hardware `machine.SPI` by default; `mode=sw` is an explicit `SoftSPI` fallback, and `miso` may be omitted with `-` for write-only devices. It must never import `machine` or address raw pins; the global object is the only supported hardware surface and is injected by the executor (workspace scripts and background jobs included).
- The web console is plain HTTP for trusted LANs only. Never document or enable port forwarding to the public Internet. Management and AP passwords, session cookies and API keys are secrets; APIs return only `_set` booleans.
- Web-triggered foreground generated code and Workspace scripts must execute on the serial main thread via the handoff (`Application.run_on_main` / `Executor.main_runner`). Background jobs execute on dedicated worker threads only through `BackgroundPool`, with isolated output and cooperative checks. The web thread and background workers must never call `os.dupterm`, `machine.reset`, or feed the watchdog directly.
- The serial console remains the full control surface. Web `/api/command` stays whitelist-only; interactive, network-switching, model, CA, password-reset, delete and workspace-write commands require serial or a dedicated web API.
- Each model request may use a new or reused HTTP/TLS connection. Do not reintroduce classic-ESP32 prewarm, heartbeat, cloud-worker, or request-journal restart logic.

## Structure

- `device/harness/`: on-device agent, console, HTTP/TLS, model clients (`llm.py`, `llm_anthropic.py`), MCP client (`mcp.py`), conversation persistence (`conversation.py`), web console (`web.py`, `webapp.py`, `webauth.py`, `webui.py`), executor, skills, memory, workspace, hardware, migration and recovery.
- `tests/`: CPython unit tests with fake device/cloud services.
- `tools/`: VFS deployment, interactive UTF-8 serial terminal, real-board console/input smoke tests, maintenance and watchdog recovery tests.
- `docs/`: architecture, setup, commands, skills, tools, memory, workspace, development, extension and safety guides.
- `certs/`: public root CA certificates, including a full Mozilla bundle for flexible HTTPS endpoints.
- `backups/`: local board backups, ignored by Git.

## Required checks

```powershell
python tools/check.py
```

This is equivalent to `python -m unittest discover -s tests -t . -v` plus
`python -m compileall -q device tools tests`. Use `python tools/check.py --quick`
while iterating on agent, config, HTTP, LLM, persistence or web code.

For ordinary device iterations, use the partial deploy and quick smoke instead
of the full suite; run the full device smoke before release or after changes to
threading, handoff, hardware, recovery or networking:

```powershell
python tools/deploy.py --port <PORT> --changed --no-reset
mpremote connect <PORT> run tools/device_quick_smoke.py
```

Device deployment defaults to `.mpy` and requires the matching compiler:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

For device changes on the reference board, first enter `/repl` if the `esp32>` console is running. Replace `<PORT>` with the board's serial port (for example `COM8`, `/dev/ttyACM0`, or `/dev/cu.usbmodem*`); host tools also accept `HARNESS_PORT` or auto-detect a single USB serial port:

```powershell
python tools/deploy.py --port <PORT> --no-reset
mpremote connect <PORT> run tools/device_smoke.py
mpremote connect <PORT> reset
python tools/console_smoke.py --port <PORT>
```

Do not default to a real LLM request: it needs user credentials and can incur cost.

## Compatibility notes

Target MicroPython 1.29.0 on ESP32-S3 N16R8 with Octal PSRAM and `_mpy=11014`. Foreground device output capture uses `os.dupterm()` and a compliant `io.IOBase` stream; CPython stdout redirection alone is not sufficient. Background workers must not touch `os.dupterm`: they capture `print` through the shared execution runtime, and writes to `sys.stdout` directly are not reliably attributable. The web server uses one background `_thread` with a 32 KB stack; each background job uses another worker thread (default 32 KB stack), so cap concurrency and test thread creation on the real board. Worker start closes idle HTTP/TLS connections and retries with progressively smaller stacks (32/24/16/12/8 KB) because the ESP-IDF internal heap may be too fragmented for 32 KB even when the MicroPython heap looks free. `Exception.__init__` and `socket.getsockname()` are unavailable in this port, so custom exceptions set attributes directly and fixed ports are used on device. On this port built-in modules may not appear in `sys.modules` before import, so tests must accept a missing/restored `time` entry rather than assuming identity. `sys.stdin` is a `TextIOWrapper` whose universal-newline translation turns CR into LF; read `sys.stdin.buffer` to distinguish CRLF/LFCR pairs from intentional empty lines. `str.isalnum()` is not implemented. MicroPython compiles `for i in range(...)` against the builtin even when globals are shadowed, so the executor instruments source (`range` -> `__harness_range`, `while True:` -> `while __harness_check() or True:`) and proxies `time` through `sys.modules`; built-in modules reject `setattr`. `network.STAT_*` values are not 0–5 (`STAT_NO_AP_FOUND=201`, `STAT_CONNECTING=1001`, `STAT_GOT_IP=1010`), and ESP-IDF may keep returning the previous disconnect reason while a new scan is in progress; do not treat `STAT_NO_AP_FOUND`/`STAT_WRONG_PASSWORD` as final before the 5 s settle window in `Network.connect`. Keep all network responses, history, source and output bounded. Update README and relevant docs whenever commands, layout, configuration or workflows change.
