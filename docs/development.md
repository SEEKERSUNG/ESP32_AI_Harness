# 开发、测试和调试

## 仓库布局

- `device/`：部署到 MicroPython 根目录的代码（`llm.py`/`llm_anthropic.py`、`mcp.py`、`conversation.py`、`background.py`、`executor.py` 等）；
- `tests/`：CPython 单元测试，设备服务使用 Fake/Mock；
- `tools/deploy.py`：构建 `.mpy`、复制公共 CA 并操作 VFS 的部署工具；
- `tools/device_smoke.py`：真实 MicroPython 模块、Web 线程、HTTP、handoff 与 Agent 闭环测试；还包括原生 `machine` 导入、HTTP 请求总超时、执行超时、持久对话和 MCP 存储；
- `tools/serial_console.py`：简单交互式 UTF-8 串口终端，按字符边界限速发送，适合长中文输入和通用串口调试；
- `tools/console_smoke.py`、`input_smoke.py`：串口控制台和 UTF-8 输入测试；
- `tools/diagnose_deepseek.py`：维护模式下分阶段诊断 Wi-Fi/DNS/TCP/TLS/HTTP；
- `docs/`：架构、使用和扩展文档。

## Skills、内置工具与 Memory 测试

新增或修改这些系统时至少覆盖：

