# Changelog

## Unreleased

### Added

- 后台任务“停止并丢弃”：`__background action=stop_discard`、`/background stopdiscard [ID]`、网页每个任务的“停止并丢弃 #N”和“停止并丢弃全部”，以及 `POST /api/background/stop {"discard":true}`。排队任务立即移除；运行中任务标记 `discard_on_finish`，在协作检查点结束后直接移除，不产生 ready 结果，不会被自动继续/手动继续，也不会在重启后恢复为 error/ready 记录；
- 定时任务：新增 `schedule.py`、`/state/schedules.json` 持久化和 `__schedule` 内置工具（list/get/create/update/delete/enable/disable/run）。支持一次性（`delay_s` 或本地 `at`）、固定间隔（`every_s`）和每天本地时间（`at=HH:MM`）；提示词任务到点后执行一个正常 Agent 回合，代码任务到点后创建后台任务；AI 创建、修改、启用或立即运行代码任务需要 AUTO，停用/删除不受限。串口主线程在 `Application.idle()` 中逐个执行，RTC 无效时跳过；失败 60 秒后重试、最多 3 次，重复任务错过多次只补跑一次；新增 `/schedule`、`GET/POST /api/schedules` 和网页“任务”页的新建/编辑/启停/立即运行/删除，状态通过 `/api/status.schedules` 和 `schedule_seq` 同步；
- `__time` 写入与时间区管理：`action=set` 支持本地 `at` 或 UTC `epoch` 写入 `machine.RTC()`（始终保存 UTC），`action=timezone` 修改 `timezone_offset_minutes` 并重排每日定时任务；保留默认 `read` 行为；
- 网页“任务”页：后台任务气泡从聊天流移到底部“任务 → 后台任务”，位置固定，不再随新回复漂移；同一页下方管理定时任务；
- `docs/openai.md`：补齐 OpenAI 兼容 Chat Completions 的端点、Header、thinking、流式工具调用、重试和限制说明，与 `docs/anthropic.md` 对称；
- 自动校时与 `__time` 工具：联网成功后自动通过 NTP 校时，启动时立即尝试，之后按 `ntp_sync_interval_s`（默认 21600 秒/6 小时，0 表示仅首次）刷新，失败 60 秒后重试；新增 `ntp_server`、`ntp_sync_interval_s`、`timezone_offset_minutes`（默认 480，UTC+8）配置及网页“行为”设置。`__time` 直接读取 RTC 的 UTC 时间，返回本地 ISO 时间、UTC、epoch、星期、时区偏移和有效性，模型可用原生或文本工具调用回答当前日期/时间；RTC 本身仍保存 UTC，时区只影响显示，不处理夏令时；
- 思考文本默认显示：`show_thinking` 默认值改为 `true`，出厂设置或首次启动后 High/Max 思考模式的 reasoning 会直接显示，不再出现“显示 High 但看不到思考”的观感；仍可用 `/thinking show off` 或网页“显示思考文本”关闭；
- Anthropic 协议的 DeepSeek 思考参数：`AnthropicClient` 检测 DeepSeek 模型或 `deepseek.com` 端点后改发 DeepSeek 的 `thinking: {"type":"enabled"}`（关闭时显式 `disabled`），不再发送会被 DeepSeek 忽略的 Anthropic `adaptive`/`output_config`；修复切到 DeepSeek Anthropic 兼容端点后显示 High 但实际未思考的问题；
- 网页搜索简化为 AUTO / Bing 两种：移除独立 DeepSeek Search API、`search_url` 和 `search_api_key` 专用 Key，也移除显式的 Anthropic/OpenAI/DeepSeek 选项。`search_provider=auto` 跟随模型 API——Anthropic 使用 Messages API 的 `web_search_20250305` 服务端工具，OpenAI 官方端点使用 Responses API 的 `web_search`，DeepSeek 直连端点使用 `deepseek_web_search` 服务端工具（显式关闭 thinking），OpenCode Go 和其他兼容端点使用 Bing 免 Key 搜索；原生搜索通过一次有界的独立 API 请求执行，失败时自动回退 Bing。显式 `bing` 不回退。Bing 优先请求 `&format=rss`，RSS 无结果或失败时再解析 HTML，并正确区分转义 HTML 标签与 `<`/`>` 比较文本。网页设置只显示 AUTO/Bing 和实际生效提供方；`/search provider` 同步只接受这两个值；`/api/config` 返回 `search_provider_effective`/`search_provider_fallback`；新增 `search_bing_rss`、`search_answer_limit`、`search_native_max_tokens`、`search_anthropic_tool`、`search_openai_tool` 配置；旧 `search_url`/`search_api_key` 在配置加载时从内存中移除；
- 更多板载资源 API：新增 `api.pwm`（`machine.PWM`，占空比 0–65535，仅限 `/peripherals pwm <pin>`）、`api.touch`（`machine.TouchPad`，仅限 `/peripherals touch <pin>` 的 GPIO1–14）、`api.rtc`（读取始终可用，写入需 `/peripherals rtc write on`）、`api.pulse.measure`（`machine.time_pulse_us`，用于 HC-SR04 等，要求引脚在 `allowed_gpio_pins` 且先设为输入）和只读 `api.system`（内部温度 `esp32.mcu_temperature`、唤醒/复位原因、唯一 ID、CPU 频率、运行毫秒）；`pwm_pins`/`touch_pins`/`rtc_write` 加入配置和 `/peripherals`，所有旧外设检查继续生效；
- SPI 显示支持：`/peripherals spi <id> <sck> <mosi> <miso|-> [freq] [hw|sw]` 现在默认使用硬件 `machine.SPI`，写入型设备可用 `-` 省略 MISO；`api.spi.write/read/transfer` 在只配置一条总线时可省略 bus 参数；SPI/I2C/UART 数据接受 `memoryview`；新增 `/peripherals transfer <bytes>` 调整 `peripheral_max_transfer`（最高 4096）；`device.info()` 增加 CPU 频率、唯一 ID 和复位原因；`import device` 作为兼容别名可用；
- 原生 function calling：OpenAI 兼容端点使用 `tools`，Anthropic 使用 `input_schema`；Agent 消费 `tool_calls` / `tool_use` 并以原生 assistant/tool 消息回传结果，流式累积工具参数，空正文但有效工具调用不再被误判为空响应。端点拒绝原生工具时自动回退文本 `tool` 块；新增 `/native on|off` 和 `native_tools` 配置；
- 生成代码硬件 API：执行器注入受控全局 `api` 对象（`api.status/available`、`api.gpio/spi/i2c/uart/neopixel/adc`），工作区脚本和后台任务同样可用；所有调用继续受 `peripherals_enabled`、保留引脚、`allowed_gpio_pins`、已配置总线和 `peripheral_max_transfer` 限制，`import api` 兼容旧代码。系统提示词明确可用 `api` 驱动 ST7735 等外设，但不得 `import machine`；
- 工作区脚本后台运行：`__workspace action=run` 新增 `background=true`，串口新增 `/workspace run bg <path> [timeout]`；后台默认使用 `code_timeout_max_s`，短任务仍优先前台，只有长任务才建议后台并说明原因；
- 上下文回退：`/undo [N]` / `/rewind [N]`、网页顶栏“回退”和每条用户消息的“回退到这里”；删除第 N 条最近真实用户消息及其后的全部回复，同时清除待执行代码、Skill 提案、待回答问题和最近执行结果。历史消息新增持久化 `kind` 标记，旧历史按已知观察前缀推断，避免把工具/执行记录当成用户消息；
- 多后台任务：`background_execution`（默认 `true`）现在可创建多个后台任务，每个任务在独立 worker 线程执行，最多同时运行 `background_max_jobs` 个（默认 4，可设 1–4），其余 `queued`；串口主线程保持空闲，可继续对话、前台运行代码或处理网页请求。`/api/status.backgrounds` 返回每个任务的编号、备注、`queued/running/ready/error`、耗时、超时、停止状态和有界结果，`background` 保留单任务兼容视图；网页按任务显示卡片并分别继续/停止/丢弃，自动继续逐个提交。`/api/run` 支持 `mode=foreground|background`；`/api/continue` 支持 `id`；`/api/background/stop|discard` 支持 `id`/`all`。任务记录在 `/state/background.json` 中有界保存，重启后未完成记录显示为 error 且不会自动重跑；
- 执行并发隔离：新增共享 `ExecutionRuntime`，按线程 ID 路由 `time.sleep*`、`print` 和嵌套 `exec` 的 `__harness_check`，使 worker 输出不串线、前台与多个后台任务可同时协作停止；后台 worker 不调用 `os.dupterm`，最后一个执行结束后恢复原始 `builtins`/`time` 钩子；
- 多任务管理界面与工具：新增 `/background [list|status ID|stop [ID]|note ID TEXT|discard ID]`、`/run bg [备注]`、`/continue [id]`、`/discard [ID]`；网页设置新增 `background_max_jobs`；内置 `__background` 支持 `start|list|status|stop|note|discard` 和任务编号；AUTO 模式下 AI 可直接以 `action=start` 启动后台任务（源码、备注、超时），非 AUTO 模式仍要求返回 python 代码块确认；系统提示词说明多任务、备注、前台/后台运行和输出捕获限制；
- 网页历史消息返回绝对 `index` 和 `kind`；新增 `POST /api/rewind`、`POST /api/continue`、`POST /api/background/discard`、`POST /api/background/stop`；`/api/config` 和网页设置新增 `background_execution`。
- 后台任务停止：内置 `__background stop`、网页每个任务的停止按钮、`/background stop` 和 `POST /api/background/stop` 设置协作取消；执行器在检查点抛出 `ExecutionCancelled`，返回 `status=cancelled`，不会硬杀阻塞中的 C/SPI 调用。
- 后台无限超时：代码块可写 `timeout=0`/`infinite`/`inf`/`unlimited`，仅后台执行允许；串口前台 `/run` 会拒绝并保留 pending。无限后台任务仍依赖 `__harness_check()`、插桩循环或 `time.sleep()` 喂狗和接收停止请求。
- 系统提示词和内置工具提示补充后台执行、结果观察、上下文回退、线程存活和 `timeout=` 正确写法；执行超时观察会明确提示开围栏位置。
- 工作区访问提示：系统提示词和内置 `__workspace` 说明现在明确要求用 `summary/list/read/stat` 查看工作区，不要为“看一眼 Workspace”生成 `import os` + `listdir/open/stat` 的 Python 代码；只有确实需要对文件内容做计算时才生成代码。
- AUTO 后台自动继续：`auto_run=true` 且 `auto_feedback=true` 时，设备主线程在串口 `esp32>` 空闲时自动提交已完成的后台结果并继续模型对话，不再显示“继续/丢弃”选择；浏览器关闭或标签页隐藏也能继续。手动模式仍保留网页按钮和 `/continue`。`/api/status.auto.continue_seq` 用于网页在设备自动继续后刷新历史。
- AUTO 后台继续的网页流式显示：新增有界 `LiveTurn` 缓冲和 `GET /api/live`，`/api/status.live` 返回摘要；打开的网页在 `busy_source=auto-continue` 时轮询该接口，把 reasoning/content 增量追加到聊天气泡，不再只显示“正在处理”后突然出现整段回复。完成后仍以 `/api/history` 为准。

