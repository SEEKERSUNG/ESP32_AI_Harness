# ESP32-S3 Harness

> **语言 / Language:** **简体中文** | [English](README.en.md)

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MicroPython](https://img.shields.io/badge/MicroPython-1.29.0-blue.svg)](https://micropython.org/)
[![Board](https://img.shields.io/badge/board-ESP32--S3%20N16R8-red.svg)](https://www.espressif.com/en/products/socs/esp32-s3)
[![GitHub](https://img.shields.io/badge/GitHub-ESP32__AI__Harness-181717?logo=github)](https://github.com/SEEKERSUNG/ESP32_AI_Harness)

在 **ESP32-S3 N16R8（16 MB Flash + 8 MB Octal PSRAM）** 上运行的本地 MicroPython Agent Harness。串口和网页控制台、对话历史、API 请求、代码确认、本地执行、技能、记忆和 MCP 远程工具全部在开发板上完成；云端 OpenAI 兼容 API 或 Anthropic Messages API 只负责模型推理。

> **安全提示**：生成代码拥有完整 MicroPython 进程权限，不是沙箱，并可直接使用 `machine`/`neopixel` 操作硬件。Web 控制台是**纯 HTTP**，只适用于可信局域网。自动执行默认关闭。公开部署前请阅读 [安全、恢复与限制](docs/safety.md) 和 [Web 控制台](docs/web.md)。

## 目录

- [为什么是它](#为什么是它)
- [特性](#特性)
- [模型端点与搜索](#模型端点与搜索)
- [硬件与固件](#硬件与固件)
- [快速开始](#快速开始)
- [Web 控制台](#web-控制台)
- [代码执行与后台任务](#代码执行与后台任务)
- [常用命令](#常用命令)
- [项目结构](#项目结构)
- [文档](#文档)
- [开发与测试](#开发与测试)
- [安全边界](#安全边界)
- [贡献](#贡献)
- [许可证](#许可证)

## 为什么是它

- **整机运行，不依赖 PC 守护进程**：部署完成后，拔掉电脑也能在局域网内通过浏览器或串口使用。
- **可审查执行**：模型生成的 Python 默认先展示、再确认、后执行；自动执行是显式 opt-in。
- **协议跟随模型**：OpenAI 兼容与 Anthropic Messages 双协议；AUTO 搜索自动跟随当前模型 API 选择原生搜索，不支持服务端搜索的端点使用免 Key 的 Bing。
- **原生 MicroPython 硬件**：生成代码直接 `import machine`、`neopixel`、`framebuf` 等模块，使用标准 MicroPython 外设库；没有全局 `api` 对象、外设 allowlist 或 `/peripherals` 前置配置。
- **完整控制面**：串口保留全部能力；网页提供日常对话、配置、历史和后台任务管理。

## 特性

**交互与控制**

- **双控制台**：串口 `esp32>` 自然语言任务和 `/` 命令；设备内置 Web 控制台，可登录对话、停止流式回答、执行命令页 `/` 命令、查看历史、修改 Wi-Fi/模型/API Key/CA/管理密码和恢复出厂设置。
- **AP 配网**：首次使用或 Wi-Fi 连接失败时自动开启 WPA2 AP（`ESP32-Harness-XXXX` / `esp32setup`），手机或电脑连接后即可设置 Wi-Fi、模型和管理密码。
- **流式响应**：默认 SSE 流式，reasoning 和正文实时输出；思考级别为关闭/Low/High/Max，默认 High 并持久化。`show_thinking` 默认开启，可用 `/thinking show off` 隐藏。
- **上下文持久化与回退**：已提交对话、压缩摘要和首个用户请求锚点原子写入 `/state/conversation.json`，复位后自动恢复；压缩不会让模型忘记最初的任务目标，`/clear` 才会重置锚点。`/undo [N]`（别名 `/rewind`）和网页回退只删除对话上下文，不回滚设备副作用。

**模型与工具**

- **双 API 协议与原生工具调用**：设置页、`/setup` 和 `/model` 可选择 OpenAI 兼容（`chat/completions`）或 Anthropic Messages（`/v1/messages`）。内置工具优先通过原生 function calling 发送（OpenAI `tools` / Anthropic `input_schema`），端点拒绝原生工具时自动回退到文本 `tool` 块。
- **内置工具**：`__skill`、`__workspace`、`__memory`、`__search`、`__ask`、`__http`、`__background`；不再加载模型自定义可执行 Tool。
- **Skills 与 Memory**：Skill 是 Markdown `SKILL.md` 指令包，永不执行；`MEMORY.md` 始终加载，支持读取、追加、删除和替换。
- **公共网络抓取**：`__http` 支持 GET/HEAD 公共 HTTPS 页面、JSON API 和小文件下载；私有、回环、链路本地、CGNAT 和保留地址在连接前及每次重定向前都会被拒绝。
- **模型提问**：模型可通过 `__ask` 提交问题和最多 8 个选项；串口打印编号选项，网页显示问题卡，回答作为下一条用户消息发送。
- **MCP 远程工具**：支持 Streamable HTTP 和无状态协议，兼容 `tools/list`、`tools/call`、分页、JSON/SSE 响应和静态 Header；非只读工具默认需要确认。

**执行与硬件**

- **确认后执行**：模型生成的 Python 默认经确认后在设备上 `compile()` / `exec()`；可选自动执行和自动反馈，受 `max_turns` 限制（默认 32）。
- **协作式超时**：默认 20 秒执行超时，`range()`、`time.sleep()`、`while True` 和显式 `__harness_check()` 会在截止时间或停止请求处停止并返回 `status=timeout`/`cancelled`，不会因普通超时重启设备。
- **多后台任务**：短程序优先前台执行；长时间任务可在独立 worker 线程运行（默认最多 4 个），串口主线程仍可继续对话或处理网页请求。停止是协作式的，阻塞 C/SPI 调用仍只能等看门狗。
- **原生硬件访问**：生成代码直接使用 MicroPython 的 `machine`（`Pin`、`SPI`、`I2C`、`UART`、`PWM`、`ADC`、`TouchPad`、`RTC`、`time_pulse_us`）、`neopixel`、`framebuf`、`esp` 和 `esp32`；没有 `api` 对象、引脚 allowlist 或 `/peripherals` 命令。保留引脚（Flash/PSRAM、USB、控制台 UART、strapping）仍需按接线说明避开。

**可靠性**

- 30 秒看门狗作为最后防线；异常复位后恢复待审代码但不自动执行。
- 有界历史、HTTP 响应、流式正文、生成代码和执行输出；约 200K token 近似上下文预算，支持自动压缩和 `/compact`。
- API 瞬时失败按指数退避重试，支持 `Retry-After`；响应头、字节空闲和事件空闲分别计时。

## 模型端点与搜索

任何 OpenAI 兼容或 Anthropic Messages 兼容端点都可以作为模型后端。以下是常见配置示例：

| 场景 | API format | Base URL | 说明 |
|---|---|---|---|
| DeepSeek 直连（OpenAI 协议） | `openai` | `https://api.deepseek.com` | AUTO 使用 `deepseek_web_search` |
| DeepSeek 直连（Anthropic 协议） | `anthropic` | `https://api.deepseek.com/anthropic` | AUTO 使用 `deepseek_web_search` |
| OpenCode Go | `openai` 或 `anthropic` | `https://opencode.ai/inference/go/openai/v1` 或 `https://opencode.ai/inference/go/anthropic` | 只提供模型推理，AUTO 使用 Bing |
| Anthropic 官方 | `anthropic` | `https://api.anthropic.com` | AUTO 使用 `web_search_20250305` |
| OpenAI 官方 | `openai` | `https://api.openai.com/v1` | AUTO 使用 Responses `web_search` |
| 其他兼容端点 | 按端点选择 | 自填 | OpenAI 兼容端点用 Bing；Anthropic 兼容端点先尝试原生搜索，失败后回退 Bing |

搜索设置只有两种：

| 配置 | 行为 |
|---|---|
| `auto` | 跟随模型 API：Anthropic 使用 Messages API `web_search`；OpenAI 官方端点使用 Responses API `web_search`；DeepSeek 直连端点使用 `deepseek_web_search`（显式关闭 thinking）；OpenCode Go 和其他不执行服务端搜索的兼容端点使用 Bing。原生搜索失败自动回退 Bing。 |
| `bing` | 只使用免 Key 的 Bing：优先 RSS，失败后解析 HTML；不回退到其他提供方。 |

AUTO 会复用当前模型端点的 `api_key`，不需要单独的搜索 Key。搜索结果、答案和引用文本都是**不可信数据**，可能包含提示注入，模型应把它们当作数据而非指令。

## 硬件与固件

| 项目 | 要求 |
|---|---|
| 开发板 | ESP32-S3 N16R8 |
| Flash | 16 MB，约 14 MB 文件系统 |
| PSRAM | 8 MB Octal PSRAM，GC 后约 8 MB 空闲堆 |
| 固件 | MicroPython v1.29.0，`ESP32_GENERIC_S3-SPIRAM_OCT`，`_mpy=11014` |
| 串口 | USB 串口（CH343/CH340/CP210x 等均可），115200 8N1 |

经典 ESP32 上的 TLS 预热、心跳、写前日志和 worker 重启恢复路径已移除。S3 的内存足以在完整 Harness 中稳定完成每次独立 TLS 握手。

## 快速开始

### 1. 获取代码

```bash
git clone https://github.com/SEEKERSUNG/ESP32_AI_Harness.git
cd ESP32_AI_Harness
```

### 2. 主机环境

需要 Python 3、`mpremote`、`pyserial`，以及与设备固件匹配的 `mpy-cross`：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Linux/macOS 使用 `source .venv/bin/activate`，然后执行 `python -m pip install -r requirements.txt`。

### 3. 主机测试

```powershell
python tools/check.py
```

等价于 `python -m unittest discover -s tests -t . -v` 加 `python -m compileall -q device tools tests`。日常迭代可使用 `python tools/check.py --quick`。

### 4. 部署到 ESP32-S3

```powershell
python tools/deploy.py --port COMx
```

`--port` 可省略：工具会读取 `HARNESS_PORT`，或自动选择唯一的 USB 串口。Linux 常见 `/dev/ttyACM0`，macOS 常见 `/dev/cu.usbmodem*`。

首次部署或明确允许清空 VFS 时：

```powershell
python tools/deploy.py --port COMx --clean
```

部署器只操作 MicroPython VFS，不擦除 Flash、不修改固件或分区。默认构建并复制 `.mpy`，同时把 `certs/ca-bundle.pem` 复制为 `/ca.pem`；也可以使用 `--source` 部署源码。`--clean` 会删除设备配置和技能（公共 CA 随后重新复制），只应在明确允许时使用。

如果设备已经运行 `esp32>` 控制台，先执行 `/repl` 并确认，部署器会自动清除维护标志并复位。

### 5. 配置 Wi-Fi 和模型

```powershell
mpremote connect COMx repl
```

也可以使用仓库自带的串口终端，它按 UTF-8 字符边界分块限速发送，长中文输入不会被 UART RX 缓冲截断：

```powershell
python tools/serial_console.py --port COMx
```

部署器会自动把 `certs/ca-bundle.pem` 复制为设备 `/ca.pem`（完整部署、`--clean` 和首次增量部署；`--changed` 在 bundle 内容变化时更新），通常不需要手动安装 CA。然后执行：

```text
/setup
```

`/setup` 检测到已有 CA 时，替换提示回答 `N`。`serial_console.py` 用 `:quit` 或 `:exit` 退出，`Ctrl-C` 取消设备当前输入；输入密码和 API Key 时注意本地终端仍会回显。Wi-Fi 密码和 API Key 以明文保存在设备 `/secrets.json`，不会进入 Git。

OpenAI 兼容端点示例：

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

Anthropic Messages 示例（在 `/model` 中先把 API format 设为 `anthropic`，或在网页设置页选择）：

```text
API format: anthropic
Base URL:   https://api.anthropic.com
Model:      claude-<model>
API key:    sk-ant-...
CA:         /ca.pem
```

OpenAI 兼容路径和 Anthropic 路径都会发送稳定的 `x-opencode-session: ses_...` 会话头（兼容 OpenCode Go 等网关），并发送 `esp32-harness/0.4` User-Agent；`/clear` 时轮换会话 ID。

部署器会自动安装 `certs/ca-bundle.pem`（Mozilla 全量公共根证书）到 `/ca.pem`，因此更换 API 地址或访问多个公共 HTTPS 服务时通常无需重新配置。**不想自定义 CA 时保持原样即可**：无需在 Web 或串口重复上传；保持当前路径和 TLS 验证开启。Web 将自定义 CA 放在默认收起的“高级”区域，检测不到 CA 文件时才自动展开提醒。私有 CA、企业内网或自建 API 仍需按 [CA 证书说明](certs/README.md) 替换；完整部署会用公共 bundle 覆盖 `/ca.pem`，私有 CA 请在部署后重新安装。

## Web 控制台

Web 控制台默认启用，监听 `80` 端口。启动后查看地址：

```text
/status
/web
```

正常 STA 连接时打开 `http://<设备IP>/`；首次使用或 Wi-Fi 失败时连接设备 AP：

```text
SSID: ESP32-Harness-XXXX
密码: esp32setup   # 默认值，建议设置完成后修改
URL:  http://192.168.4.1/
```

首次打开会要求设置管理密码，可选填写 Wi-Fi 和模型。之后登录即可对话、执行网页命令页的 `/` 命令和修改配置。页面跟随系统浅色/深色主题，适配手机和桌面；对话/命令导航栏固定在页面底部；Thinking 下拉框和 AUTO 开关位于对话框旁。模型调用 `__ask` 时会显示问题卡，可点选项或直接输入自定义回答。代码、执行输出和历史本地记录默认折叠；流式回答默认每 32 字符刷新一次。

网页与串口共享历史；同一时间只允许一个模型回合。修改 Wi-Fi 时设备会先开 AP，首次保存后约 2–5 秒完成扫描/关联；页面短暂断线或短暂显示旧错误都不等于失败，以 `/status` 的 `Wi-Fi:` 行为准。忘记管理密码时，在串口执行：

```text
/web reset-password
```

设置页末尾提供“恢复出厂设置”：需勾选确认并输入当前管理密码，删除配置、秘密、`MEMORY.md`、recovery 文件、Skill、旧版 Tool 目录、Workspace 和对话状态后重启；固件、启动文件和公共 CA 保留。串口等价命令是 `/factory-reset`，需要输入 `RESET` 二次确认。

完整说明、API、NDJSON 事件、AP 状态机和安全边界见 [Web 控制台](docs/web.md)。

## 代码执行与后台任务

用自然语言让模型写一个程序：

```text
写一个 MicroPython 程序，计算 1 到 100 的和并打印结果
```

模型返回代码后：

```text
/code       # 再次查看
/run        # 输入 y 后在前台（串口主线程）执行
/run bg     # 输入 y 后创建后台任务；可重复生成/运行多个任务
/background # 查看所有后台任务的编号、备注、状态和耗时
/continue 1 # 提交 1 号任务结果并继续对话
```

执行结果按配置自动反馈给模型；后续修复代码仍需 `/run` 确认，自动反馈轮数由 `max_turns` 限制（默认 32，可用 `/auto turns N` 或网页设置调整）。执行超时默认 20 秒，模型可以在代码块开围栏同一行写 `timeout=45` 选择更长的截止时间（上限 `code_timeout_max_s=300`）。超时后 Harness 返回 `status=timeout` 并让模型继续，不会因为普通超时重启设备；长循环应调用 `__harness_check()`，`range()`、`time.sleep()` 和 `while True` 已自动插桩。

后台任务在自己的 worker 线程运行，适合长时间采集或需要与用户继续交互的场景；短程序优先使用前台。停止是协作式的：只设置取消标志，在检查点抛出 `ExecutionCancelled`，阻塞在 C/SPI 调用中的任务可能只能重启设备。`timeout=0`/`infinite` 允许后台无截止时间运行，但不暂停 30 秒看门狗。

需要回退上下文时，`/undo` 删除最近一条用户消息及其后的全部回复，`/undo 2` 删除倒数第二条用户消息及其后内容；网页每条真实用户消息旁的回退按钮提供相同能力，顶栏不再重复放置回退按钮。

上下文监控与压缩：

```text
/context    # 估算 token、历史消息数和自动压缩阈值
/compact    # 立即压缩较早历史，保留最近消息
/summary    # 查看当前摘要
```

默认在约 150000 token 时自动压缩，`context_tokens` 为 200000；这些是设备端的近似预算，不是供应商 tokenizer 的精确计数。

## 常用命令

| 命令 | 说明 |
|---|---|
| `/help` | 显示内置帮助 |
| `/status` | 脱敏配置、内存、网络、历史、Skill、Memory 和工作区状态 |
| `/setup` | 配置 Wi-Fi、模型和可选 CA |
| `/model` | 配置 API 协议、Base URL、模型、API Key、CA 和 TLS 验证 |
| `/thinking [level]` | 查看或设置 thinking：disable/low/high/max（默认 high） |
| `/thinking show on\|off` | 显示或隐藏 reasoning 文本 |
| `/auto [on\|off]` | 自动运行生成代码并自动反馈 |
| `/auto turns N` | 设置自动反馈最大轮数（默认 32，范围 1–50） |
| `/run` / `/run bg [note]` / `/continue [id]` | 前台执行、创建后台任务，或继续某个后台结果 |
| `/background [list\|status ID\|stop [ID]\|note ID TEXT\|discard ID]` | 查看和管理多个后台任务 |
| `/undo [N]` / `/rewind [N]` | 回退第 N 条最近用户消息及其后的全部上下文 |
| `/code` / `/discard [ID]` | 查看或丢弃待审代码、后台结果 |
| `/compact` / `/context` / `/summary` | 压缩、估算和查看上下文 |
| `/memory` | 查看始终加载的 `/MEMORY.md` |
| `/skills` / `/skill-show` / `/skill-delete` | 管理 Skill |
| `/workspace` | 查看和管理共享工作区 |
| `/search <query>` | 手动调用 Web 搜索 |
| `/search provider auto\|bing` | 选择搜索提供方 |
| `/search on\|off` | 启用/禁用 Web 搜索 |
| `/http get <url>` | 抓取公共 HTTPS 文本/JSON |
| `/http head <url>` | 只取响应头 |
| `/http download <url> [path]` | 下载到 `/workspace` |
| `/mcp` | 查看、添加、刷新和管理 MCP 远程服务器与工具 |
| `/web` / `/web reset-password` | 查看 Web 状态 / 重置管理密码 |
| `/factory-reset` | 删除用户数据并重启（需输入 `RESET`） |

完整命令、网页命令限制和交互规则见 [命令手册](docs/commands.md)。

### MCP 示例

MCP 使用远程 HTTP/SSE 服务器，不需要在设备上运行子进程：

```text
/mcp add github https://api.githubcopilot.com/mcp/ Authorization=Bearer <token>
/mcp refresh
/mcp tools
/mcp auto github on
```

网页“设置 → MCP”提供等价的服务器 JSON 编辑和刷新按钮。工具会以 `mcp__github__<tool>` 出现在系统提示词中，模型通过原生工具调用（或兼容的文本 `tool` 块）调用。默认只有 `readOnlyHint` 工具免确认，其他工具在串口询问、网页拒绝，除非为该服务器开启 `auto`。

限制：不支持 stdio MCP（设备不能启动子进程）、OAuth 浏览器授权、sampling/roots/elicitation；需要 OAuth 的服务器请改用静态 Token 或先在主机侧代理。详见 [MCP 远程工具](docs/mcp.md)。

## 项目结构

```text
device/       部署到设备 VFS 的 MicroPython 代码
tests/        CPython 单元测试（Fake 设备/云端服务）
tools/        部署、串口终端、烟雾测试和维护工具
docs/         架构、使用、扩展和安全文档
certs/        Mozilla 全量 CA bundle（部署时自动安装）
```

## 文档

| 文档 | 内容 |
|---|---|
| [文档索引](docs/README.md) | 按使用、实现、扩展和安全分类的阅读顺序 |
| [架构与执行时序](docs/architecture.md) | 线程模型、会话、上下文预算、执行流程 |
| [部署和初始化](docs/setup.md) | 固件、部署、串口、Wi-Fi/模型/CA 配置 |
| [命令手册](docs/commands.md) | 串口和网页命令、确认规则、典型流程 |
| [Web 控制台与 AP 配网](docs/web.md) | HTTP API、NDJSON、AP 状态机、安全边界 |
| [Skills](docs/skills.md) | Markdown 指令包、渐进式加载 |
| [MEMORY.md](docs/memory.md) | 始终加载的项目上下文 |
| [Anthropic Messages API](docs/anthropic.md) | 协议映射、thinking、Header 和限制 |
| [MCP 远程工具](docs/mcp.md) | Streamable HTTP、配置、确认和限制 |
| [Workspace 工作区](docs/workspace.md) | 持久资料和临时文件 |
| [生成代码的硬件访问](docs/hardware.md) | `machine`/`neopixel`/`framebuf`、GPIO/SPI/I2C/UART/PWM/ADC/触摸/RTC 和 ST7735 示例 |
| [开发、测试和调试](docs/development.md) | 测试矩阵、设备约定、API 调试 |
| [增加外设或模型供应商](docs/extending.md) | 扩展点 |
| [安全、恢复与限制](docs/safety.md) | 威胁模型、看门狗、秘密和设备边界 |
| [安全策略](SECURITY.md) | 漏洞报告方式和部署安全要求 |
| [变更记录](CHANGELOG.md) | 版本历史 |

## 开发与测试

```powershell
python tools/check.py
```

日常小改动使用增量部署和快速烟雾，完整设备回归只在涉及线程、handoff、硬件、恢复或发布前运行：

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

预期标志：`DEVICE_QUICK_SMOKE_OK`、`DEVICE_SMOKE_OK`、`CONSOLE_SMOKE_OK`。仓库不包含真实 API Key、Wi-Fi 密码或设备配置；测试使用 Fake 服务和占位凭据。默认不发送真实 LLM 请求，因为它需要用户凭据并可能产生费用。

更多测试矩阵、MicroPython 兼容注意事项和调试流程见 [开发、测试和调试](docs/development.md)。

## 安全边界

- **动态代码不是沙箱**：`/run` 和网页执行的代码拥有当前 MicroPython 进程权限，确认机制只用于避免误操作。
- **Web 控制台是纯 HTTP**：只用于可信局域网，绝不端口映射到公网。管理密码、Cookie、对话和配置在局域网内可被嗅探。
- **秘密保存在设备上**：`/secrets.json` 和 `/mcp.json` 是明文文件（MCP 的 Header 可能含 Token），串口或物理访问可读取；`/status`、`/api/status`、`/api/config` 和 `/api/mcp` 只返回 `_set` 布尔或 Header 名称。
- **对话会落盘**：`/state/conversation.json` 保存已提交的对话和摘要，可能包含用户粘贴的敏感文本；`/state/background.json` 会保存最多 `background_max_records`（默认 8）条有界后台记录和输出，直到继续/丢弃。可用网页设置关闭持久化，`/clear` 和恢复出厂设置会删除。
- **原生硬件直接可访问**：生成代码可以使用 `machine`/`neopixel` 操作真实引脚，错误接线、错误电压或误用 Flash/PSRAM、USB、控制台 UART、strapping 引脚可能损坏外设或导致设备无法正常启动；确认执行前请核对接线。
- **自动模式默认关闭**：`auto_run` 需要显式开启和二次确认，会产生多次计费请求；MCP 的 `auto` 同样需要确认。
- **超时不是硬沙箱**：`range()`、`time.sleep()`、`while True` 和 `__harness_check()` 可协作停止；不调用任何钩子的 C 级阻塞或复杂 `while` 条件仍可能由 30 秒看门狗复位。停止按钮只停止接收和后续回合，不能强制终止已经开始的 Python 执行。

完整威胁模型和恢复行为见 [安全、恢复与限制](docs/safety.md)。

## 贡献

欢迎提交 Issue 和 Pull Request。提交前请：

1. 运行 `python tools/check.py`；
2. 修改命令、配置、布局或工作流时同步更新 README 和相关文档；
3. 不要提交真实 `config.json`、`secrets.json`、API Key、Wi-Fi 密码、固件二进制、备份或含敏感信息的串口日志；
4. 安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告，不要在公开 Issue 中粘贴凭据。

## 许可证

本项目使用 [MIT License](LICENSE)。