- Skill 的 `SKILL.md` frontmatter、保存、读取、删除和长度限制；
- `tool` 调用循环、内置工具结果回到模型、`max_tool_calls`（默认 16）上限；
- 原生 function calling：OpenAI `tools` 定义与 `tool_calls` 累积、Anthropic `input_schema` 与 `tool_use`/`input_json_delta`、assistant/tool 消息转换、空正文但有效工具调用、端点拒绝原生工具后的文本回退、`/native off`；
- 内置工具协议容错：`tool_call`/`arguments` 别名、字符串 `args`、未知工具提示、动作与代码块混用/空代码块/超长代码的 `max_format_retries` 修复循环；Skill 头或描述校验错误带针对性提示，并至少重试 2 次；
- 原生硬件：生成代码可直接 `import machine`，使用 `Pin`/`SPI`/`I2C`/`UART`/`PWM`/`ADC`/`TouchPad`/`RTC`/`time_pulse_us` 和 `neopixel`；执行器不再注入 `api`；
- `skill` 提案解析、保存确认和本地事件；
- `MEMORY.md` 创建、追加、删除、替换、清空和始终加载；
- 自动模式：`auto_run` 只执行新生成代码，自动反馈循环受 `max_turns` 限制（默认 32）；`/auto turns N` 和网页设置可调整；
- `__skill` 内置工具：`list`/`read` 按需读取 Skill 目录和正文，`delete` 在用户明确要求时删除 Skill 目录；
- `__workspace` 内置工具：路径穿越、容量/数量限制、tmp 启动清理；
- `__search`：只有 `auto` / `bing` 两种设置；`auto` 跟随模型 API 选择 Anthropic `web_search`、OpenAI Responses `web_search`、DeepSeek `deepseek_web_search`（Messages/Chat Completions 两种协议，关闭 thinking）或 Bing；count 上限、结果格式、输出/答案截断和 HTTP 错误；原生搜索失败回退 Bing；Bing RSS 解析、HTML 解析、实体解码、转义标签与 `<` 比较的区分；
- `__ask`：问题和选项校验（空问题、超过 8 个选项、超长文本）、结束回合、问题写入历史、编号回答解析、网页 `question` 事件和 `/api/status.question`；
- `__http`：公共/私有 IPv4、DNS 解析结果、端口和明文 HTTP 限制、URL 内嵌凭据、请求头注入、重定向逐跳校验、文本截断、二进制拒绝、下载大小和工作区写入；
- 自动模式提问守卫：解释中检测到面向用户的提问且同时有 python 代码块时，代码不执行并走协议纠正；示例/片段应使用 text 代码块的提示；中英文检测和误报边界；
- API 重试：429/5xx/网络错误的可重试分类、401/证书错误不重试、指数退避与 `Retry-After` 上限、压缩请求重试；
- 会话头：`x-opencode-session` 的稳定性、`/clear` 轮换和每次模型请求的携带；
- `/workspace run` 和 `__workspace run`：脚本大小、`auto_run` 门槛或用户确认回调、无限循环保护、前台执行结果回传，以及 `background=true` / `/workspace run bg` 创建后台任务；
- 旧 `.py` 记录迁移为 Markdown Skill；
- Anthropic Messages：system 拆分、adaptive thinking/effort、SSE content_block 事件、`stop_reason` 映射、401/529 分类、`temperature` 省略；
- thinking：四档归一化（`default`/`medium`/`middle` → `high`）、`config.json` 持久化和旧值迁移、流式超时按原等级重试且不降级；
- 对话持久化：原子写入、字符/消息裁剪、`kind` 保留、损坏 JSON、`/clear` 删除、启动恢复；
- 上下文回退：真实用户消息识别（旧历史 `Local ...` 观察不可回退）、`count`/绝对 `index` 校验、待执行/问题清空、持久化截断、后台任务运行期间拒绝；
- 多后台任务：`BackgroundPool` 的 worker 线程、最多并发数/排队、`queued/running/ready/error`、单任务停止/全部停止、编号/备注/耗时/`summary_lines`、结果按 id 提交/丢弃、`background_max_records` 修剪且不丢 ready、多任务持久化与重启后 active→error、降低 `background_max_records` 时保留 ready/只裁剪非 ready、`id@ticks` token 去重、原任务摘要和 `turns` 恢复；前台运行与后台运行模式分离、运行期间允许对话/新代码/前台执行、维护操作 409、待执行项/问题阻止自动继续、`turn_busy` 与 `busy` 分离；AUTO 模式设备主线程自动继续、手动模式保留继续/丢弃、`auto_continue_seq` 网页历史同步、`__background list|status|stop|note|discard` 与 Web 端点；无限超时 `0`/`infinite` 仅后台允许；
- 执行并发：共享 `ExecutionRuntime` 按线程路由 `time.sleep*`/`print`/嵌套 `__harness_check`，worker 输出隔离、不调用 `os.dupterm`、最后一个执行结束后恢复全局钩子；两个 worker 与前台同时运行；`__harness_check` 嵌套 `exec(..., 独立 globals)` 仍可协作停止；
- 执行超时：`range`/`while True`/`time.sleep` 插桩、`__harness_check()`、`ExecutionTimeout` 不被 `except Exception` 吞掉、超时观察回传、`timeout=` 开围栏解析/空格与标点兼容/闭合围栏后独立行兼容/首个源码行兼容/说明文字不误读、正整数上限、正小数向上取整而非变成无限；
- MCP：initialize/session、JSON/SSE、tools/list 分页、tools/call、2026-07-28 `_meta`/Header、404 重初始化、Header 脱敏、只读工具免确认和 auto 确认。

真实硬件测试前核对实际接线、电平和供电；I2C 地址可用 `I2C.scan()` 验证。不要只依据主机 Fake 结果判断真实引脚。

## Web 控制台测试

新增或修改 Web 功能时至少覆盖：