### Fixed

- 生成代码的 `while <任意条件>:` 此前不会自动插桩，只有 `range()`、字面量 `while True:`/`while 1:` 和 `time.sleep()` 会喂狗；使用 `while time.ticks_diff(...) < ...:` 等轮询循环且不显式调用 `__harness_check()` 时，30 秒看门狗会直接复位设备（`WDT_RESET`）。执行器现在把所有 `while` 条件改写为 `while __harness_check() or (<条件>):`，扫描时跟踪括号、字符串、注释和 `:=`，避免误改切片/字典/lambda 中的冒号；非 `range` 的 `for` 循环和单次迭代内长时间阻塞仍需显式检查；
- 后台任务或定时任务完成后的 Agent 续答会与上一条回复合并到同一个 turn 卡片，看起来像同一条消息。现在后台结果观察和定时任务都从新的 turn 开始；live 自动继续通过 `separate_turn` 标志创建新 turn，并在新 turn 内显示“后台任务 #N 已返回/定时任务 #N 开始执行”的提示。手动“继续”也会先开启新的 turn；
- `__http` 出站信任边界加固：DNS 校验与实际连接原本分别解析，存在 rebinding 窗口；现在把校验过的 IPv4 地址固定用于该次 socket 连接，同时保留原主机名的 TLS SNI 和 Host。跨源重定向会丢弃调用方 Header，HTTPS→HTTP 降级被拒绝；URL 和 Header 中的换行/控制字符、非法 Header 名会被拒绝，避免请求头注入；
- `tools/deploy.py --changed` 现在跟踪 `harness/__init__.py`，只改 `__version__` 的增量部署不会再被静默跳过；部署器改用当前 Python 的 `sys.executable -m mpremote`，避免虚拟环境中的 `mpy-cross` 与系统 Python 的 `mpremote` 混用；
- 快速开始此前只创建虚拟环境并安装依赖，后续却调用系统 `python`/`mpremote`；中英文 README 和部署文档现在明确激活虚拟环境，并把“先刷入匹配的 MicroPython v1.29.0 SPIRAM_OCT 固件”放在部署之前；
- 停止流式响应后实时用户气泡缺少“回退到这里”按钮：中止 fetch 的 `catch` 先清空了 `activeStream`，使 `waitForStoppedTurn` 立即退出，设备回合结束后不会刷新历史。现在停止分支保留 `activeStream` 并持续轮询 `/api/status`，设备空闲后刷新 `/api/history` 并按绝对 `index` 补上按钮；
- 网页顶栏的“回退”按钮没有实际用途且容易误触，现已移除；每条真实用户消息旁的“回退到这里”、`/undo`、`/rewind` 和 `POST /api/rewind` 保持不变；
- 压缩后忘记最初问题：新增持久化的 `original_request` 会话锚点，首次用户请求随 `/state/conversation.json` 保存并始终注入系统提示词；压缩、重启和 HISTORY 裁剪不会丢失会话起点，`/clear` 才重置；
- 网页对话改为按用户请求分组的 turn 卡片：同一轮的每个 thinking 步骤、工具调用/结果、执行结果和最终正文都在一个助手卡片内按顺序展示；assistant reasoning 随对话持久化（每条最多 4096 字符），刷新后仍能看到 thinking，而不是丢标签或出现空助手气泡；
- 网页 live 渲染与刷新后历史不一致：live 中原生 tool call 直接渲染为紧凑的工具调用步骤，刷新后把历史里的 `Native tool call: ...` 助手内容还原为同样的工具卡片，不再出现空助手气泡或只在刷新后才看到的工具调用；
- 网页 live 轮询竞态：状态轮询可能在一个 `/api/live` 请求尚未返回时重置 `liveSeq`/事件索引并启动第二个轮询，导致 thinking 窗口、回复正文或工具卡片重复出现、顺序跳动；现在使用 in-flight 标志和 generation token，只有新回合 id 才重置游标，回合结束后保留游标直到状态确认 inactive；
- 同一回合内重试或 tool-call 的多个模型请求现在在同一个 turn 卡片内各自新增一个折叠的 thinking 步骤，工具、执行结果和正文按实际顺序排列，而不是弹出多个独立气泡；
- `LiveTurn` 现在在每个模型请求开始时写入带内容/推理偏移的 `model_start` 事件，浏览器按偏移先处理回合/工具事件再追加正文片段，避免多条助手回复被合并到同一个气泡，并保持工具卡片与正文的先后顺序；
- 网页自动滚动：原先在追加 DOM 后才检查是否接近底部，大块内容会让页面瞬间变高从而停止跟随；现在在追加前捕获 near-bottom，并在下一帧滚动到底部，覆盖正文、thinking、工具/执行卡片和系统消息；
- 无限循环安检把任何 `while True:` 都当成可疑代码，连有 `break`/`return` 的普通 socket 读取循环也被 `__workspace action=run` 拒绝，模型被迫改写探测脚本；现在按缩进扫描循环体并在去除注释/字符串后识别 `break`/`return`/`raise`，有界循环可通过，真正的无出口循环仍被拒绝；
- `__workspace action=run` 和串口 `/workspace run` 都用 `code_limit`（默认 12288）截断已写入工作区的脚本，13 KB 的正常脚本被拒绝，尽管 `workspace_file_limit` 允许 32768。现在改用 `Workspace.read_source()` 读取完整脚本并以 `workspace_file_limit` 为上限；`code_limit` 只约束模型生成的 python 代码块。缺少 `action` 时也返回明确的 `action is required; ...` 提示，而不是泛泛的 usage；
- 自动继续收到 `cancelled/stopped` 的后台结果时，模型把“被用户停止”误判为“任务未完成”，会自动重新启动同一个后台程序。系统提示和原生工具提示现在明确：取消/停止是用户的明确动作，不得自动重启，除非用户再次要求；应报告停止结果；
- AUTO 后台结果在网页上重复显示：同一结果既由后台卡片展示，又作为 `execution` 事件进入 `/api/live`，停止任务后会连续出现多个“后台已结束/结果”框再出现回复。自动继续的 `LiveTurn` 不再注入 execution 事件；历史刷新把连续的 observation（工具结果、执行结果、后台结果）合并为一个可折叠的“本地执行/工具记录”块，避免一长串结果框；
- 串口在模型请求/网页回合期间缓冲的输入使用 `bytearray`，但 `readline()` 调用 `pending_input.pop(0)`；MicroPython 的 `bytearray` 没有该方法，Ctrl-C 或忙时键入后会让主循环反复打印 `'bytearray' object has no attribute 'pop'`。现在缓冲改为整数列表，并新增回归测试；
- ST7735 实测暴露的提示词/API 问题：`api.spi.write` 原来必须传 bus 参数且没有可见签名，模型先按 `write(data)` 调用后失败，必须额外探测才知道正确形式；原生/文本提示词现在给出完整签名和“单总线可省略 bus”的规则。旧提示词还声称后台任务“never access peripherals from generated code”，与已支持的 `api` 冲突，现已改为协调共享总线；
- 模型把 `content` 和 `action=run` 放在同一个 `__workspace` 调用里时，只收到 `Workspace file not found`，容易多绕一轮；现在直接提示 `action=run` 只执行已有文件，需先 `action=write`；
- 模型按 `device.info()` 的说明写了 `import device`，执行时报 `ImportError: no module named 'device'`；现在除注入全局 `device` 外，还注册 `sys.modules["device"]` 兼容别名，提示词明确要求直接使用全局对象、不要依赖导入；
- 设备能力提示曾宣称存在 `api.gpio/i2c/spi/uart` 和 `api.workspace`，但旧版自定义 Tool 已移除，模型生成 `import api` 后必然失败。现在 `api` 由执行器注入并带完整权限检查（见上方“生成代码硬件 API”），同时清理 `docs/workspace.md` 中残留的 `api.workspace` 示例；
- AUTO 模式无人值守自动继续时可能永久卡在串口询问：模型返回 Skill 提案会触发“是否保存 Skill？”、非 auto MCP 工具会触发“是否执行远程工具？”，而浏览器关闭时没人回答，回合锁一直被占用。自动继续期间现在只提示待处理的 Skill（网页或 `/code` 处理），MCP 调用按未确认取消，不会阻塞；手动 `/continue` 和串口正常流程仍保留询问；
- 正的小数 `timeout_s` 被 `int()` 截断成 0，而 0 在 `Executor` 中表示无限超时；例如 0.2 秒会静默变成无限。现在正小数向上取整到至少 1 秒，整数 0 仍明确表示无限；
- `BackgroundPool.restore()` 在当前 `background_max_records` 较小时会保留最新记录并静默丢弃更早的 `ready` 结果，违反“不静默丢弃已完成结果”的约束。恢复时现在保留全部 ready 结果（仍受存储 16 条上限约束），只裁剪非 ready 记录；
- `auto_run=true` 但 `auto_feedback=false` 时，系统提示词仍声称后台结果会自动继续，与手动恢复行为不一致；提示词和结果观察现在只在两者都开启时声明自动继续；
- 真实设备启动后台任务偶发 `can't create thread`：ESP-IDF 内部堆在模型/TLS 请求后可能不足以为 32 KB worker 栈分配任务。启动前现在关闭空闲 HTTP/TLS 连接并 GC；线程创建失败会依次尝试 32/24/16/12/8 KB 栈，仍失败时返回可操作的错误（提示降低 `background_thread_stack_kb`/`background_max_jobs`）。网页命令页的 `/background`、`/background list`、`/background status ID` 也已加入只读白名单；
- 内置 `__background` 工具没有加入 `parse_tool_call` 的内置工具白名单，模型调用时被误判为“Names may not start with '__'”，重试后仍失败；解析器现在允许该内置名称，并保留对其他未注册 `__` 名称的拒绝；
- 停止后台程序后自动继续报 `Automatic feedback failed: No execution result to send`：重启后后台 job id 会复用，`has_background_result()` 可能匹配到历史中旧的 `(job=1)` 观察，导致 `take_background_result()` 跳过记录、`last_result` 保持为空。现在 job 使用跨重启不重复的 `id@ticks` token，提交时总是记录结果，去重只在启动恢复结果文件时使用；
- 网页正常对话结束后，用户消息旁不显示“回退到这里”，只有手动刷新历史才出现：`done` 事件现在返回本轮用户消息的绝对 `history` 下标，前端在流结束时直接给实时用户气泡补上回退按钮；
- `timeout=` 被模型写在闭合围栏之后时不生效：解析器现在兼容紧跟在闭合围栏后的独立 `timeout=` 行（开围栏 info 仍优先），也兼容代码块首个独立 `timeout=N` 行（仅当源码后续不再使用该名字），并接受 `timeout = 45`、`45s`、结尾逗号等常见写法；超时观察和系统提示词明确说明开围栏位置，避免模型误以为 20 秒上限是硬性的；
- 后台运行 `/workspace/dashboard.py` 约 30 秒后设备被看门狗复位、网页会话丢失：生成代码通过 `exec(..., 独立 globals)` 动态加载脚本时，`__harness_check` 不在其全局命名空间，显式检查退化为 NameError；执行器现在在代码运行期间把同一个检查函数临时挂到 `builtins`，动态加载脚本也能协作停止/喂狗，结束后恢复；
- 设备复位后网页直接回到登录页且没有解释：启动时读取 `machine.reset_cause()`，串口打印警告，`GET /api/setup` 返回 `last_reset`/`reset_note`，登录页显示 WDT_RESET/BROWNOUT_RESET 等原因和排查提示；
- 模型手写 `--- question for you:` 文本块（模仿历史中的渲染格式、没有调用 `__ask`）时，Harness 在代码围栏外识别并转换为待回答问题，网页显示问题卡、串口编号回答继续生效；系统提示词同时要求模型不要手写该格式；
- 网页提问卡片的恢复能力：状态轮询发现卡片已从 DOM 移除时会重新渲染，标签页恢复可见时立即刷新状态，避免后台标签或页面重绘后看不到待回答问题；
- 网页历史与串口 `/history` 不一致：网页视图原来默认只取 50 条/32 KB，长对话看起来像丢了上下文；默认值提高到 200 条/128 KB，旧默认配置自动迁移，`/api/history` 返回 `total_messages`/`shown_messages` 并在网页提示“最近 X/Y 条”和单条 8192 字符上限；`web_history_limit`/`web_history_chars` 加入 `/api/config` 可更新字段；

