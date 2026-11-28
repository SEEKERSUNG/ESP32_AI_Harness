# ESP32-S3 Harness

> **Language / 语言:** [简体中文](README.md) | **English**

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MicroPython](https://img.shields.io/badge/MicroPython-1.29.0-blue.svg)](https://micropython.org/)
[![Board](https://img.shields.io/badge/board-ESP32--S3%20N16R8-red.svg)](https://www.espressif.com/en/products/socs/esp32-s3)
[![GitHub](https://img.shields.io/badge/GitHub-ESP32__AI__Harness-181717?logo=github)](https://github.com/SEEKERSUNG/ESP32_AI_Harness)

A local MicroPython agent harness that runs entirely on an **ESP32-S3 N16R8 (16 MB flash + 8 MB octal PSRAM)**. The serial console, web console, conversation history, API requests, code review, local execution, skills, memory and remote MCP tools all live on the board. A cloud OpenAI-compatible or Anthropic Messages API is used only for model inference.

> **Security notice:** Generated code runs with the full privileges of the MicroPython process and is **not sandboxed**; it can drive hardware directly with `machine`/`neopixel`. The web console is **plain HTTP** and is intended for trusted LANs only. Auto-run is disabled by default. Read [Safety, recovery and limitations](docs/safety.md) and [Web console](docs/web.md) before exposing the device.

## Contents

- [Why this project](#why-this-project)
- [Features](#features)
- [Model endpoints and search](#model-endpoints-and-search)
- [Hardware and firmware](#hardware-and-firmware)
- [Quick start](#quick-start)
- [Web console](#web-console)
- [Code execution and background jobs](#code-execution-and-background-jobs)
- [Common commands](#common-commands)
- [Project layout](#project-layout)
- [Documentation](#documentation)
- [Development and testing](#development-and-testing)
- [Security boundaries](#security-boundaries)
- [Contributing](#contributing)
- [License](#license)

## Why this project

- **Everything runs on the board** — after deployment, unplug the PC and keep using the device from a browser or serial terminal.
- **Review before execution** — generated Python is shown, confirmed and only then executed. Auto-run is an explicit opt-in.
- **Search follows the model API** — OpenAI-compatible and Anthropic Messages endpoints are supported. In AUTO mode, native provider search is selected automatically; endpoints without server-side search use keyless Bing.
- **Native MicroPython hardware access** — generated code imports `machine`, `neopixel`, `framebuf` and other standard modules directly. There is no global `api` object, peripheral allowlist or `/peripherals` setup step.
- **Full control surface** — serial keeps every capability; the web console covers everyday chat, configuration, history, and a Tasks page for background and scheduled jobs.

## Features

**Interaction and control**

- **Two consoles** — the serial `esp32>` prompt handles natural-language tasks and `/` commands. The built-in web console supports login, streaming chat, stop, `/` commands from the command page, history, Wi-Fi/model/API key/CA/admin-password changes and factory reset.
- **AP provisioning** — on first use or Wi-Fi failure the board starts a WPA2 AP (`ESP32-Harness-XXXX` / `esp32setup`). Connect a phone or laptop to configure Wi-Fi, the model and the admin password.
- **Streaming responses** — SSE streaming by default with live reasoning and content. Thinking levels are off/Low/High/Max; High is the default and is persisted. `show_thinking` is on by default and can be hidden with `/thinking show off`.
- **Persistent context and rewind** — committed messages, compaction summaries and the first user request anchor are written atomically to `/state/conversation.json` and restored after reboot. Compaction does not make the model forget the original task goal; `/clear` resets the anchor. `/undo [N]` (alias `/rewind`) and the web rewind buttons remove conversation context only; they do not undo device side effects.

**Models and tools**

- **Two API protocols with native tool calling** — choose OpenAI-compatible (`chat/completions`) or Anthropic Messages (`/v1/messages`) in setup, `/model` or the web UI. Built-in tools are sent through native function calling (OpenAI `tools` / Anthropic `input_schema`), with an automatic fallback to a textual `tool` block when an endpoint rejects native tools.
- **Built-in tools** — `__skill`, `__workspace`, `__memory`, `__search`, `__ask`, `__http`, `__background`, `__time`, `__schedule`. Model-defined executable tools are no longer loaded.
- **Skills and memory** — skills are Markdown `SKILL.md` instruction packages and are never executed. `MEMORY.md` is always loaded and supports read, append, delete and replace.
- **Public HTTP fetching** — `__http` supports GET/HEAD for public HTTPS pages, JSON APIs and small downloads. Private, loopback, link-local, CGNAT and reserved addresses are rejected before connecting and again on every redirect; the validated DNS address is pinned for the actual connection, and caller headers are dropped on cross-origin redirects.
- **Model questions** — `__ask` lets the model submit a question with up to eight options. The serial console prints numbered options; the web UI renders a question card. The answer is sent as the next user message.
- **Automatic time sync and time tool** — after Wi-Fi connects the harness synchronizes the RTC over NTP, refreshes it every six hours by default and again after a reconnect once due. `__time` reads the board RTC and returns both local time (configurable fixed UTC offset, default UTC+8) and UTC, so the model can answer time/date questions directly. On an explicit user request, `__time action=set` writes the UTC clock (local input is converted first) and `action=timezone` changes the display offset.
- **Remote MCP tools** — Streamable HTTP and stateless protocols, `tools/list`, `tools/call`, pagination, JSON/SSE responses and static headers. Non-read-only tools require confirmation by default.

**Execution and hardware**

- **Confirmation-gated execution** — generated Python is compiled and executed on the device after confirmation. Optional auto-run and auto-feedback are bounded by `max_turns` (default 32).
- **Cooperative timeouts** — the default execution timeout is 20 seconds. `range()`, every `while` condition, `time.sleep()` and explicit `__harness_check()` stop at the deadline or a stop request and return `status=timeout`/`cancelled`; an ordinary timeout does not reboot the board.
- **Multiple background jobs** — prefer foreground execution for short programs. Long-running work can use dedicated worker threads (four by default) while the serial main thread keeps handling chat and web requests. Stop is cooperative; blocking C/SPI calls still wait for the watchdog. Background jobs are rendered on the web Tasks page instead of being inserted into the chat log.
- **Scheduled tasks** — one-time, fixed-interval or daily local-time execution. A prompt task starts an agent turn when due; a code task starts a background job. The model manages them with `__schedule` (create, edit, delete, enable/disable, run now). AI creation, editing, enabling or immediate running of a code task requires AUTO; the web form is confirmed by the administrator. Tasks persist in `/state/schedules.json`, run from the main-thread idle hook when the RTC is valid, and retry failures three times.
- **Native hardware access** — generated code uses MicroPython's `machine` (`Pin`, `SPI`, `I2C`, `UART`, `PWM`, `ADC`, `TouchPad`, `RTC`, `time_pulse_us`), `neopixel`, `framebuf`, `esp` and `esp32` directly. There is no `api` object, pin allowlist or `/peripherals` command. Reserved pins (flash/PSRAM, USB, console UART, strapping) must still be avoided according to the wiring instructions.

**Reliability**

- A 30-second watchdog is the last line of defense. After an abnormal reset, pending code is recovered for review but never executed automatically.
- Bounded history, HTTP responses, streams, generated code and output. The approximate context budget is 200K tokens, with automatic compaction and `/compact`.
- Transient API failures are retried with exponential backoff and `Retry-After` support. Header wait, byte idle and event idle timeouts are tracked separately.

## Model endpoints and search

Any OpenAI-compatible or Anthropic Messages-compatible endpoint can be used as the model backend. Common examples:

| Endpoint | API format | Base URL | Notes |
|---|---|---|---|
| DeepSeek direct (OpenAI protocol) | `openai` | `https://api.deepseek.com` | AUTO uses `deepseek_web_search` |
| DeepSeek direct (Anthropic protocol) | `anthropic` | `https://api.deepseek.com/anthropic` | AUTO uses `deepseek_web_search` |
| OpenCode Go | `openai` or `anthropic` | `https://opencode.ai/inference/go/openai/v1` or `https://opencode.ai/inference/go/anthropic` | Model inference only; AUTO uses Bing |
| Anthropic official | `anthropic` | `https://api.anthropic.com` | AUTO uses `web_search_20250305` |
| OpenAI official | `openai` | `https://api.openai.com/v1` | AUTO uses Responses `web_search` |
| Other compatible endpoint | per endpoint | your URL | OpenAI-compatible uses Bing; Anthropic-compatible tries native search, then Bing |

There are only two search settings:

| Setting | Behavior |
|---|---|
| `auto` | Follow the model API. Anthropic uses Messages API `web_search`; OpenAI official endpoints use Responses API `web_search`; direct DeepSeek endpoints use `deepseek_web_search` (thinking disabled); OpenCode Go and other endpoints that cannot execute provider-side search use Bing. Native failure falls back to Bing. |
| `bing` | Keyless Bing only: RSS first, then HTML. No fallback to another provider. |

AUTO reuses the current model endpoint's `api_key`; no separate search key is required. Search results, answers and cited text are **untrusted data** and may contain prompt injection. The model should treat them as data, not instructions.

## Hardware and firmware

| Item | Requirement |
|---|---|
| Board | ESP32-S3 N16R8 |
| Flash | 16 MB, about 14 MB filesystem |
| PSRAM | 8 MB octal PSRAM, about 8 MB free heap after GC |
| Firmware | MicroPython v1.29.0, `ESP32_GENERIC_S3-SPIRAM_OCT`, `_mpy=11014` |
| Serial | USB serial (CH343/CH340/CP210x and similar), 115200 8N1 |

The classic-ESP32 TLS prewarm, heartbeat, write-ahead journal and worker-restart recovery paths have been removed. The S3 has enough memory to complete each independent TLS handshake reliably with the full harness.

## Quick start

### 1. Get the code

```bash
git clone https://github.com/SEEKERSUNG/ESP32_AI_Harness.git
cd ESP32_AI_Harness
```

### 2. Prepare a host virtual environment

Install the host dependencies in a Python virtual environment so they do not mix with the system Python. Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the remaining commands in the **same terminal with that environment activated**; activate it again after opening a new terminal. Once active, `python`, `mpremote`, and the `mpy-cross` used by the deployer all come from this environment. If PowerShell blocks `Activate.ps1`, use `.venv\Scripts\python -m pip ...`, `.venv\Scripts\python tools/check.py`, and replace `mpremote` with `.venv\Scripts\python -m mpremote`.

### 3. Flash MicroPython first (first use or firmware mismatch)

**`tools/deploy.py` deploys application files only; it does not install MicroPython.** Download the **v1.29.0 `ESP32_GENERIC_S3-SPIRAM_OCT`** image from the [official MicroPython download page](https://micropython.org/download/ESP32_GENERIC_S3/) and flash it as described in [Deployment and setup: updating MicroPython firmware](docs/setup.md#3-更新-micropython-固件). An N16R8 board must use the `SPIRAM_OCT` variant; the regular S3 image cannot use octal PSRAM correctly.

> Flashing firmware erases the entire flash, including existing files and configuration, so back them up first. Skip this step if the matching firmware is already installed; normal Harness upgrades do not require reflashing.

### 4. Host tests

```powershell
python tools/check.py
```

This is equivalent to `python -m unittest discover -s tests -t . -v` plus `python -m compileall -q device tools tests`. Use `python tools/check.py --quick` while iterating.

### 5. Deploy to the ESP32-S3

```powershell
python tools/deploy.py --port COMx
```

`--port` may be omitted: the tool reads `HARNESS_PORT` or auto-detects a single USB serial port. Common Linux ports look like `/dev/ttyACM0`; macOS ports look like `/dev/cu.usbmodem*`.

For a first deploy, or when you explicitly allow clearing the VFS:

```powershell
python tools/deploy.py --port COMx --clean
```

The deployer only writes MicroPython VFS files. It does not erase flash or modify firmware or partitions. It builds and copies `.mpy` files by default and copies `certs/ca-bundle.pem` to `/ca.pem`; use `--source` to deploy source. `--clean` deletes device configuration and skills (the public CA is copied afterwards), so use it only when explicitly allowed.

If the `esp32>` console is running, enter `/repl` and confirm first; the deployer clears the maintenance flag and reboots the board automatically.

### 6. Configure Wi-Fi and the model

```powershell
mpremote connect COMx repl
```

The repository also includes a serial terminal that chunks input on UTF-8 boundaries and rate-limits it, so long Chinese input is not truncated by the UART RX buffer:

```powershell
python tools/serial_console.py --port COMx
```

The deployer automatically copies `certs/ca-bundle.pem` to `/ca.pem` (full deploys, `--clean`, and the first incremental deploy; `--changed` refreshes it when the bundle changes), so manual CA installation is usually unnecessary. Then run:

```text
/setup
```

When `/setup` finds an existing CA, answer `N` to the replace prompt. Exit `serial_console.py` with `:quit` or `:exit`; `Ctrl-C` cancels the current input on the device. Passwords and API keys are echoed by the local terminal. Wi-Fi passwords and API keys are stored in plaintext in the device's `/secrets.json` and are never committed to Git.

OpenAI-compatible example:

```text
Base URL: https://api.deepseek.com
Model:    deepseek-flash
CA:       /ca.pem
```

```text
Base URL: https://opencode.ai/inference/go/openai/v1
Model:    deepseek-v4.1-flash
CA:       /ca.pem
```

Anthropic Messages example (set API format to `anthropic` in `/model`, or choose it in the web settings):

```text
API format: anthropic
Base URL:   https://api.anthropic.com
Model:      claude-<model>
API key:    sk-ant-...
CA:         /ca.pem
```

Both OpenAI-compatible and Anthropic paths send a stable `x-opencode-session: ses_...` header (required by gateways such as OpenCode Go) and an `esp32-harness/0.4` User-Agent. `/clear` rotates the session ID.

The deployer automatically installs `certs/ca-bundle.pem` (the full Mozilla public root bundle) at `/ca.pem`, so changing API endpoints or using multiple public HTTPS services usually requires no CA changes. **If you do not need a custom CA, leave it as is**: there is no need to upload it again in the web UI or serial console. Keep the current path and TLS verification enabled. The web UI places custom CA settings in a collapsed “Advanced” section and only expands a warning when no CA file is present. Private CAs, enterprise networks and self-hosted APIs still require replacing it as described in [CA certificates](certs/README.md); a full deploy overwrites `/ca.pem` with the public bundle, so reinstall a private CA after deployment.

## Web console

The web console is enabled by default and listens on port `80`. Check the address with:

```text
/status
/web
```

With a working STA connection, open `http://<device-ip>/`. On first use or when Wi-Fi fails, connect to the device AP:

```text
SSID: ESP32-Harness-XXXX
Password: esp32setup   # default; change it after setup
URL:  http://192.168.4.1/
```

The first visit asks for an admin password and optionally Wi-Fi and model settings. After login you can chat, run `/` commands from the web command page and change configuration. The UI follows the system light/dark theme and works on phones and desktops. The chat/command navigation stays at the bottom; the Thinking selector and AUTO switch sit next to the composer. When the model calls `__ask`, a question card offers the choices plus a free-form answer. Code, execution output and local history records are collapsed by default; streaming text refreshes every 32 characters by default.

Web and serial share history; only one model turn runs at a time. When changing Wi-Fi, the device starts its AP first; the first save takes roughly 2–5 seconds to scan and associate. A brief page disconnect or a stale error does not necessarily mean failure — check the `Wi-Fi:` line in `/status`. If you forget the admin password, run:

```text
/web reset-password
```

The settings page ends with “Factory reset”: check the confirmation and enter the current admin password. It deletes configuration, secrets, `MEMORY.md`, recovery files, skills, the legacy tool directory, the workspace and conversation state, then reboots. Firmware, boot files and the public CA are kept. The serial equivalent is `/factory-reset`, which requires typing `RESET`.

For the full API, NDJSON events, AP state machine and security boundaries, see [Web console](docs/web.md).

## Code execution and background jobs

Ask the model for a program:

```text
Write a MicroPython program that prints the sum of 1 to 100.
```

After the model returns code:

```text
/code       # review it again
/run        # type y to run in the foreground (serial main thread)
/run bg     # type y to create a background job; repeat to create more
/background # list job IDs, notes, states and elapsed time
/continue 1 # commit job 1's result and continue the conversation
```

Execution results are fed back to the model according to configuration; subsequent fixes still require `/run` confirmation. Auto-feedback turns are bounded by `max_turns` (default 32; change with `/auto turns N` or in the web settings). The default execution timeout is 20 seconds. The model can put `timeout=45` on the same line as the opening code fence to request a longer deadline (maximum `code_timeout_max_s=300`). A timeout returns `status=timeout` and lets the model continue; it does not reboot the board on an ordinary timeout. `range()`, every `while` condition (not only `while True`) and `time.sleep()` are instrumented automatically. Very long `for` loops over other iterables, or one iteration that blocks for many seconds, still need an explicit `__harness_check()`.

Background jobs run on their own worker threads, suitable for long-running work or when the user wants to keep interacting. Prefer the foreground for short programs. Stop is cooperative: it sets a cancellation flag and raises `ExecutionCancelled` at checkpoints. A job blocked inside a C/SPI call may only stop after a reboot. Stop-and-discard additionally drops the result: queued jobs are removed immediately, and a running job is removed when its worker reaches a checkpoint, so it never produces a ready result to continue or discard. `timeout=0`/`infinite` allows a background job to run without a harness deadline, but does not suspend the 30-second watchdog.

Scheduled tasks are managed by the `__schedule` tool or the web Tasks page. A prompt task runs a normal agent turn on the main thread when due, and generated code still follows AUTO/confirmation; a code task starts a background job. AI-created or edited code tasks require AUTO, while the web form is confirmed by the administrator. One-time tasks survive reboot, recurring tasks missed over downtime run once, and nothing runs while the RTC is invalid. See [Command reference](docs/commands.md) for details.

To rewind context, `/undo` removes the most recent user message and everything after it; `/undo 2` removes the second-most-recent user message and everything after it. The web button next to each real user message does the same; the top bar no longer duplicates it. Rewind only changes conversation context; it does not revert files, GPIO, network or other device side effects.

Context monitoring and compaction:

```text
/context    # approximate tokens, message count and auto-compaction threshold
/compact    # compact older history now, keeping recent messages
/summary    # show the current summary
```

Automatic compaction starts at about 150000 tokens; `context_tokens` is 200000. Both are configurable in Settings → Harness behavior (the threshold must stay below the context length). These are approximate on-device budgets, not a provider tokenizer's exact count.

## Common commands

| Command | Description |
|---|---|
| `/help` | Show built-in help |
| `/status` | Redacted config, memory, network, history, skills, memory and workspace status |
| `/setup` | Configure Wi-Fi, model and optional CA |
| `/model` | Configure API format, Base URL, model, API key, CA and TLS verification |
| `/thinking [level]` | Show or set thinking: disable/low/high/max (default high) |
| `/thinking show on\|off` | Show or hide reasoning text |
| `/auto [on\|off]` | Auto-run generated code and auto-feedback |
| `/auto turns N` | Set the maximum auto-feedback turns (default 32, range 1–50) |
| `/run` / `/run bg [note]` / `/continue [id]` | Run in the foreground, create a background job, or continue a result |
| `/background [list\|status ID\|stop [ID]\|stopdiscard [ID]\|note ID TEXT\|discard ID]` | Inspect and manage background jobs; `stopdiscard` stops and drops the result |
| `/schedule [list\|status ID\|run ID\|enable ID\|disable ID\|delete ID]` | Inspect and manage scheduled tasks |
| `/undo [N]` / `/rewind [N]` | Remove the Nth most recent user message and everything after it |
| `/code` / `/discard [ID]` | Review or discard pending code or a background result |
| `/compact` / `/context` / `/summary` | Compact, estimate and inspect context |
| `/memory` | Show the always-loaded `/MEMORY.md` |
| `/skills` / `/skill-show` / `/skill-delete` | Manage skills |
| `/workspace` | Inspect and manage the shared workspace |
| `/search <query>` | Run a web search manually |
| `/search provider auto\|bing` | Choose the search provider |
| `/search on\|off` | Enable or disable web search |
| `/http get <url>` | Fetch public HTTPS text or JSON |
| `/http head <url>` | Fetch response headers only |
| `/http download <url> [path]` | Download to `/workspace` |
| `/mcp` | Inspect, add, refresh and manage remote MCP servers and tools |
| `/web` / `/web reset-password` | Show web status / reset the admin password |
| `/factory-reset` | Delete user data and reboot (requires typing `RESET`) |

See the [command reference](docs/commands.md) for the full command set, web command limits and interaction rules.

### MCP example

MCP uses remote HTTP/SSE servers; no subprocess runs on the device:

```text
/mcp add github https://api.githubcopilot.com/mcp/ Authorization=Bearer <token>
/mcp refresh
/mcp tools
/mcp auto github on
```

The web “Settings → MCP” page provides an equivalent server JSON editor and refresh button. Tools appear in the system prompt as `mcp__github__<tool>` and are called through native tool calling (or the textual `tool` block fallback). Only tools with `readOnlyHint` are confirmation-free by default; other tools prompt on serial or are rejected on web unless `auto` is enabled for that server.

Limitations: no stdio MCP (the device cannot start subprocesses), OAuth browser authorization, sampling/roots/elicitation. Servers that require OAuth need a static token or a host-side proxy. See [Remote MCP tools](docs/mcp.md).

## Project layout

```text
device/       MicroPython code deployed to the device VFS
tests/        CPython unit tests (fake device/cloud services)
tools/        Deployment, serial terminal, smoke tests and maintenance tools
docs/         Architecture, usage, extension and security documentation
certs/        Mozilla CA bundle (installed automatically during deployment)
```

## Documentation

| Document | Content |
|---|---|
| [Documentation index](docs/README.md) | Reading order by usage, implementation, extension and security |
| [Architecture and execution flow](docs/architecture.md) | Thread model, sessions, context budget, execution flow |
| [Deployment and setup](docs/setup.md) | Firmware, deployment, serial, Wi-Fi/model/CA configuration |
| [Command reference](docs/commands.md) | Serial and web commands, confirmation rules, typical flows |
| [Web console and AP provisioning](docs/web.md) | HTTP API, NDJSON, AP state machine, security boundaries |
| [Skills](docs/skills.md) | Markdown instruction packages, progressive loading |
| [MEMORY.md](docs/memory.md) | Always-loaded project context |
| [OpenAI-compatible API](docs/openai.md) | Chat Completions mapping, thinking, streaming tool calls and limits |
| [Anthropic Messages API](docs/anthropic.md) | Protocol mapping, thinking, headers and limits |
| [Remote MCP tools](docs/mcp.md) | Streamable HTTP, configuration, confirmation and limits |
| [Workspace](docs/workspace.md) | Persistent files and temporary storage |
| [Hardware access for generated code](docs/hardware.md) | `machine`/`neopixel`/`framebuf`, GPIO/SPI/I2C/UART/PWM/ADC/touch/RTC and an ST7735 example |
| [Development, testing and debugging](docs/development.md) | Test matrix, device conventions, API debugging |
| [Adding peripherals or model providers](docs/extending.md) | Extension points |
| [Safety, recovery and limitations](docs/safety.md) | Threat model, watchdog, secrets and device boundaries |
| [Security policy](SECURITY.md) | Vulnerability reporting and deployment requirements |
| [Changelog](CHANGELOG.md) | Version history |

> Detailed documentation is currently written in Chinese. The English README is the primary English entry point; issues and pull requests that add English translations are welcome.

## Development and testing

```powershell
python tools/check.py
```

For routine changes, use incremental deployment and the quick smoke test. Run the full device regression only for changes involving threading, handoff, hardware, recovery or before a release:

```powershell
python tools/deploy.py --port COMx --changed --no-reset
mpremote connect COMx run tools/device_quick_smoke.py
```

```powershell
python tools/deploy.py --port COMx --no-reset
mpremote connect COMx run tools/device_smoke.py
mpremote connect COMx reset
python tools/console_smoke.py --port COMx
```

Expected markers: `DEVICE_QUICK_SMOKE_OK`, `DEVICE_SMOKE_OK`, `CONSOLE_SMOKE_OK`. The repository contains no real API keys, Wi-Fi passwords or device configuration; tests use fake services and placeholder credentials. Tests do not send real LLM requests by default because they require user credentials and may incur cost.

See [Development, testing and debugging](docs/development.md) for the test matrix, MicroPython compatibility notes and debugging workflow.

## Security boundaries

- **Dynamic code is not a sandbox** — `/run` and web-triggered code have the full privileges of the MicroPython process. Confirmation reduces accidental execution; it is not a security boundary.
- **The web console is plain HTTP** — use it only on a trusted LAN and never port-forward it to the public Internet. Admin passwords, cookies, conversations and configuration can be sniffed on the LAN.
- **Secrets live on the device** — `/secrets.json` and `/mcp.json` are plaintext files (MCP headers may contain tokens) and can be read over serial or with physical access. `/status`, `/api/status`, `/api/config` and `/api/mcp` return only `_set` booleans or header names.
- **Conversations are persisted** — `/state/conversation.json` stores committed messages and summaries that may contain sensitive user text. `/state/background.json` stores up to `background_max_records` (default 8) bounded background records and output until continued or discarded. Persistence can be disabled in the web settings; `/clear` and factory reset delete them.
- **Native hardware is directly accessible** — generated code can drive real pins with `machine`/`neopixel`. Wrong wiring, voltage or use of flash/PSRAM, USB, console UART or strapping pins can damage peripherals or prevent the board from booting normally; verify the wiring before confirming execution.
- **Auto-run is off by default** — `auto_run` requires an explicit opt-in and confirmation and can cause multiple billed requests; MCP `auto` requires confirmation as well.
- **Timeouts are not a hard sandbox** — `range()`, every `while` condition, `time.sleep()` and `__harness_check()` can stop cooperatively. C/SPI-level blocking calls, very long `for` iterations, or one iteration that blocks for many seconds may still be reset by the 30-second watchdog. The Stop button stops receiving and subsequent turns; it cannot force-kill Python code that has already started.

See [Safety, recovery and limitations](docs/safety.md) for the full threat model and recovery behavior.

## Contributing

Issues and pull requests are welcome. Before submitting:

1. Run `python tools/check.py`.
2. Update the README and relevant docs when commands, configuration, layout or workflows change.
3. Never commit real `config.json`, `secrets.json`, API keys, Wi-Fi passwords, firmware binaries, backups or serial logs containing secrets.
4. Report security vulnerabilities privately as described in [SECURITY.md](SECURITY.md); do not paste credentials in a public issue.

## License

Released under the [MIT License](LICENSE).