- `webauth`：加盐 SHA-256、密码长度、错误密码限速/指数锁定、`Retry-After`、会话 TTL/上限/登出/改密清除、Cookie 解析；
- HTTP：请求行/头/正文上限、Content-Length、拒绝 Transfer-Encoding 和重复长度、100-continue、安全头、JSON/NDJSON framing、Host/Origin/X-Harness 校验；请求头与正文共享总 deadline，socket 超时不能无限续期；WebServer 停止未完成时不得假成功重启；
- 配置：秘密字段永不返回、`_set` 布尔、keep/clear 语义、校验失败回滚、CA 路径和 PEM 校验、单次保存；JSON 布尔类型严格校验；`save_all()` 第二次写盘失败时两个文件都恢复；保存成功后的响应发送失败不再回滚 RAM；setup 失败可重试且密码会话不提前轮换；
- 命令：非交互命令可用、确认命令返回 `confirm` 并可带 `confirm:true` 重试、交互/代码执行/重启命令拒绝、输出截断、变更命令持锁、只读命令不持锁；`/code` 必须持锁，HTTP 200 中的 `ok:false` 必须在网页显示错误；
- 状态与历史：pending 清理竞态不返回 500，历史字符预算优先保留最新消息，历史条目返回绝对 `index`/`kind`，`backgrounds` 列表和单任务结果字段有界；
- 串口：CRLF/LFCR 配对只吞一次，LF-only 和 CR-only 的连续空行必须保留；
- Web UI：聊天区 Thinking/AUTO 控件不在设置页重复出现，AUTO 开启确认和反馈保持；代码、执行输出和本地记录默认折叠，流式更新保留展开状态，窄屏控件可换行；CA 已安装时高级自定义默认收起，缺失时自动展开且不改变 TLS 验证默认值；顶栏不再有回退按钮，实时用户消息通过 `done.user_index` 添加回退按钮，手动停止后保留状态轮询并在设备回合结束时刷新历史补上，刷新历史后同样显示；
- 聊天与执行：auto off 代码卡、auto on 安全代码执行/反馈、无限循环阻止、Skill 永不自动保存、`max_turns`（默认 32）和 `/auto turns` 边界、手动 Run 只反馈一次、客户端断开在步骤间停止；前台/后台 `mode` 分离、多个后台任务并发与排队、`/api/continue {"id"}` 只提交一次、自动继续卡片和回退按钮存在；
- 线程：`UIRouter` 线程隔离、`Executor.main_runner` 回主线程、worker executor 不回主线程、`Application.run_on_main/pump_main_request`、后台期间允许对话/前台执行但拒绝维护操作、停止单任务/全部任务、共享运行时的全局钩子恢复、协作钩子喂狗与非协作前台代码停止喂狗；
- 网络：AP 启动/停止、AP 宽限、无管理密码不关 AP、断线开 AP、STA 重试、状态缓存；
- 目标文件：`tests/test_background.py`、`test_console_ui.py`、`test_executor.py`、`test_web_auth.py`、`test_web_http.py`、`test_web_app.py`、`test_web_ui.py`、`test_network_ap.py`。

真实设备测试建议依次验证：`/web`、STA 登录、静态资源、错误密码限速、非交互命令和确认流程、配置脱敏、AP-only 首次设置、`/web reset-password`、网页触发 handoff 和 30 秒死循环复位。多后台任务至少验证两个 worker 同时运行、前台代码与 worker 输出不串线、`/background list|stop ID|note|discard`、无限任务协作停止、队列升槽，以及退出后 `builtins.print`/`__harness_check`/`time` 已恢复。不要默认发送真实 LLM 请求；它会使用用户凭据并计费。真实 LLM 压力测试需要用户明确授权，并让串口与网页测试分阶段进行、不要同时占用设备；建议先完成串口长对话，再重置并通过网页 API/浏览器做独立的长对话、取消和标签切换测试。免费或排队严重的网关可能对 600 字以上的提示连续返回响应头超时；测试时保持每条约 120–200 字、要求 500 字以上回答，并适当提高 `api_retries`、降低 thinking，测完恢复原配置。

MicroPython 兼容注意：不要调用 `Exception.__init__(self, message)`，ESP32 移植会报 `AttributeError: type object 'Exception' has no attribute '__init__'`。自定义异常只需定义 `__init__` 并设置属性（必要时覆盖 `__str__`）。Web 线程使用 32 KB 栈，后台 worker 默认也用 32 KB 栈；创建 worker 前会设置 `_thread.stack_size`，测试时务必在真实板验证两个任务同时运行和内存余量。`socket.getsockname()` 在 MicroPython 不可用，固定端口或捕获 `AttributeError`。`str.isalnum()` 在 1.29 不可用，使用显式字符范围。`network.STAT_*` 在 1.29 中是 `STAT_NO_AP_FOUND=201`、`STAT_WRONG_PASSWORD=202`、`STAT_CONNECTING=1001`、`STAT_GOT_IP=1010` 等，不是 0–5；ESP-IDF 在新连接扫描期间可能沿用上一次的断开原因，判定失败前要给驱动留出稳定窗口。MicroPython 的内建模块不一定会出现在 `sys.modules`，测试全局 `time` 恢复时应允许条目缺失。