- 回合失败后上下文丢失：重试耗尽、工具/自动反馈上限、用户取消等停止路径现在会把本轮已产生的用户消息、工具结果和一条“本轮未完成”说明写入 `/state/conversation.json`，不再只保留上一次成功提交的内容；
- Anthropic 流式响应头等待不再反向缩短 thinking 预算：实际超时取 `max(stream_header_timeout, thinking 预算)`（`high` 600 秒、`max` 900 秒）；
- 网关 prefill/排队导致的伪空闲超时：新增 `stream_first_token_timeout`（默认 600 秒），只约束响应头之后、首段正文/reasoning 之前；事件空闲在首 token 之前也使用该预算，避免 120 秒字节空闲误杀长时间预填充；
- 修正文档中“正文 socket 每 5 秒唤醒”的过时描述（实际为 1 秒）。

### Changed

- 网页后台任务不再插入聊天气泡，统一放入新的底部“任务”标签页；该页同时提供定时任务管理，后台任务的结构化输出抽屉和继续/停止/丢弃操作保持不变；
- 文档索引、README、命令、架构、Web、安全、开发和 AGENTS 说明补充定时任务、`__schedule`、`__time` 写时间/时区和任务页；主机测试增加到 586 项；
- `__ask` 选项上限从 4 提高到 8，问题总文本上限从 1600 提高到 2400；
- `stream_idle_timeout` 默认值从 120 秒提高到 300 秒；缺少 `stream_first_token_timeout` 且值仍为 120 的旧配置在加载时自动迁移，之后显式设置的 120 秒会保留；
- 新增 `tools/check.py` 主机门禁（编译 + 全套/快速测试）、`tools/deploy.py --changed` 增量部署和 `tools/device_quick_smoke.py` 设备快速烟雾；
- 部署器默认把 `certs/ca-bundle.pem` 复制为 `/ca.pem`：完整部署、`--clean` 和首次增量部署都会复制，`--changed` 在 bundle 内容变化时更新；删除 `certs/digicert-global-root-g2.pem`，只维护全量 Mozilla bundle；README 和相关文档同步；
- 自动反馈默认上限 `max_turns` 从 8 提高到 32（范围仍 1–50）；串口 `/auto turns`、网页设置和文档同步；
- 移除 `/peripherals` 命令、`HardwareAPI`/`HardwareManager`、外设 allowlist 和对应配置字段；生成代码直接 `import machine`/`neopixel`/`framebuf` 并使用真实引脚；旧 `config.json` 中的外设键在加载时丢弃；系统提示词、README、硬件文档和安全说明同步；
- 网页命令页放开所有非交互命令（配置、Memory/Skill/Workspace 写入、MCP、后台控制、HTTP），风险操作使用浏览器确认并以 `confirm:true` 重试；需要第二行输入、执行生成代码或重启的命令仍使用专用表单/按钮；
- 开源整理：重写中文 README 并新增英文 `README.en.md`，顶部提供语言切换；清理已移除的 Search API Key 文档引用，补充 `.mpy` 和本地 Claude 设置的忽略规则；
- 更新 README、架构、命令、开发、安全、Web 和 Anthropic 文档。