执行器相关：MicroPython 对 `for i in range(...)` 使用编译期内建 `range`，覆盖 globals 无效；`executor.instrument_source()` 会跳过字符串/注释并把 `range(` 改写为 `__harness_range(`，同时把 `while True:` 改为 `while __harness_check() or True:`。MicroPython 内建模块通常不允许 `setattr`，因此 `time` 代理通过临时替换 `sys.modules["time"]`/`["utime"]` 实现，执行结束必须恢复。修改这些逻辑时同时在真实板上验证 `DEVICE_SMOKE_OK` 和超时脚本，不要只依赖 CPython 测试。

## 每次修改后的检查

主机门禁（编译 + 全套测试，通常 10 秒内）：

```powershell
python tools/check.py
```

只改 Agent、配置、HTTP、LLM、持久化或 Web 时可以先用更快的子集，或只跑指定模块：

```powershell
python tools/check.py --quick
python tools/check.py --tests tests.test_agent tests.test_conversation -k ask
```

`tools/check.py` 等价于 `compileall device tools tests` 加对应的 `unittest` 命令，不访问设备。当前有 519 项主机测试。主机依赖和与固件 `_mpy` 匹配的 `mpy-cross`：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

参考固件：

```text
ESP32-S3 N16R8
MicroPython 1.29.0 / ESP32_GENERIC_S3-SPIRAM_OCT
_mpy = 11014
```

串口用 `<PORT>` 表示：Windows 为 `COMx`，Linux 常见 `/dev/ttyACM0`，macOS 常见 `/dev/cu.usbmodem*`。主机工具支持 `HARNESS_PORT` 或自动选择唯一的 USB 串口；`mpremote` 需要显式端口。

如果设备正在运行 `esp32>`，先执行 `/repl`。日常小改动用增量部署和设备快速烟雾（几秒级）：

```powershell
python tools/deploy.py --port <PORT> --changed --no-reset
mpremote connect <PORT> run tools/device_quick_smoke.py
```

`--changed` 用 `build/deploy-manifest.json` 比较内容哈希，只编译/上传变化的模块，并删除被替换模块的旧 `.py`/`.mpy`；清单同时跟踪 `certs/ca-bundle.pem`，bundle 变化时重新复制 `/ca.pem`。第一次运行或没有清单时仍完整部署。`device_quick_smoke.py` 只检查导入、关键默认值、一次 Fake Agent 回合和一次微型执行，不启动 Web 线程、不测网络/外设，不写持久对话。涉及 Web 线程、handoff、硬件缓存、真实外设或发布前，仍要跑完整流程：

```powershell
python tools/deploy.py --port <PORT> --no-reset
mpremote connect <PORT> run tools/device_smoke.py
mpremote connect <PORT> reset
python tools/console_smoke.py --port <PORT>
python tools/input_smoke.py --port <PORT>
```

部署器默认构建并复制 `.mpy`，不会把主机 `__pycache__` 带到设备。`--source` 可部署 `.py`，适用于小型调试；发布版本优先使用 `.mpy`。

`--clean` 会清空 VFS（包括配置、CA、技能），随后部署器会重新复制公共 CA；只应在明确允许时使用。

## 设备代码约定

- 以 MicroPython 1.29 的 ESP32-S3 移植为目标；
- 使用小模块、短函数和 `%` 格式化，避免 CPython 专属 API；
- 网络、历史、代码和输出必须有上限；
- 不在模块导入时连接网络；
- 硬件相关导入延迟到实际需要时；
- 每次模型请求可以复用或新建 HTTP/TLS 连接，正确性不依赖长连接；
- 新纯逻辑必须添加 PC 测试，硬件兼容必须添加设备烟雾断言；
- 修改命令、结构、配置或工作流时同步更新 README 和相关 docs。

## 执行器兼容性

CPython 测试通过替换 `sys.stdout` 捕获输出；MicroPython 的 `sys` 属性不可这样替换，因此设备使用 `os.dupterm()`。自定义流必须实现 `readinto()`、`write()` 和符合 MicroPython 1.29 约定的 `ioctl()`。不要仅依据主机测试判断输出捕获正确。`BufferWriter` 会把 CRLF/CR 统一为 LF；`write()` 对 bytes 必须返回输入字节数而不是解码后的字符数，否则多字节 UTF-8 的后续 `print` 参数会被截断。`ExecutionResult.live` 表示输出已在串口实时显示，控制台据此避免重复打印。

串口 REPL 使用 UART0 的固定 RX 缓冲。自动化脚本一次写入超长中文任务可能丢失字节；测试和工具应分段发送并在段间短暂延时。不要通过重新初始化 UART0 来绕过，这会破坏 REPL。

## 调试真实 API

1. 先 `/status` 检查脱敏配置和空闲内存；
2. `/test` 验证 Wi-Fi 和 RTC/NTP；
3. 确认 `/ca.pem` 是目标域名所需的根证书或包含它的全量 bundle；
4. 把设备配置中的 `http_trace` 设为 `true` 可观察 DNS/TCP/TLS/HTTP 阶段，完成后恢复 `false`；
5. 最后 `/test api`，避免把网络、证书和协议问题混在一起；
6. API 错误只显示有界正文，不输出 Authorization 请求头。

当前客户端要求 HTTP/1.1 和未压缩正文，支持 Content-Length、Chunked 和 SSE `data:` 事件；默认 `stream=true`。供应商若强制 HTTP/2 或压缩响应，需要扩展客户端，而不是绕过 TLS。

DeepSeek 默认开启 thinking，且 reasoning 会计入 `max_tokens`。本项目默认 `thinking_level=high`，只有四档，可通过 `/thinking` 切换：

- `disabled`（别名 `disable`/`off`）：发送 `{"thinking":{"type":"disabled"}}`；
- `low` / `high` / `max`：发送 `{"thinking":{"type":"enabled"}}` 和对应 `reasoning_effort`；
- 旧的 `default`/`medium`/`middle` 仍被接受，统一规范化为 `high`。

用户设置会写入 `config.json`；`Config.__init__` 在加载时规范化旧值，因此下一次对话和重启、复位后都使用同一级别。Anthropic 路径的 `low`/`high`/`max` 对应 `output_config.effort`，`disabled` 不发送 thinking 参数。

流式请求的响应头等待取 `stream_header_timeout`（默认 300 秒）与 thinking 预算的较大值（`high` 600 秒、`max` 900 秒）；正文开始前另受 `stream_first_token_timeout`（默认 600 秒）保护；非流式请求仍按 thinking 级别使用至少 600/900 秒。流式正文开始后同时受 `stream_idle_timeout`（默认 300 秒字节空闲）和 `stream_event_idle_timeout`（默认 300 秒真实事件空闲）约束；持续到达的正文/reasoning 会刷新两层计时，SSE ping 只能刷新字节层，不能掩盖模型停滞。正文不会因为固定总时长被截断。该规则对不发送 `thinking` 参数的非 DeepSeek 模型同样生效。`show_model_meta` 控制结果元数据，`show_thinking`（默认 `true`）控制 reasoning 文本是否打印；`/thinking show on|off` 可切换并保存。`thinking_print_limit` 同时限制流式和非流式打印长度。`finish_reason=length` 会先提高 token 预算重试，仍截断再降级 thinking。高 thinking 的 SSE 事件总量可能较大，`stream_response_limit` 默认 16 MiB。

## API 重试

`llm.py` 给 `LLMError` 增加 `status`、`retryable` 和 `retry_after` 属性：HTTP 408/429/500/502/503/504/529 标记为可重试；网络、DNS、连接、超时、流提前结束和 SSE 错误按消息分类；TLS 证书、URL、大小限制、截断和空正文不重试。

`Agent._complete()` 在 max_tokens 截断处理之后执行通用重试；流式超时不再降低 thinking 等级，而是按当前等级重试。配置项：