## 0.4.0 - 2026-11-13

### Added

- 模型协议选择：`api_provider=openai|anthropic`，可在 `/setup`、`/model` 和网页设置页选择；
- `llm_anthropic.py`：Anthropic Messages `/v1/messages`、`x-api-key`、`anthropic-version`、`x-opencode-session`、adaptive thinking + `output_config.effort`、SSE content-block 解析、`stop_reason`/refusal/错误分类；
- 对话持久化：`/state/conversation.json` 原子保存已提交历史和摘要，复位/重启后恢复；`/clear`、恢复出厂设置删除，网页可关闭；
- 协作式执行超时：`code_timeout_s`（默认 20 秒）、`code_timeout_max_s`（默认 300 秒）、`range`/`while True` 源码插桩、`time.sleep` 代理和 `__harness_check()`；超时返回 `status=timeout` 并反馈模型，不重启设备；
- 模型自选超时：python fence info 支持 `timeout=SECONDS`；
- MCP 远程工具：`mcp.py` 支持 Streamable HTTP（initialize/session 与无状态 2026-07-28）、JSON/SSE、`tools/list` 分页、`tools/call`、`resources/read`、`prompts/get`、静态 Header、会话过期重初始化；
- MCP 管理：串口 `/mcp add/remove/refresh/tools/enable/auto/timeout/protocol/read/prompt`，网页设置和 `GET/POST /api/mcp`、`POST /api/mcp/refresh`；Header 值只写不读；
- MCP 安全：`readOnlyHint` 免确认，其他工具默认确认，按服务器 `auto` 可关闭确认；输出限长并作为不可信数据；
- `__ask` 内置工具：模型可向用户提问（标题、问题、最多 4 个选项、自定义回答），串口打印编号选项，网页显示问题卡；问题结束当前回合并由下一条用户消息回答，不阻塞设备线程；
- 系统提示词根据 `auto_run` 说明代码是否自动执行，明确要求模型不要向用户索要执行输出；需要用户决策时使用 `__ask`；
- 搜索提供方：`search_provider=auto|deepseek|bing`；`auto` 在没有 DeepSeek 搜索 Key 或当前不是 DeepSeek 端点时使用 Bing 免 Key HTML 搜索，DeepSeek 401/403 时自动回退到 Bing；
- 新增 `__http` 内置工具和串口 `/http get|head|download`：抓取公共 HTTPS 页面/JSON、查看响应头、下载小文件到 `/workspace`；SSRF 地址校验、重定向逐跳校验、明文 HTTP 默认关闭、请求头注入防护和大小限制；
- 串口 `/search provider|key|on|off` 和网页设置新增搜索配置；`search_api_key` 只写不回显；
- 自动模式提问守卫：模型在解释中提问并同时返回 python 代码时，Harness 将其作为协议错误要求纠正，代码不会先执行；示例/片段应使用 ```text；
- 流式 socket 轮询从 5 秒缩短到 1 秒，减少串口模型回合期间网页状态请求被饿死的情况；
- 新增 `docs/anthropic.md`、`docs/mcp.md`，更新 README、命令、架构、开发、扩展、安全和 Web 文档。

### Changed

- 版本升至 `0.4.0`，User-Agent 更新为 `esp32-harness/0.4`；
- 自动反馈默认上限 `max_turns` 从 4 提高到 8，新增 `/auto turns N` 和网页设置（1–50）；达到上限时提示如何调整；
- thinking 级别精简为四档：`disabled`（关闭）、`low`、`high`、`max`，默认 `high`；用户设置会写入 `config.json`，重启/复位后继续生效；旧的 `default`/`medium`/`middle` 自动映射到 `high`；
- 流式响应超时不再降低 thinking 等级，按用户当前等级重试（受 `api_retries` 限制）；只有 `finish_reason=length` 且提高 `max_tokens` 后仍截断时才使用 `thinking_fallback`；
- 执行器在超时/协作检查期间通过 `feed_execution()` 喂狗，长但可协作的程序可以超过 30 秒；看门狗仍是最后防线；
- `ExecutionResult` 和工具结果新增 `timed_out`，执行观察状态可为 `timeout`；
- `config.example.json` 重新按 `DEFAULTS` 生成，新增持久化、超时和 MCP 配置；
- Web 状态/配置增加 `api_provider`、`mcp`、`persist_conversation`；
- 工厂重置新增 `mcp.json` 和 `/state`。

### Fixed

- 修复 MicroPython 中 `for i in range(...)` 绕过 globals 覆盖的问题（源码插桩）；
- 修复 MicroPython 内建模块不允许 `setattr` 导致 `time.sleep` 代理失败的问题（临时替换 `sys.modules`）；
- 修复执行超时被生成代码 `except Exception` 吞掉的问题（`ExecutionTimeout` 继承 `BaseException`）；
- 修复 OpenCode Go Anthropic 端点因缺少 `x-opencode-session` 返回 HTTP 400 的问题；
- 修复大历史下 `/api/status` 和网页回合准备阶段耗时约 6–13 秒的问题：`estimate_tokens()` 改用 UTF-8 字节长度在 C 层估算，不再逐字符遍历历史；实测状态轮询从约 6.3 秒降到 0.1–0.2 秒；
- 流式 socket 轮询从 5 秒缩短到 1 秒，串口模型回合期间网页状态请求不再长时间等待。

## Unreleased

> 说明：本段 Added/Changed/Fixed 条目保留了 0.3.0 开发过程中的历史记录；其中自定义可执行 Tool 已在 `Removed` 中移除，当前行为以 `Removed` 和 README/docs 为准。

### Added

- SSE 流式模型响应，支持 chunked、Content-Length 和连接关闭三种正文读取；
- reasoning 和正文到达时可实时打印；reasoning 文本默认隐藏，可用 `/thinking show on` 开启；并保留 finish_reason、耗时、超时和长度诊断；
- SSE 两层空闲超时：`stream_idle_timeout`（默认 120 秒，任意字节刷新）和 `stream_event_idle_timeout`（默认 300 秒，只有真实 reasoning/content/finish_reason 刷新）；SSE ping 只能刷新字节层；
- 流式响应头使用独立的 `stream_header_timeout`（默认 300 秒），不再随 thinking 级别自动升高；非流式请求仍按 thinking 使用至少 600/900 秒；
- `finish_reason` 到达即完成，不要求服务端继续发送 `[DONE]`；
- 已输出正文后的超时/断流标记为 partial，不自动重试；thinking 降级只用于尚未输出正文的流；
- 串口 Ctrl+C 和网页断连可在响应头等待、隐藏 reasoning 和正文阶段取消当前请求；
- `Retry-After` 使用独立的 `api_retry_after_max_ms`（默认 300000），不再被 30 秒退避上限截断；重试退避增加 0–25% jitter；
- 模型结果新增 header/idle/event-idle、headers、first-token、token-events、max-gap 和 tail-gap 诊断，网页显示首字、最大间隔和尾间隔；
- 修复执行输出捕获中 `BufferWriter.write()` 对 UTF-8 返回字符数而不是字节数，导致多参数 `print` 的中文输出被截断；
- 约 200K token 的近似上下文预算，按 ASCII/非 ASCII 分别估算；
- 自动上下文压缩：达到阈值后摘要较早消息，保留最近消息并注入系统提示词；
- 手动 `/compact`、`/context`、`/summary` 命令；
- `stream_response_limit` 独立限制 SSE 总字节；
- 压缩请求临时关闭 thinking，避免 reasoning 占用摘要输出预算；
- Skill 对齐 Claude Code：`/skills/<name>/SKILL.md`，带 frontmatter，系统提示词只加载目录，模型用 `__skill read` 按需加载正文；
- `__skill delete`：模型可在用户明确要求时删除 Skill 目录；
- 恢复出厂设置：串口 `/factory-reset`（确认后输入 `RESET`）和 Web 设置页 / `POST /api/factory-reset`（当前管理密码 + `confirm:true`）；删除配置、秘密、`MEMORY.md`、recovery 文件、Skill、Tool、Workspace 和对话状态后重启，保留固件、启动文件和公共 CA；
- Tool：`tooldef` 块提出的可执行 `run(args, api)`，保存后立即进入模型工具列表，无需批准；
- Tool 沙箱：受限 builtins、静态源码检查、源码校验和、私有 `/tools/<name>/` 数据目录和大小限制；
- `/tools`、`/tool`、`/tool-show`、`/tool-delete` 命令；
- `/MEMORY.md` 始终加载的项目上下文，模型可通过 `__memory` 读取/追加/删除/替换；
- `/memory`、`/memory append`、`/memory delete`、`/memory replace`、`/memory clear` 命令；
- MEMORY.md 按行编辑：`delete` 删除所有包含匹配文本的行，`replace` 要求唯一匹配并重写整行，均受 `memory_append_limit` 和总长度限制；
- GPIO、I2C、SPI、UART、NeoPixel、ADC 能力 API；外设默认关闭，按引脚/总线显式配置；
- `/peripherals` 子命令：enable/disable/clear、gpio、i2c、spi、uart、neopixel、adc、remove、scan；
- NeoPixel 能力：`neopixel_strips` 配置、单条灯带最多 64 像素，`api.neopixel.write/fill/clear/read`；支持 GPIO48 板载 RGB LED；
- ADC 能力：`adc_pins` 配置和 `api.adc.read`，采样前自动设置 11 dB 衰减；
- 保留 Flash/PSRAM、USB、strapping 和控制台 UART 引脚保护；
- 自动审批模式：`auto_run` 自动执行生成代码并自动反馈，`auto_save_tools` 自动保存 Tool；
- `/auto`、`/auto run`、`/auto tools` 命令，默认关闭并显示醒目的安全警告；
- 默认 thinking 级别改为 `high`；
- 默认 `max_tokens` 提高到 32768，减少高 thinking 先耗尽输出预算的情况；
- 高 thinking 流在尚未输出正文时空闲超时，会按 `thinking_fallback`（默认 low）重试一次；已输出正文后不再自动重试；（0.4.0 起超时不再降级，改为按原 thinking 等级重试）
- `finish_reason=length` 时自动提高 `truncation_retry_max_tokens`（默认 49152）重试，仍截断再降级 thinking；
- 非流式高 thinking 请求超时最低 600 秒，`max` 最低 900 秒；流式请求改用独立的首字节预算；
- 单任务 `max_tool_calls` 默认提高到 16；
- `stream_response_limit` 默认提高到 16 MiB，适配高 thinking 的长 SSE 流；
- 开启 `show_thinking` 后，流式 reasoning 打印受 `thinking_print_limit` 限制，避免高 thinking 刷爆串口；
- 自动运行静态保护：明显包含 `while True`/`while 1` 的代码不会被自动执行，必须人工 `/run`；
- 技能名禁止 `__` 前缀，保留给内置工具；
- 新增内置 `__search`：调用 DeepSeek Search API，支持 `count`、`need_content`、搜索语法和 `/search <query>` 手动调用；
- 新增搜索配置：`search_enabled/search_url/search_count/search_max_results/search_timeout/search_response_limit/search_output_limit/search_content_limit/search_include_content`；
- 新增可选 secret `search_api_key`，为空时回退到 `api_key`；
- 新增 `certs/ca-bundle.pem`：Mozilla 全量公共根证书 bundle（118 个根，约 200 KB），可直接安装为 `/ca.pem`，支持更换 API 地址和访问其他公共 HTTPS 服务；
- 新增 `certs/README.md`，记录 bundle 来源、日期、大小、SHA-256、安装和切换方法；
- 新增 `/workspace` 共享工作区和 `/workspace/tmp` 临时区；
- 模型内置 `__workspace` 工具：`summary/list/read/write/mkdir/remove/stat/run`；
- 新增 `/workspace run <path>`：显示源码并确认后执行工作区 `.py` 脚本；
- tool 协议容错：`tool_call`/`arguments` 别名、字符串 `args`、action 大小写不敏感、未知工具列出可用列表、`max_format_retries`（默认 1）修复循环；
- 动作协议错误改为回传模型重试：混用 `tool`/`skill`/`tooldef` 与 Python 代码块、一次返回多个动作、空代码块、超长代码块和未闭合代码块都会把具体错误与纠正提示作为本地观察送回模型，而不是立即终止任务；每次有效动作后重试预算重置；系统提示词同时明确禁止在同一回复中混用工具调用和代码块；
- tooldef/skill 头、元数据和源码校验错误（如 `__name__`/`if __name__ == '__main__'`、缺少 `run(args, api)`、非法 capability、缺少 description）现在会收到针对性纠正提示，并至少重试 2 次，避免自动反馈因模型重复同一个格式错误而突然停止；
- `__workspace run` 在 `auto_run` 开启时可直接执行脚本并把输出返回模型；关闭时显示源码并要求串口确认，拒绝则取消；
- Tool 新增 `workspace` 能力和 `api.workspace`；
- `/workspace` 用户命令、启动清空 tmp、路径约束和容量限制；
- 模型 API 瞬时失败自动重试：408/429/500/502/503/504/529、连接失败、超时和流中断按指数退避重试，默认 3 次；支持 `Retry-After` 并受独立的 `api_retry_after_max_ms` 上限约束；401/403、证书错误、大小超限和截断不重试；
- `api_retries`、`api_retry_base_ms`、`api_retry_max_ms`、`api_retry_after_max_ms`、`api_retry_jitter_pct` 配置项；压缩请求和 `/test api` 复用同一重试逻辑；
- `stream_header_timeout`、`stream_event_idle_timeout` 配置项；Web `/api/config` 可读取/更新流式超时和重试参数；
- 压缩请求改为流式调用（`stream=false` 时回退非流式），新增 `summary_timeout`（默认 300 秒），避免网关上的非流式摘要请求超时；
- 网络错误分类识别 `ETIMEDOUT`、`EPIPE`、`EHOSTUNREACH` 等 errno 名称，修复被 `HTTPError` 包装后不再重试的问题；
- 每个会话生成稳定的 `x-opencode-session: ses_...` 请求头，兼容 OpenCode Go 的会话路由要求；`/clear` 时轮换；
- 非流式请求 User-Agent 更新为 `esp32-harness/0.2`；
- 新增 `tools/serial_console.py` 简单交互式串口终端：按 UTF-8 字符边界分块限速发送，过滤设备对当前输入行的回显，避免本地回显和设备回显重复；适合长中文输入和任意串口设备；`:quit`/`:exit` 退出，`Ctrl-C` 发送 `0x03`，支持 `--baud`/`--chunk`/`--delay`；
- 新增设备端 Web 控制台：后台 `_thread` HTTP/1.1 服务器、静态 SPA、管理密码登录、RAM 会话 Cookie、登录限速、Host/Origin/X-Harness 校验、严格 CSP 和安全响应头；
- Web API：`/api/setup`、`/api/login`、`/api/logout`、`/api/status`、`/api/history`、`/api/config`、`/api/config/ca`、`/api/auto`、`/api/chat`、`/api/run`、`/api/discard`、`/api/save-skill`、`/api/save-tool`、`/api/command`、`/api/wifi/connect`、`/api/test`；
- 聊天和执行使用 NDJSON 流：`start/model_start/reasoning/content/notice/model/tool/response/code/execution/skill/tooldef/saved/feedback/done/error`；
- 网页端对话、代码运行/丢弃、Skill/Tool 提案、命令页、Wi-Fi/模型/API Key/CA/管理密码/AP 密码/thinking/自动模式设置和状态轮询；
- 网页对话新增停止按钮：浏览器停止接收后，设备端通过 HTTP 客户端的取消检查在响应头等待、reasoning 和正文阶段中止当前模型请求并释放回合锁，不保存部分回答；页面等待真实回合结束，已开始的代码或 Tool 不会被强制中断；
- 对话/命令导航栏固定在页面底部，输入框位于导航栏上方；
- 流式 reasoning/正文默认每 32 字符批量发送，可用 `web_stream_batch_chars` 调整；
- 网页思考框在用户接近底部时自动跟随滚动，摘要显示已接收字数；
- 网页 reasoning 和正文显示都不再受 `web_output_limit` 限制，长 thinking 和长回答可以完整显示；思考框改为增量追加文本节点，正文流式渲染对纯文本走增量追加，避免长文本反复重建 DOM 造成浏览器卡顿；
- 首次使用或 Wi-Fi 失败自动开启 WPA2 AP：`ESP32-Harness-XXXX` / 默认 `esp32setup` / `192.168.4.1`；AP 宽限期、断线重开 AP、STA 自动重连和首次设置 AP-only 限制；
- 新增 `/web` 和 `/web reset-password`；`/setup` 末尾可设置网页管理密码；
- 新增 `web_request_timeout_s`（默认 30 秒）：请求头和正文共享完整读取总期限，防止慢客户端通过断续发送无限占用唯一 Web 线程；
- 新增 `Config.save_all()`：Web 首次设置和配置更新在第二次写盘失败时恢复两个文件旧内容；密码会话只在全部保存成功后轮换；
- 自定义 CA 改为 Web 设置中的高级折叠项，并提示全量 bundle 已安装时保持原样即可；CA 缺失时自动展开提醒，串口 `/model` 和 `/setup` 同步补充说明，TLS 验证默认不变；
- 对话框旁新增 Thinking 强度和 AUTO 控件，设置页不再重复；AUTO 同时切换 `auto_run` 和 `auto_save_tools`，支持半选状态、开启确认和保存失败恢复；
- 代码、Tool/Skill 块、执行输出、Tool 调用详情和历史本地记录改为默认折叠展示，点击摘要查看全文，流式更新保留展开状态；
- 新增一组 `web_*`/`ap_*` 配置项和 `web_password_hash`、`web_password_salt`、`ap_password` secrets；
- 新增 `UIRouter` 按线程路由 UI 输出；网页和串口共享历史，`turn_lock` 互斥，串口只读命令在网页回合期间仍可用；
- 新增主线程执行 handoff：`Application.run_on_main/pump_main_request`、`Executor.main_runner` 和 `ToolRunner.main_runner`；网页触发的代码、普通 Tool 调用和执行器工作区脚本都回到串口主线程，`os.dupterm` 只在主线程调用；
- 网页自动模式与串口 `/auto` 共用 `auto_run`/`auto_save_tools`/`auto_feedback`，需要二次确认，仍执行无限循环保护；
- Web 线程使用 32 KB 栈；看门狗只由主线程喂，网页请求/模型请求期间由空闲主线程喂狗，网页触发的死循环代码或 Tool 仍会复位并恢复 pending；
- 新增 `docs/web.md`，并更新 README、setup、commands、architecture、development、safety 和 AGENTS；
- 版本升至 `0.3.0`，User-Agent 更新为 `esp32-harness/0.3`。

### Removed

- 移除模型自定义可执行 Tool：删除 `tooldef` 提案、`/tools`、`/tool`、`/tool-show`、`/tool-delete`、`auto_save_tools`、Tool 存储/运行器/数据目录、Web `/api/save-tool` 和 Tool 提案卡；保留 `__skill`、`__workspace`、`__memory`、`__search` 内置工具；
- 旧 `/skills/*.py` 迁移现在只生成 Markdown Skill；恢复出厂设置仍清理旧版 `/tools` 目录；
- 移除 Tool 专属的受限硬件 API、`WorkspaceAPI` 和 `PeripheralAPI`；`/peripherals` 配置和 `scan` 保留。

### Changed

- Web 控制台默认端口从 `8080` 改为 `80`，页面地址变为 `http://<设备IP>/`；`web_port` 仍可配置；
- thinking 文本显示默认关闭（`show_thinking=false`）；新增 `/thinking show on|off`（别名 `/thinking display`）开关，开启后才打印 reasoning 正文；结果元数据仍显示 reasoning 字符数；
- 串口 Skill 查看和删除命令改为 `/skill-show` 和 `/skill-delete`，与 `/tool-show`、`/tool-delete` 对齐。
- `/code` 和 `/peripherals scan` 改为独占命令，分别避免提案保存/丢弃竞态和并发 I2C 事务；
- `/api/history` 的字符预算优先保留最新消息，再按时间正序返回；
- thinking 的 `middle` 统一归一化为 `medium`，串口、Web 和降级重试共用同一别名表；
- Web JSON 布尔字段严格要求 `true`/`false`，不再用 Python 真值规则解释字符串；
- 开源整理：新增 MIT `LICENSE`、`SECURITY.md`、`requirements.txt`；README 重写为面向开源用户的结构，文档统一使用 `<PORT>` 占位并说明 `HARNESS_PORT`/自动检测；主机工具不再硬编码机器特定串口；
- 文档和验证记录移除本地串口、临时测试配置等机器特定信息；仓库仍不包含真实配置、秘密、固件或串口日志。

### Fixed

- 修复重复模型请求前的 `connect()` 不断重置 AP 宽限截止时间的问题；同一 STA 的重复连接确认不再续期，只有真实掉线恢复或显式目标切换才重新计时，ESSID 查询失败路径也不会反复续期；
- 修复网页 Tool 在 Web 线程直接执行、死循环时主线程仍喂狗并永久持有回合锁的问题；所有 Tool 统一回主线程；
- 修复 GPIO 撤销授权后仍可写、I2C/SPI/UART 重配置后仍使用旧总线，以及 NeoPixel 数量变化仍使用旧灯带的问题；
- 修复当前 ESSID 查询失败时跳过目标 AP 切换并把旧连接误报为成功的问题；未知 SSID 现在显式重连目标网络；
- 修复 Web 配置用 `bool("false")` 误开启自动运行等选项的问题；
- 修复 setup 中途写盘失败导致下次直接 409、普通配置仅恢复 RAM 而 Flash 未回滚，以及保存成功后响应发送失败又错误回滚 RAM 的问题；
- 修复 `/api/status` 在 pending 类型和源码之间发生串口清理时抛出 `AttributeError` 并返回 500 的问题；
- 修复 `/api/history` 预算不足时优先丢弃最新消息的问题；
- 修复 Web“测试 Wi-Fi”错误依赖模型、API Key 和 CA 配置的问题；
- 修复 WebServer 停止超时后 `start()` 假成功但不建立 listener 的问题；`stop()` 现在返回实际停止状态；
- 修复 LF-only/CR-only 串口输入把下一次空行确认吞掉的问题；串口直接读取 `sys.stdin.buffer`，绕过 `TextIOWrapper` 的 CR→LF 转换，设备上 CRLF/LFCR 仍只产生一次换行且保留真实空行；
- 修复 Tool 静态检查使用 `str.isalnum()` 在 MicroPython 上抛出 `AttributeError` 的问题，改为 ASCII 字母/数字范围判断；
- 修复 Tool 列表 limit 按目录项而不是实际 Tool 计数的问题；
- 修复 Tool 静态检查误杀 `pos.`、普通注释和字符串正文的问题，保留真实危险标识符和 `__` 限制；
- 修复网页命令收到 HTTP 200 + `ok:false` 时前端忽略错误、只显示空白的问题；
- 修复网页从命令页切回对话页后滚动到顶部、必须手动下滑才能看到最新消息的问题；标签切换和刷新历史定位到底部，新内容仅在接近底部时自动跟随；
- Wi-Fi 连接失败时读取 `WLAN.status()` 并把状态附加到错误信息；密码错误或找不到 AP 仍会快速失败，但会先等待 5 秒扫描/关联稳定窗口。ESP-IDF 在新连接开始后可能仍返回上一次的 `STAT_NO_AP_FOUND`（MicroPython 1.29 中为 201），此前会在约 200 ms 内把这种临时状态误判为最终失败，导致 AP 模式下第一次改回正确 Wi-Fi 失败、第二次或复位后才成功；
- 修复非 DeepSeek 模型（例如 OpenCode Go 的 `iceman-contributor-free`）在 thinking 非 disabled 时仍使用 120 秒请求超时的问题；现在所有模型在 thinking 非 disabled 时都至少使用 600 秒、`max` 至少 900 秒。真实设备上小请求 8 秒成功，但长提示首次响应头曾连续超时，需要重试才成功；
- 修复 SSE 流式正文被固定总超时截断的问题：现在每收到一段正文或事件都刷新空闲计时，只有连续 `stream_idle_timeout` 无数据才中止；`request_timeout` 仍约束响应头等待和非流式请求，`stream_response_limit` 约束总字节；
- 默认 `stream_idle_timeout` 从 30 秒提高到 120 秒，并让正文 socket 每 5 秒唤醒喂狗。真实网页压力测试中网关经常在输出部分 reasoning/正文后停顿 30–90 秒，30 秒会误杀仍在进行的长回答并触发重试，表现为正文突然停住后重新开始；
- 修复 ESP-IDF 仍在连接时再次调用 `WLAN.connect()` 导致 `Wifi Internal State Error`（`sta is connecting, cannot set config`）的问题：连接前先断开并等待驱动状态稳定，失败/超时后清理在途连接，内部状态错误自动断开并重试一次，同时校验实际连接的 SSID；清理在途连接失败时返回明确错误而不是裸 OSError；只有本次调用实际发起连接且未连上时才断开驱动，避免 `feed()`/状态采集等无关异常拆掉健康链路；
- 修复 MicroPython `bytearray` 不支持切片删除导致的 SSE 读取失败；
- 修复执行输出在串口重复显示的问题；`os.dupterm()` 已实时显示的输出不再二次打印；
- 捕获输出时统一 CRLF/CR 为 LF，避免执行结果出现多余空行；
- 修复无语言标签的代码块被误判为可执行 Python 的问题；现在只有显式 `python`/`py`/`micropython` 代码块会进入待执行状态；
- 丢弃待执行代码时写入本地事件，使后续多轮对话能正确理解代码已被丢弃；
- 修复多个名称时 `/skills`/`/tools` 对字典排序导致的错误；
- Tool I2C API 支持省略总线号，默认总线 0；
- 新增 `api.i2c.get(bus)` 总线对象，兼容 `writeto`/`readfrom` 等常见 Tool 写法；修复迁移前 `oled_text` 的 `'I2CAPI' object has no attribute 'get'` 错误；
- 自动迁移旧 `/skills/*.py` 记录：带 Tool 元数据的转成 `/tools`，普通程序转成 `SKILL.md`；
- 流式响应异常时也会保留已读取的字节数和 reasoning 字符数；
- 流式正文仍完整缓冲后解析代码，代码确认执行语义不变；
- 修复 MicroPython 上自定义 `WebError` 调用 `Exception.__init__` 导致所有认证/参数错误变成 500 的问题；
- 修复 `unquote()` 把 UTF-8 百分号编码按字节转字符导致的乱码；
- 修复 `WebServer` 无条件调用 `socket.getsockname()` 在 MicroPython 上失败的问题；
- 修复 Web 服务器错误通知因 `WebApp` 缺少 `queue_notice` 被静默丢弃的问题；
- 修复网页输出预算被 reasoning 占满后正文完全不可见的问题；reasoning 与正文分别受 `web_output_limit` 约束；
- 修复状态响应缺少 `ap_ip`、网页待处理项刷新丢失、设置页密码输入未清空和聊天 Enter 发送等问题；
- 修复 `.card-view{display:flex}` 覆盖 `[hidden]` 导致登录、首次设置和主界面同时显示的问题；
- Web 顶栏和串口 `/web` 显示 AP 宽限关闭倒计时，便于确认 AP 仍开启的原因。

### Verified

- 280 项 CPython 单元测试；
- 真实设备 Web 控制台：STA 登录、静态资源、Host/Origin/X-Harness 拒绝、401/403/409/415/421 错误、配置和状态脱敏、白名单命令、AP-only 首次设置限制和 `/web reset-password`；
- 设备烟雾测试覆盖 `_thread`、32 KB Web 线程栈、localhost HTTP 解析/响应、`WebAuth` 哈希/会话、`os.dupterm` 主线程 handoff、模型驱动 `__skill delete`、外设缓存重建、词法检查和 HTTP 总超时；
- 设备上的恢复出厂设置文件删除测试使用临时目录，验证配置/秘密/MEMORY/recovery/Skill/Tool/Workspace 被删除，`boot.py`/`main.py`/`ca.pem` 保留；串口 `/factory-reset` 确认提示可取消，Web `/api/factory-reset` 未认证返回 401；
- Node DOM 行为检查：命令页切回对话页滚动到底部、新内容仅在接近底部时跟随、隐藏对话页不自动滚动；
- 串口 LFCR/CRLF 和中文 UTF-8 输入烟雾测试通过；
- 设备实测：串口把 SSID 改为不存在的 `Definitely-Not-An-AP`（密码留空保留原值），约 5 秒后返回 `Wi-Fi connection failed: AP not found ...` 并进入 AP 模式（192.168.4.1）；**不重启**把 SSID 改回原网络后 2–3 秒内重新获得 STA IP，`/status` 显示 `mode: apsta`；测试后 `secrets.json` 与测试前备份逐项一致，临时备份已删除；
- 设备实测修复前的首次在线切换：失败一次并开 AP 后，第一次 `Network.connect()` 在 214 ms 内误报 `AP not found`，第二次 2.2 秒成功；修复后同一路径第一次 2.2 秒成功；
- 设备实测在已有 STA 连接尝试进行中再次调用 `Network.connect()`：自动清理在途连接，不再出现 `Wifi Internal State Error`；清理失败和重试失败均有明确错误，无关异常不会断开已连接的健康链路；
- 设备实测 OpenCode 长提示：120 秒配置下首次响应头超时，短提示重试后可成功；把 `request_timeout` 提高到 600 秒做对照，长提示第一次等待响应头 603 秒后超时，重试 85 秒成功，输出 4612 字符，总耗时约 691 秒。据此把 thinking 非 disabled 的 600/900 秒最低超时应用到所有模型，并保留重试；
- 串口长对话压力测试（关闭 thinking，临时 `request_timeout=150`、`api_retries=8`）：三轮提示 123/125/122 字，分别耗时 46.0/37.3/37.8 秒，正文 2120/1520/约 1500 字，形成 6 条消息、5501 字符历史；`/compact` 在 43.7 秒内压缩 2 条旧消息、保留 4 条并生成 2269 字符摘要，压缩后暗号回忆 97 字、10.5 秒成功；
- 网页 API 压力测试：首轮在网关连续响应头超时后返回结构化错误且不写历史、释放回合锁；后两轮成功输出 2591 和 3853 字正文（1354.9 和 344.2 秒）；网页 `/api/command /setup` 返回 400，浏览器断开后 111.8 秒释放 `busy`，最终历史 8 条、`busy=False`；
- 真实浏览器 UI 压力测试（Playwright + Edge，420×720 视口）：登录成功，长对话增长到 5787 字符，设置页、恢复出厂按钮和 Wi-Fi 设置可见，命令页 `/status` 正常；从命令页切回对话页后页面位于底部、`#chatLog.scrollTop` 为 0；控制台只有登录前预期的 401；
- 真实网页流式回归（120 秒空闲上限）：36.6 秒完成，2481 字正文、265 字 reasoning，最大 token 间隔 7.4 秒，无 `SSE idle timeout`；此前 30 秒配置在相同网关下多次在输出 693–1299 字后误杀并重试；
- Web 线程在 32 KB 栈上完成真实 HTTPS/TLS 请求（example.com，CA 验证开启）；
- 真实 DeepSeek `/test api` SSE 流式响应；
- `/thinking high` 实时 reasoning 和正文输出；
- 手动 `/compact`、`/summary`、`/context` 及压缩后上下文回忆；
- 低阈值自动压缩、摘要合并和压缩后上下文回忆；
- OpenCode Go 网关上的流式摘要压缩：8152 tokens 触发，压缩 20 条消息、保留 4 条、摘要 639 字符；第二次 8234 tokens 触发，压缩 22 条消息并把旧摘要合并到 912 字符，均无失败；
- 两次压缩后模型都能正确回忆只在首轮出现的暗号 `ORANGE-42`；压缩后 `hw_probe` 仍能控制 GPIO48 板载 RGB LED 并读取 GPIO4 ADC；
- 16 轮约 730 字符长消息加 8 轮延续的串口压力测试，验证限速长行输入、自动压缩触发和摘要合并；
- 模型创建 `board-notes` Skill（SKILL.md），`/skill-show` 和 `__skill read` 均返回正文；
- 模型创建 `echo_tool` Tool，保存后立即出现在 `/tools`，`/tool` 返回 `hello-tool`；
- 旧 4 个 Plugin 自动迁移为 `/tools`，OLED 工具继续可用；
- 模型在 high thinking 下直接回答并把 `__skill`/`__memory` 加入上下文；
- `/MEMORY.md` 追加后，模型在后续请求中正确回答 115200；
- 模型通过 `__workspace write/read` 写入并读取 `/workspace/tmp/hello.txt`，重启后 tmp 自动清空；
- 模型生成并保存 `ws_demo` Tool，通过 `api.workspace` 写入 `/workspace/tmp/plugin.txt` 并成功读取；
- SCL=15、SDA=16 上扫描到 SSD1306 `0x3C`，迁移后的 `oled_text` 工具显示 Hello；
- 高 thinking 下的 4 MiB SSE 流限制和 8 KiB 实时 reasoning 打印上限通过真实请求验证；
- 中文任务输入、流式输出和本地执行闭环。

### Pending hardware verification

- 恢复出厂设置的完整端到端清空（会真实删除 `config.json`、`secrets.json`、Wi-Fi/API 凭据和用户数据）尚未在参考板执行，以免破坏现有配置；文件删除逻辑、串口确认提示和 Web API 鉴权已在设备验证。执行前应自行确认可重新配置 Wi-Fi 和管理密码。

## 0.2.0 - 2026-11-07

### Changed

- 目标硬件迁移到 ESP32-S3 N16R8（16 MB Flash + 8 MB Octal PSRAM）；
- 固件升级到 MicroPython v1.29.0 `ESP32_GENERIC_S3-SPIRAM_OCT`；
- 移除经典 ESP32 的 TLS 预热、keep-alive 心跳、请求写前日志、session worker 和自动恢复重启；
- 恢复 Claude Code 式客户端会话模型：每轮重放消息，HTTP/TLS 连接独立建立或按需复用；
- 主机工具不再硬编码默认串口，支持 `--port`、`HARNESS_PORT` 和自动检测；
- 保留中文 UTF-8 串口输入、代码确认执行、技能和看门狗恢复；
- 版本号升级为 0.2.0。

### Fixed

- DeepSeek thinking 默认耗尽 900 token 输出预算导致空 `content`；
- Harness 曾把空 assistant 内容当作正常回复写入历史；
- 现在拒绝 `finish_reason=length` 和空模型响应；
- `max_tokens` 提高到 4096，请求超时提高到 120 秒，响应上限提高到 64 KiB；
- 成功执行后的自动反馈不再鼓励生成无关的额外代码块；
- 系统提示和设备能力描述改为 ESP32-S3 N16R8；
- 新增 `/thinking`：disable/default/low/middle/high/max；
- 每个请求打印 thinking、耗时、超时预算、finish_reason、reasoning/content/body 长度；
- 打印 bounded `reasoning_content`，便于诊断空回复、截断和超时；
- thinking 非 disabled 时自动延长请求超时，middle 按 DeepSeek 规则映射 high。

### Verified

- ESP32-S3 16 MB Flash、8 MB PSRAM，GC 空闲堆约 8.3 MB，文件系统约 14 MB；
- 54 项 CPython 单元测试；
- 设备部署、控制台、UTF-8 和本地执行烟雾测试；
- 真实 DeepSeek `deepseek-flash` 多轮对话；
- `/thinking low/high` 和 reasoning 打印；
- 中文任务 → 生成代码 → 确认执行 → 自动反馈 → 继续追问，无重启、无空回复。

## 0.1.0 - 2026-11-03

### Added

- ESP32 本地 Agent、串口自然语言交互和 `/` 命令；
- OpenAI 兼容非流式 Chat Completions 客户端；
- 有界 HTTP/TLS、会话、代码和执行输出；
- 生成代码串口确认、本地编译执行、结果反馈和恢复标记；
- 技能保存、查看、运行、删除和上一版回滚；
- Wi-Fi、模型和 CA 的串口配置向导；
- `/repl` 维护模式和 `/reconnect` 安全连接重建；
- 与 MicroPython 1.29 匹配的 `.mpy` 构建和部署，避免设备端编译碎片；
- 导入 UI 前的 TLS 预热、HTTP keep-alive 和可选心跳；
- 每次云端请求的写前日志、最小 worker 重放和跨重启会话恢复；
- PC 单元测试、设备烟雾测试、串口控制台测试；
- 架构、部署、命令、开发、扩展和安全文档；
- 按总截止时间重试短期接收超时，严格校验 Content-Length 和提前 EOF；
- 模型响应验证通过后才提交会话历史，并保留当前任务、源码和观察结果；
- 单次任务受 `input_limit` 约束，恢复代码执行后跳过无上下文自动反馈；
- Wi-Fi 重配置强制断开旧连接；
- 恢复代码禁止自动反馈，异常和观察结果均限长；
- 技能当前版与上一版分别保存任务元数据；
- 增加 DeepSeek 所需的 DigiCert Global Root G2 公开根证书；
- 模型配置会明确报告缺失的 CA 文件，`/status` 显示 CA 是否存在。

### Fixed

- 串口同时兼容 CRLF/LFCR，避免残留行结束符把 Wi-Fi SSID 等下一项输入提前提交为空；
- 交互式 Wi-Fi 配置拒绝空 SSID，并在同一会话内重新提示；
- 修复经典 ESP32 上完整 Agent 的 TLS 握手因 C 堆碎片随机失败或触发看门狗的问题；
- NTP 暂时失败但 RTC 时间有效时继续 TLS，而不是跳过云端预热；
- 服务端关闭 keep-alive 连接时，通过写前日志和最小 worker 恢复请求，避免会话与反馈丢失；
- 默认 20 秒 HTTPS 心跳，避免空闲对话后每次请求都触发恢复重启；
- 串口行编辑支持增量 UTF-8 解码，可输入中文 SSID 和中文任务。

### Verified

- 51 项 CPython 单元测试；
- Generic ESP32 / MicroPython 1.29.0 部署；
- 真实 MicroPython 编译执行、异常输出捕获、技能回滚和模拟 Agent 闭环；
- `/status`、`/help`、`/skills` 真实串口交互；
- `/repl` 进入维护模式、部署后自动恢复控制台；
- 受控死循环触发看门狗、复位恢复待审源码并确认丢弃；
- LFCR 输入同步、中文 UTF-8 SSID 接收及 Ctrl-C 取消不保存；
- 空闲 100 秒后仍复用 TLS 连接，不重启、不重新握手；
- 真实 DeepSeek `deepseek-flash` API 请求和两次连续请求；
- 完整“生成代码 → 用户确认执行 → TLS 卡死触发 30 秒看门狗 → 写前日志 → worker 重启补发 → 会话恢复 → 最终结果”闭环。

### Not automatically exercised

- 长时间运行、Flash 写前日志磨损和所有 DeepSeek 边缘节点组合；
- 除 DeepSeek 外的模型供应商；
- 无 PSRAM 经典 ESP32 在高频多轮调用下的长期稳定性。