```text
api_retries              默认 3，首次请求之外的重试次数
api_retry_base_ms        默认 1000，首次重试延迟
api_retry_max_ms         默认 30000，退避延迟上限
api_retry_after_max_ms   默认 300000，Retry-After 独立上限
api_retry_jitter_pct     默认 25，0–25% 随机抖动
```

延迟按指数增长；`Retry-After` 秒数优先采用，并受 `api_retry_after_max_ms` 限制，不再被 `api_retry_max_ms` 截断。重试期间通过 HTTP 客户端的 `feed` 回调持续喂狗，并检查串口/网页取消。`_retry_api_call()` 供压缩请求和 `/test api` 复用。**已经输出正文后的超时/断流会标记为 partial，不再自动重试**；只有没有正文（包括仅 reasoning）时才允许通用重试，且重试保持当前 thinking 等级。partial 正文不会写入历史。

测试用 `api_retry_base_ms=0`、`api_retry_max_ms=0`、`api_retry_jitter_pct=0` 避免真实等待和随机值。不要重试 401/403、证书错误或不可解析响应，否则会把配置问题拖成多次超时。

## SSE 与上下文压缩

流式请求由 `http_client.HTTPBodyReader` 增量读取正文，`llm.complete_stream()` 逐行解析 SSE。需要同时验证：

- chunked 块边界、Content-Length 和连接关闭三种正文模式；
- 跨 `recv()` 边界的半行和半个 UTF-8 字符；
- reasoning/content 回调顺序、`[DONE]`、空流和未完成流；
- 持续数据刷新字节空闲计时、SSE ping 不刷新事件空闲计时、超过 `stream_idle_timeout`/`stream_event_idle_timeout` 中止、首字节超过 `stream_first_token_timeout`，以及 `stream_header_timeout` 响应头超时；
- `finish_reason` 到达即完成，不等待 `[DONE]`；已输出正文后超时标记 partial 且不重试；
- 正文 socket 轮询上限（5 秒）低于看门狗，长空闲超时期间仍能喂狗；
- SSE 响应总字节上限 `stream_response_limit`，它高于非流式的 `response_limit`，因为每个事件带协议开销。

上下文压缩位于 `agent.py`：

- `estimate_tokens()` 是近似值，不追求与供应商 tokenizer 一致；它使用 `len(text.encode("utf-8"))` 在 C 层估算，3 字节中文按 1 字符/token，2/4 字节字符做近似。不要退回逐字符 Python 循环：6 万字符历史会让状态轮询和请求准备各耗时数秒；
- `_maybe_auto_compact()` 在请求前检查 `auto_compact_tokens`；
- `_compact_history()` 通过 `_summary_request()` 调用流式 `llm.complete_stream(..., thinking_level="disabled", allow_truncated=True, timeout=summary_timeout)`；`stream=false` 时回退到 `llm.complete`。输入只取最近可放入 `summary_input_chars`（默认 120000）字符窗口的较早消息，输出受 `summary_max_tokens` 和 `summary_char_limit` 双重限制；摘要达到输出上限时接受截断内容，避免整个压缩失败；
- 摘要放入系统提示词，最近 `compact_keep_messages` 条消息保留；
- 压缩失败不会阻止当前请求，随后仍按 `context_tokens` 裁剪；
- `/compact`、`/context`、`/summary` 分别对应手动压缩、估算统计和摘要查看。

修改压缩逻辑时，应同时测试摘要合并、空摘要、历史不足、自动阈值触发和 `/clear` 清除摘要。

## 版本管理

仓库使用 Git，`.gitattributes` 统一文本文件为 LF。真实 `secrets.json`、本地 `config.json`、缓存、固件、备份、日志、`.mpy` 构建产物和本地 Claude 设置已忽略。提交前检查：

```powershell
git status --short
git diff --check
```

不要提交设备导出的配置、Wi-Fi 密码、API Key、固件二进制或含敏感信息的串口日志。主机依赖见根目录 `requirements.txt`；安全报告方式见 `SECURITY.md`。
