# Web 控制台与 AP 配网

设备内置一个可选的 HTTP 控制台：浏览器访问 ESP32 的局域网 IP 和端口（默认 `80`），登录后可以通过可收起的侧边栏进入状态、对话、任务、Skill、文件和设置。对话输入框同时接受普通消息和 `/` 命令；Wi-Fi、模型、MCP、搜索、Harness 行为、安全和恢复出厂设置都在设置分区中。串口控制台的完整能力保持不变。

网页在独立后台线程运行；前台生成代码和工作区脚本回到串口主线程，后台任务在各自 worker 线程并发执行。网页和串口共享同一份 Agent、历史、配置和待处理项，同一时间只允许一个模型回合或变更型操作。

## 启动流程

1. 配置了 Wi-Fi 但没有网页管理密码：启动时先开 AP，再连接 STA；AP 保留，方便首次设置。
2. 没有配置 Wi-Fi：直接进入 AP-only 模式。
3. Wi-Fi 连接失败：保留/启动 AP，网页和串口仍可用；修正凭据后可恢复 STA。
4. 正常连接且已设置管理密码：只连接 STA，网页通过 STA IP 访问。

默认 AP：

```text
SSID:     ESP32-Harness-XXXX   （MAC 末 4 位十六进制）
密码:     esp32setup           （仅默认值，建议首次设置后修改）
地址:     http://192.168.4.1/
```

AP 是 WPA2。首次设置只接受来自 AP 网段的客户端（可用 `web_setup_ap_only=false` 放宽，但不推荐）。

## 首次设置

1. 电脑或手机连接 `ESP32-Harness-XXXX`，密码 `esp32setup`（或已配置的 AP 密码）。
2. 打开 `http://192.168.4.1/`。
3. 设置管理密码（8–128 字符），填写 Wi-Fi，可选填写 API 协议（OpenAI 兼容/Anthropic）、Base URL、模型和 API Key。
4. 保存后会自动登录；Wi-Fi 连接在响应发出后执行。若原页面通过 STA 打开，连接切换时可能短暂断线，改连 AP 或新 IP 后重新登录。

设置完成后 AP 进入宽限期（`ap_grace_ms`，默认 5 分钟），STA 连接稳定后关闭。管理密码未设置时 AP 不会自动关闭。宽限截止时间不会因重复模型消息或普通状态检查而重置；只有真实 STA 掉线恢复或显式切换目标网络时才重新计时。

## 串口保留的能力

串口 `esp32>` 控制台继续提供全部命令。新增：

```text
/web                 显示 Web 状态、URL、端口、AP/STA 和会话数
/web reset-password  确认后清除网页管理密码和全部会话，启动 AP 和 Web
```

`/web reset-password` 是找回管理密码的方式；物理串口是信任边界。也可运行 `/setup`，在末尾设置网页管理密码。

网页 `POST /api/command` 接受状态/历史、搜索/HTTP、Memory/Skill 写入、Workspace 写入、MCP、后台任务控制、只读 `/web`、`/run`、`/run bg`、`/workspace run` 和 `/continue`。需要确认的命令会在浏览器弹出确认框，并以 `confirm:true` 重试；`/auto on`、`/mcp auto <server> on` 也走同一确认流程。

`/run`、`/run bg`、`/workspace run` 和 `/continue` 可直接在对话输入框以 `/` 开头执行，生成/工作区代码通过主线程 handoff 运行，并在浏览器确认后继续。需要第二次输入或重启设备的命令仍使用专用网页入口：`/setup`、`/wifi`、`/model`、`/ca`、`/web reset-password` 使用设置侧边栏；`/factory-reset` 使用恢复出厂设置表单；`/repl` 仅串口可用。`/memory replace <match> => <replacement>` 可在对话框直接执行。`/peripherals` 已移除，生成代码直接使用 MicroPython 原生外设库。

`/code` 仍属于独占操作：它在串口可能进入提案保存/丢弃流程。网页回合期间，串口不能把 `/code` 当作无锁只读命令使用。

## 网页能力

- 视觉：响应式布局，跟随系统浅色/深色主题；使用本地 CSS 变量、无外部字体/CDN/图片，符合严格 CSP；输入框 16px。输入区采用紧凑布局，Thinking/AUTO/发送按钮约 36–38px 高，其余主要按钮和导航目标不小于 44px。
- 侧边栏：桌面端固定左侧，可用顶部按钮或侧栏按钮收起，收起状态记在浏览器 `localStorage`；窄屏（≤900px）默认收起为抽屉，用汉堡按钮打开，选择栏目后自动关闭，背景遮罩可点击关闭。侧边栏包含状态、对话、任务（后台任务/自动化任务）、Skill、文件和设置（网络/模型/MCP/网页搜索/Harness 行为/安全/恢复出厂设置）。
- 对话：assistant 正文按 Markdown 渲染（标题、粗体/斜体、删除线、行内代码、列表、引用、分隔线、链接和管道表格）；模型输出的原始 HTML 会先转义，链接只允许 `http(s)`、`mailto` 和站内路径，代码围栏仍显示为可折叠代码卡，不按 Markdown 内联渲染。一条 assistant 回复始终放在同一个气泡里，正文段落和代码卡按顺序嵌在该气泡内部，不再因为中间出现代码围栏而拆成多个气泡；同一回合的 thinking、工具调用/结果等仍按事件各自成气泡。执行结果、问题卡、Skill 提案和代码确认卡也各自是独立气泡。reasoning 随 assistant 消息持久化（每条最多 4096 字符），刷新页面后仍显示；实时流式和刷新后的历史使用同一套气泡结构。后台任务结果和定时任务续答属于新的 Agent 消息，会在新的 turn 中显示（后台结果折叠为“后台任务结果”，定时任务显示为一条用户请求），不会与上一条回复合并。工具/执行记录默认折叠，运行、丢弃和保存按钮保持在折叠区外；流式更新会保留已展开区块的状态。
- 停止：流式期间显示“停止”按钮。点击后浏览器停止接收响应，设备端在下一批事件发送失败时中止当前模型请求；页面保持等待并轮询真实 busy 状态，回合结束后才恢复输入。若设备正在执行本页没有持有的回合（AUTO 自动继续、定时任务或串口回合），`/api/status.turn_busy` 为真时同样显示停止按钮，点击会调用 `POST /api/stop`，由主线程在下一个网络检查点或执行器检查点协作取消。浏览器自己 `/api/run` 流已经开始执行的代码不会被该按钮中断；设备自有回合在主线程上协作运行的生成代码会在 `__harness_check()`、插桩后的 `range`、所有 `while` 条件或 `time.sleep()` 处返回 `cancelled`。阻塞在 C/SPI 调用中的代码仍可能继续产生副作用，直到执行结束或看门狗复位。`timeout=` 应写在开围栏同一行；闭合围栏后的独立 `timeout=` 行和代码块首个未再使用的独立 `timeout=N` 行也会被兼容识别，其他说明文字不会改变超时。部分未提交的模型回答不会写入历史。
- 模型提问：模型调用 `__ask` 时显示问题卡（标题、问题、最多 8 个选项按钮和自定义输入）。点击选项或直接输入文本会作为下一条消息发送；问题事件不阻塞设备线程，刷新后可从 `/api/status` 的 `question` 字段恢复。已回答的问题不会再被延迟到达的旧状态插入到下一回合；只有新的 `question` 事件会重新显示卡片。系统提示词会区分 `auto_run` 模式，避免模型在自动执行时向用户索要执行输出。
- 对话框控件：Thinking 下拉框（位于输入框左侧，只有关闭/Low/High/Max 四档，默认 High），选择后立即写入 `config.json`，重启/复位后保持；AUTO 控制 `auto_run`，开启需要确认，关闭不需要。请求期间控件禁用，保存失败恢复设备实际状态。
- 布局：侧边栏承载栏目导航，顶部栏显示当前栏目、连接状态、AUTO 标记和刷新按钮；对话框固定在内容区底部，Thinking/AUTO 位于输入框左侧，桌面和手机尽量保持单行紧凑布局，手机隐藏 Thinking 文字标签。页面按输入框实际高度更新聊天底部留白。切换栏目再回到对话时页面自动滚动到最新消息；新内容到达时仅在用户接近底部时跟随，避免打断向上翻阅历史。
- 任务：后台任务不再作为聊天气泡插入对话，而是显示在“任务 → 后台任务”中，位置固定，不随新回复漂移；每个任务仍提供停止、继续、丢弃和输出抽屉。页头显示“记录 N/M · 已完成 F · 空闲 S”（`background_max_records` 是记录总数上限，包含运行中/排队任务），达到上限时提示先继续或丢弃部分结果；还有“清空已完成结果”按钮，一次丢弃全部 ready/error 记录（`POST /api/background/discard {"all":true}`，只写盘一次）。创建被拒绝时的错误也会带上 `retained N/M` 和至少需要释放的条数。“任务 → 自动化任务”列出一次性/重复任务，支持新建、编辑、启停、立即运行和删除。提示词任务到点后在主线程执行一个 Agent 回合；代码任务到点后创建后台任务。页面通过 `/api/status.schedules` 刷新任务摘要，`schedule_seq` 变化后自动刷新历史。
- 代码确认：默认显示“前台运行/后台运行/丢弃”；`auto_run=true` 时安全代码自动执行并自动反馈。
- Skill：侧边栏“Skill”页列出已保存的 `SKILL.md`，可展开查看完整内容；对话中的 Skill 提案仍显示可保存/丢弃的独立气泡，Skill 永不自动保存。
- 命令：非交互 `/` 命令直接在对话输入框执行，命令本身显示为命令气泡，输出显示为独立系统气泡；需要确认的命令会弹出确认框，交互式命令会提示使用对应表单或串口。
- 状态页“压缩上下文”按钮会在摘要请求期间显示“压缩中…”，禁用对话输入、发送和相关操作按钮，并在状态栏/聊天区提示进度；完成后自动刷新历史。网页 `/compact` 在设备主线程执行，长时间摘要期间仍会喂看门狗。
- 设置：侧边栏按网络、模型、MCP、网页搜索、Harness 行为、安全、恢复出厂设置分区显示。包含 Wi-Fi、API 协议（OpenAI 兼容/Anthropic）、Base URL、Model、API Key、TLS 验证、CA 路径、CA PEM、MCP 服务器 JSON、网页搜索（启用、provider auto/bing、实际生效提供方）、公共 HTTP 抓取（`__http` 启用、明文 HTTP 开关）、NTP 服务器、显示时区偏移、自动校时间隔、管理密码、AP 密码、思考文本显示、自动反馈、后台执行、单回合工具调用上限（`max_tool_calls`，1–128）、上下文长度（`context_tokens`，1024–2000000）、自动压缩开关与阈值（`auto_compact`、`auto_compact_tokens`，阈值需小于上下文长度）、对话持久化和恢复出厂设置。Thinking 强度和 AUTO 在输入框左侧直接操作。
- 状态页：显示网络模式/IP/Wi-Fi/可用堆/模型/会话/版本、上下文消息数/字符数/估算 tokens/占用百分比/压缩摘要字符数、当前设备回合和后台任务数；提供“清空对话”“压缩上下文”“刷新历史”按钮。清空会删除全部历史；压缩会调用模型生成摘要，可能耗时并产生一次模型请求。
- 文件：侧边栏“文件”页浏览整个 MicroPython VFS，支持进入目录、下载、查看/编辑、上传 UTF-8 文本文件、新建文件夹/文本文件、重命名和删除。`/workspace` 下的文件可写；固件、启动文件、`/harness`、`/skills`、`/state` 和其他系统文件只读，界面标记“系统”；`/secrets.json` 和 `/mcp.json` 标记“敏感”，网页不读取也不下载。`/workspace` 和 `/workspace/tmp` 目录本身不能删除或重命名，但可以管理其中的内容。文件列表最多显示 200 项，单文件编辑/上传受 `workspace_file_limit` 和总工作区限制约束；上传仅支持 UTF-8 文本，不支持二进制。下载是认证后的原始附件响应，单文件最大 256 KB。
- 恢复出厂设置：需勾选确认并再次输入当前管理密码；删除 `config.json`、`secrets.json`、`mcp.json`、`MEMORY.md`、待恢复代码、Skill、旧版 Tool 目录、工作区、`/state` 持久对话和 RAM 中的对话状态后重启。固件、启动文件和公共 CA 保留；Wi-Fi 配置清除后会回到设备 AP 首次设置流程。
- CA 配置：页面明确提示“不想自定义时保持原样即可”。已检测到 CA 文件时，路径和 PEM 上传默认收在“高级：自定义 CA”折叠区；部署器会自动把全量 bundle 安装到 `/ca.pem`，通常无需替换。检测不到 CA 文件时自动展开并提示安装。折叠只简化界面，不会关闭 `tls_verify`。
- 状态数据：STA/AP 模式、IP、AP 宽限倒计时、Wi-Fi 错误、堆、历史/上下文、待处理项、待回答问题、会话数。状态接口使用一次性 pending 快照，串口同时运行或丢弃提案不会把正常轮询变成 500。
- 历史：`/api/history` 优先保留最新消息，再按时间正序返回；字符预算不足时省略较早消息，而不是先丢掉当前对话尾部。
- 页面每 5 秒轮询状态（空闲且页面可见时）；流式请求期间暂停轮询。
- “测试 Wi-Fi”只检查 Wi-Fi、NTP/RTC 等网络条件，不要求已经填写模型、API Key 或 CA；“测试 API”才执行完整模型配置校验和真实请求。
- JSON API 的布尔字段只接受 `true`/`false`，字符串 `"false"` 会返回 400，不会被当成启用。
- 首次设置和普通配置更新先在 RAM 中完成校验，再通过 `save_all()` 写入 `config.json` 和 `secrets.json`。第二次写入失败时会恢复两个文件的旧内容和 RAM；密码会话只在全部保存成功后轮换。每个文件仍采用临时文件原子替换，但两个文件之间的掉电原子性无法绝对保证。

### 自动模式

聊天对话框旁的 AUTO 开关和串口 `/auto` 共用全局配置：

- `auto_run=false`（默认）：显示代码，用户点击“运行”后才执行。
- `auto_run=true`：模型返回的安全代码自动执行；明显包含 `while True`/`while 1` 的代码仍会被阻止，需要手动运行。
- `auto_feedback=true`（默认）：执行结果自动反馈给模型，受 `max_turns` 限制；“设置 → Harness 行为”可调整最大轮数（默认 64，范围 1–128）和单回合工具调用上限（默认 64，范围 1–128）。
- 开启自动模式需要浏览器确认和 `/api/auto` 的 `confirm:true`；页面显示红色 AUTO 标记。聊天区 AUTO 控制 `auto_run`；`auto_feedback`、`background_execution` 和 `max_tool_calls` 保留在“设置 → Harness 行为”，不会被该开关意外重置。
- `background_execution=true`（默认）：AUTO 或手动“后台运行”创建后台任务，每个任务在自己的 worker 线程执行；`/api/chat`/`/api/run` 的流立即结束。浏览器轮询 `/api/status.backgrounds`，按任务显示编号、备注、状态、耗时和输出。AUTO 模式且 `auto_feedback=true` 时，设备主线程在 `esp32>` 空闲时自动提交已完成结果并继续模型对话，网页不显示“继续/丢弃”选项；浏览器标签页关闭或隐藏也不影响。打开的网页会在自动继续期间轮询 `GET /api/live`，把 `content`/`reasoning` 增量追加到聊天气泡，因此回复像普通流式回合一样逐步出现；设备只保留一段有界快照（默认上限 `web_output_limit`），完成后网页刷新 `/api/history` 并以持久历史为准。自动继续不会为 Skill 提案或需要确认的 MCP 工具弹出阻塞式串口确认：Skill 提案保持待处理，非 `auto` 服务器的 MCP 工具调用被拒绝。被用户取消/停止的任务不会被自动重启；模型应报告停止结果，只有用户再次要求时才重新启动。手动模式（AUTO 关闭）仍由网页或 `/api/continue {"id":N}` 继续。最多同时运行 `background_max_jobs` 个（默认 4，“设置 → Harness 行为”可改 1–4），多余任务显示“排队中”。执行期间可以继续发送新网页对话、手动前台运行另一段代码，或再创建后台任务；新的待执行卡片有“前台运行/后台运行”两个按钮。网页模型/Harness 配置可以修改，只影响后续回合；Wi-Fi/CA、回退、`/clear`、`/repl` 和恢复出厂设置等维护操作仍返回 409。可以用每个任务的停止按钮或 AI 的 `__background stop` 请求停止；`timeout=0`/`infinite` 可让后台任务无截止时间运行，但停止仍依赖协作检查点。后台输出通过 `print()` 捕获，直接写 `sys.stdout` 可能丢失。结果观察会标注原任务和其他仍在运行的任务；若其间存在待执行代码、Skill 或问题，自动继续会等待这些项处理完毕。串口可用 `/run bg`、`/background`、`/continue [id]` 手动进入同一流程。

自动执行会产生多次计费模型请求，并让代码拥有完整 MicroPython 权限。仅在可信会话中使用。

### 上下文回退

网页不再提供顶栏“回退”按钮；每条真实用户消息旁显示“回退到这里”。实时发送的消息在正常完成（`done` 事件）或手动停止后设备回合结束时补上按钮，刷新历史后同样显示。停止时会轮询设备状态，确认回合结束后刷新历史，因此不会留下没有回退按钮的实时气泡。这些按钮调用 `POST /api/rewind`，删除该用户消息及其后的全部回复，只修改对话上下文，不恢复工作区文件或设备状态。后台任务运行期间拒绝回退；任务完成后回退会丢弃所有未继续的后台结果。压缩摘要不会重建。

## 并发模型

- **串口主线程**：持有前台 `os.dupterm`、REPL 和看门狗喂狗权；执行串口命令、模型回合、前台生成代码和工作区脚本。
- **Web 线程**：HTTP/1.1 服务器，单请求、一次一个连接、`Connection: close`；处理认证、流式 NDJSON、命令和配置。
- **后台 worker 线程**：每个后台任务一个 `_thread`（默认 32 KB 栈），最多 `background_max_jobs` 个同时运行；输出通过线程级 `print` 路由到任务自己的有界缓冲区，不能调用 `os.dupterm`、`machine.reset` 或喂狗。
- Web 线程不能调用 `os.dupterm`、`machine.reset`，不能直接喂狗。
- 网页触发前台执行时，Web 线程把可调用对象放入主线程请求槽；主线程在 `SerialUI.readline` 空闲钩子中执行，并回报结果。前台生成代码和工作区脚本的 `Executor.execute()` 都通过同一通道回到主线程。
- 后台任务不占用主线程请求槽：`BackgroundPool` 立即创建/排队 worker，任务在状态中从 `queued` 变为 `running`、`ready`/`error`。结果在继续前写入 `/state/background.json`，重启后已完成结果可恢复，未完成记录变为 `error` 且绝不自动重跑。
- 串口主线程在 `esp32>` 空闲时喂狗；前台代码执行期间只有 `range()`、插桩后的所有 `while` 条件、`time.sleep()` 和 `__harness_check()` 等协作钩子会喂狗，后台 worker 的喂狗调用是空操作。普通超时在 `code_timeout_s`/`timeout=` 到达时停止并返回 `status=timeout`；C/SPI 级阻塞或超长非 `range` 的 `for` 循环仍会在 `watchdog_ms`（默认 30 秒）后复位，恢复的 pending 代码不会在启动时自动执行。后台 worker 完全阻塞时主线程仍可喂狗，但该任务无法被协作停止，通常只能重启设备。
- 串口只读命令在网页回合期间仍可用；网页和串口的变更型操作通过同一把非阻塞锁互斥，冲突时返回 409 或“Busy”提示。后台任务运行期间允许对话、前台运行、继续结果和后台控制；配置/维护类 Web 操作被拒绝，`/api/status`、`/api/history` 等只读查询仍可轮询。
- 网页前台执行期间主线程不读取串口输入；后台任务不阻塞主线程，因此网页请求、串口和对话可以继续。后台任务不会让 Web 线程永久持有回合锁。

## HTTP API

静态资源：

```text
GET /          页面
GET /app.css   样式
GET /app.js    脚本
GET /favicon.ico
```

公开接口：

```text
GET  /api/setup         setup_required/mode/AP/STA/版本/last_reset/reset_note；无秘密
POST /api/setup         {password, confirm, wifi_ssid?, wifi_password?, api_provider?, base_url?, model?, api_key?}
POST /api/login         {password}
```

登录后：

```text
POST /api/logout
GET  /api/status
GET  /api/live            AUTO 后台结果继续时的有界流式文本快照
GET  /api/history?limit=N
GET  /api/config
POST /api/config
POST /api/config/ca
GET  /api/mcp           服务器公共信息、工具数和错误；不含 Header 值
POST /api/mcp           {servers:[...], confirm?:true}；省略 headers 保留旧值
POST /api/mcp/refresh   重新加载 MCP 工具
POST /api/auto
POST /api/chat          NDJSON 流
POST /api/run           NDJSON 流；{mode:"foreground"|"background",note?}
POST /api/continue      NDJSON 流；{id?:任务编号} 提交已完成结果并继续模型
POST /api/rewind        {count:N} 或 {index:绝对历史下标}；删除用户消息及其后上下文
POST /api/stop          请求取消当前串口/AUTO/定时任务回合；协作式，在下一个网络检查点生效
POST /api/background/stop     {id?,discard?} 协作式停止；discard:true 同时丢弃结果
POST /api/background/discard  {id?} 或 {all:true} 丢弃已完成/失败的后台结果
GET  /api/schedules           列出完整定时任务；?id=N 返回单个任务
POST /api/schedules           {action:"create|update|delete|enable|disable|run", id?, ...字段}
GET  /api/skills             列出 Skill；?name=NAME 返回单个 SKILL.md
GET  /api/files?path=/PATH   列出 VFS 目录，返回 kind/size/writable/editable/protected/sensitive
GET  /api/files/read?path=   读取 UTF-8 文本（最多 32768 字符）；敏感文件 403
GET  /api/files/download?path= 下载原始附件（最大 256 KB）；敏感文件 403
POST /api/files/write         {path,content}；仅 /workspace
POST /api/files/upload        {path,content,overwrite?}；仅 /workspace，UTF-8 文本
POST /api/files/mkdir         {path}；仅 /workspace
POST /api/files/rename        {path,name}；仅 /workspace
POST /api/files/delete        {path}；仅 /workspace，目录必须为空
POST /api/discard
POST /api/save-skill
POST /api/command
POST /api/wifi/connect
POST /api/test          {kind:"wifi"|"api"}
POST /api/factory-reset {confirm:true,password} 删除用户数据并重启
```

错误统一为：

```json
{"ok":false,"error":"...","code":"busy|pending|background|schedule|auth|rate_limited|invalid|confirm|interactive|timeout|internal"}
```

### NDJSON 事件

`/api/chat` 和 `/api/run` 每行一个 JSON：

```text
start, model_start, reasoning, content, notice, model, tool,
question, response, code, background, execution, skill, feedback, done, error
```

`code` 事件带有 `background` 和 `runnable` 布尔值；待执行代码始终可运行，可用“前台运行/后台运行”两种模式。`background` 事件表示已创建后台任务，并带编号、状态、原任务摘要和备注：

```json
{"t":"background","id":3,"phase":"running","auto":true,"recovered":false,"timeout_s":45,"source_chars":120,"task":"draw the dashboard","note":"dashboard"}
```

`GET /api/status` 的 `backgrounds` 数组包含每个任务的 `id`、`phase`（`queued`/`running`/`ready`/`error`）、`elapsed_ms`、`timeout_s`（`0` 表示无限）、`note`、`task`、`turns`、`stop_requested`/`stop_reason`、`discard_on_finish` 以及完成后的有界 `result`（`ok`/`timed_out`/`cancelled`/`output`/`error`/`elapsed_ms`）。`background` 保留兼容的单任务视图（优先活动任务，其次最早完成的任务），`background_active` 是布尔汇总。`turn_busy` 只表示模型回合锁；`busy` 还包含后台任务，因此后台运行期间 `busy=true`、`turn_busy=false` 仍允许发送新对话。

`GET /api/status` 的 `schedules` 数组包含每个定时任务的 `id`、`name`、`type`（`prompt`/`code`）、`enabled`、`when`（`once`/`interval`/`daily`）、`delay_s`、`at_text`、`every_s`、`timeout_s`、`next_run`、`next_local`、`next_input`、`last_run`、`last_local`、`last_status`、`last_error`、`run_count`、`attempts`、`running`、`preview`（不包含完整提示词/源码）和 `when_label`。`schedule_seq` 在每次定时任务执行后递增，网页据此刷新历史。`GET /api/schedules` 返回完整提示词/源码（`?id=N` 只返回一条）；`POST /api/schedules` 支持 `action=create|update|delete|enable|disable|run`，创建/编辑代码任务必须带 `confirm:true`。`run` 只把 `next_run` 设为当前时间并排队，由串口主线程在设备空闲时执行。任务写入 `/state/schedules.json`，恢复出厂设置会删除；RTC 无效（未同步 NTP、年份异常）时不执行。

每个后台任务提供“停止 #N/停止并丢弃 #N/继续 #N/丢弃结果”按钮，分别调用 `POST /api/background/stop {"id":N}`、`POST /api/background/stop {"id":N,"discard":true}`、`POST /api/continue {"id":N}` 和 `POST /api/background/discard {"id":N}`。停止并丢弃会立即移除排队任务，运行中任务在协作检查点结束后移除，不产生可继续的 ready 结果；多个活动任务还提供“停止并丢弃全部”。AI 使用内置 `__background start|list|status|stop|stop_discard|note|discard`。AUTO 模式下 AI 可直接用 `action=start` 提交源码并启动任务，非 AUTO 模式会被拒绝，要求改为普通代码块确认；`start` 的明显无限循环默认拒绝，必须显式 `timeout=0` 才允许。停止只设置协作取消标志，在 `__harness_check()`、插桩后的 `range`、所有 `while` 条件或 `time.sleep()` 检查点抛出 `ExecutionCancelled`，不会强制杀死阻塞中的 C/SPI 调用；完成后的 `execution` 事件包含 `cancelled`。后台输出通过 `print()` 捕获，直接写 `sys.stdout` 可能丢失。`GET /api/history` 为每条消息返回绝对 `index` 和 `kind`（`user`/`assistant`/`observation`），回退使用该 `index`；`kind=observation` 的 `Local ...` 记录不能作为回退目标。

`/api/chat` 的 `done` 事件额外返回 `user_index`，即本轮用户消息在当前 `agent.history` 中的绝对下标；网页据此在流结束时立即给实时用户气泡添加“回退到这里”按钮，无需刷新历史。其他流的 `user_index` 为 `null`。手动停止时浏览器收不到 `done`，网页会保留停止轮询，等 `/api/status` 确认设备回合结束后刷新 `/api/history`，再按每条消息的绝对 `index` 补上回退按钮。

`content`/`reasoning` 按 `web_stream_batch_chars`（默认 32）批量发送；`reasoning` 和 `content` 都不设显示长度上限；非流式最终文本按 2048 字符分片；命令输出仍受 `web_output_limit` 约束，`code.source`、Skill 和执行输出受各自配置上限约束。客户端断开或点击“停止”后，HTTP 客户端在响应头等待、隐藏 reasoning 和正文阶段通过取消检查中止当前模型回合，不保存部分回答；自动执行/反馈也会停止。

`question` 事件由模型调用 `__ask` 产生，回合随即结束：

```json
{"t":"question","header":"Approach","question":"Which approach?","options":[{"label":"I2C","description":"fewer pins"}],"allow_custom":true}
```

`done.pending` 此时为 `question`。问题同时写入对话历史；`GET /api/status` 的 `question` 字段返回同一对象，便于刷新页面后恢复问题卡。回答通过下一次 `POST /api/chat` 发送，不需要额外 API。

## 配置项

| 键 | 默认 | 说明 |
|---|---:|---|
| `web_enabled` | `true` | 是否启动 Web 控制台 |
| `web_port` | `80` | HTTP 端口 |
| `web_boot_timeout_s` | `10` | 启动时 STA 连接最长等待 |
| `web_session_ttl_s` | `43200` | 会话绝对有效期（12 小时） |
| `web_max_sessions` | `8` | 最大 RAM 会话数，最旧淘汰 |
| `web_login_fail_limit` | `5` | 每 IP 连续失败次数 |
| `web_login_lock_ms` | `30000` | 初始锁定，指数递增到 5 分钟 |
| `web_body_limit` | `32768` | 普通 JSON 正文上限（CA 路由 64 KB）；文件上传/写入按 `workspace_file_limit` 放宽，最多 262144 |
| `web_request_timeout_s` | `30` | 请求头和正文完整读取的总超时（秒，设备端限制为 1–120） |
| `web_command_limit` | `4096` | 网页命令最大长度 |
| `web_output_limit` | `16384` | 网页命令输出预算；模型 reasoning/正文不设上限 |
| `web_stream_batch_chars` | `32` | 流式 reasoning/正文每批发送字符数 |
| `web_history_limit` | `200` | `/api/history` 最大消息数 |
| `web_history_chars` | `131072` | 历史响应总字符预算 |

网页对话视图加载的是 `/api/history` 的**有界最近窗口**：最多 `web_history_limit` 条、`web_history_chars` 字符，单条普通正文最多 8192 字符、单条工具/执行 observation 最多 2048 字符（与实时工具结果一致，避免长输出挤掉 thinking）、单条 reasoning 最多 4096 字符。observation 记录会按“工具结果/执行结果/后台任务结果/Harness 记录”逐条渲染为独立气泡；`Local tool result for ...` 渲染为工具结果气泡，`Local ESP32 background execution result` 会开启新的 turn，与后续续答单独成组。assistant reasoning、Native tool call 和正文也按事件拆成独立气泡，按发生顺序重建。串口 `/history` 打印当前 RAM 中的完整 `agent.history`，因此条数可能明显多于网页；网页在有截断时会显示 `最近 X/Y 条` 的提示。两者都来自同一份 `agent.history`，不是两份独立上下文；差异只来自网页响应预算。需要查看更多时可通过 `POST /api/config` 提高 `web_history_limit`（1–200）或 `web_history_chars`（1024–262144），或在串口使用 `/history`、`/context`。自动压缩或 `_trim_history` 之后，网页需要刷新历史才能看到压缩后的结果。

| `ap_ssid_prefix` | `ESP32-Harness` | AP SSID 前缀 |
| `ap_channel` | `6` | AP 信道 |
| `ap_grace_ms` | `300000` | STA 恢复后 AP 保留时间 |
| `ap_fallback_enabled` | `true` | Wi-Fi 丢失后自动开 AP |
| `wifi_lost_grace_ms` | `30000` | 断线多久后开 AP |
| `wifi_reconnect_interval` | `60000` | STA 重试间隔 |
| `web_setup_ap_only` | `true` | 首次设置仅接受 AP 客户端 |
| `ntp_server` | `pool.ntp.org` | 自动校时使用的 NTP 服务器 |
| `ntp_sync_interval_s` | `21600` | 自动校时间隔；`0` 表示仅启动后首次 |
| `timezone_offset_minutes` | `480` | 固定显示时区偏移，UTC+8 为 `480`；范围 -720–840 |

搜索配置也可通过 `/api/config` 读取/更新：`search_enabled`、`search_provider`（仅 `auto`/`bing`）、`search_bing_url`；结果/超时等上限可通过 `config.json` 调整。`/api/config` 还返回只读的 `search_provider_effective` 和 `search_provider_fallback`，用于显示 `auto` 当前解析到的提供方。`auto` 跟随模型 API：Anthropic 使用内置 web_search，OpenAI 官方端点使用 Responses web_search，DeepSeek 直连端点使用 `deepseek_web_search` 服务端搜索，OpenCode Go 和其他兼容端点使用 Bing 免 Key 搜索；原生搜索失败时回退 Bing，显式 `bing` 不回退。独立的 DeepSeek Search API、`search_url` 和 `search_api_key` 已移除。

公共 HTTP 抓取配置：`http_tool_enabled`、`http_tool_allow_http`（默认 `false`）、`http_tool_timeout`、`http_tool_response_limit`、`http_tool_output_limit`、`http_tool_download_limit`、`http_tool_max_redirects`、`http_tool_allowed_ports`。“设置 → 网页搜索”提供启用和明文 HTTP 开关；其余通过 `/api/config` 或 `config.json` 调整。

时间设置可通过 `/api/config` 或“设置 → Harness 行为”读取/更新：`ntp_server`、`ntp_sync_interval_s`（0–86400 秒）、`timezone_offset_minutes`（-720–840）。NTP 把 RTC 写成 UTC；`__time` 只把显示结果转换为配置偏移，默认北京时间。RTC 时间不会写回本地时区。

模型请求相关超时也可通过 `/api/config` 读取/更新：`stream_header_timeout`（默认 300 秒）、`stream_idle_timeout`（默认 300 秒）、`stream_event_idle_timeout`（默认 300 秒）、`stream_first_token_timeout`（默认 600 秒）、`request_timeout`、`api_retries`、`api_retry_base_ms`、`api_retry_max_ms`、`api_retry_after_max_ms`、`api_retry_jitter_pct`。`model` 事件额外返回 `headers_ms`、`first_token_ms`、`token_count`、`max_gap_ms`、`tail_gap_ms` 以及三类超时预算，便于区分网关排队和模型输出慢；`tail_gap_ms` 是最后一次真实 token 到流结束/超时的间隔。

秘密（`SECRET_DEFAULTS`）：`web_password_hash`、`web_password_salt`、`ap_password`（默认 `esp32setup`）。秘密不会出现在 `/api/status`、`/api/config`、`/api/setup` 或广播日志中，只返回 `_set` 布尔值。

## 安全边界

- **纯 HTTP，没有服务器 TLS**。管理密码、Cookie、对话和配置在局域网内可被嗅探。只用于可信 LAN，绝不端口映射到公网。
- 加盐 SHA-256 不是密码 KDF；设备端不保存明文密码，但物理/串口访问可以重置密码并读取 `secrets.json`。
- 默认 AP 密码是公开的；首次设置完成后建议在网页修改 AP 密码。附近知道默认密码的人可能抢先完成首次设置。
- 管理员可以修改 `base_url`，这会把已保存的 API Key 发往新地址；管理员权限约等于凭据控制权。
- 网页代码执行和串口 `/run` 一样拥有完整 MicroPython 权限；自动模式风险更高。普通超时由协作式截止时间停止，绕过所有钩子的死循环仍由看门狗复位。“停止”只停止接收和后续回合，不提供强制终止当前 Python 执行的能力。
- 单 Web 线程：长对话期间其他网页请求排队；串口只读命令仍可响应。请求读取有总超时，慢客户端不能无限占用连接；但已接受的长模型回合仍会推迟网络维护，AP 宽限关闭和 STA 重连时刻应理解为不早于计划时间，而不是精确定时。
- 不实现 HTTPS、mDNS、WebSocket、Captive Portal 或端口转发；页面通过 `IP` 访问。

## 故障排查

| 现象 | 处理 |
|---|---|
| 浏览器打不开 | `/web` 查看服务器状态和 URL；确认手机/电脑与 ESP32 同网段；检查 `web_enabled` 和端口 |
| 忘记管理密码 | 串口执行 `/web reset-password`，连接 AP 后重新设置 |
| 首次设置页提示“Connect to the setup AP first” | 首次设置必须连接设备 AP |
| 修改 Wi-Fi 后页面断线 | 原 STA 已断开；设备首次保存后需约 2–5 秒完成扫描/关联，AP 信道也可能随 STA 切换。浏览器短暂断线不等于连接失败，连接 AP 或新 IP 后重新登录，并以串口 `/status` 的 `Wi-Fi:` 行为准 |
| AP 模式下第一次改 Wi-Fi 失败，第二次或复位后才成功 | 旧固件把 ESP-IDF 扫描期间沿用的 `STAT_NO_AP_FOUND`（201）误判为最终失败；当前固件会等待约 5 秒稳定窗口，升级后首次在线切换应直接成功 |
| Wi-Fi error: AP not found | ESP32-S3 只支持 2.4 GHz；确认选择的是 2.4G SSID、名称拼写和信号范围。保存后的前 5 秒仍可能在扫描，看到旧错误时请等待连接结果，不要立即重复提交 |
| Wi-Fi error: wrong password | 重新输入正确密码；确认路由器不是 WPA3-only 或 WPA2-Enterprise |
| Wi-Fi error: Wifi Internal State Error | 旧固件在 ESP-IDF 仍在连接时重复调用 `connect`；当前固件会先断开、清理在途尝试并自动重试一次，升级后重试即可 |
| 掉线后 AP 约 30 秒才出现 | `wifi_lost_grace_ms` 默认 30000ms，用于避免短暂抖动；主动保存 Wi-Fi 时会先开 AP |
| 网页对话长时间无输出 | 高 thinking 或网关排队时，首次 HTTP 响应头可能需要数分钟；流式请求由 `stream_header_timeout`（默认 300 秒，且不低于 thinking 预算）控制响应头等待，首段正文前还有 `stream_first_token_timeout`（默认 600 秒），正文开始后还有 `stream_idle_timeout`（默认 300 秒字节空闲）和 `stream_event_idle_timeout`（默认 300 秒事件空闲）保护。网关中途停顿 30–90 秒时不会立即重试，超过空闲上限才重试；已经输出正文后不会自动重试。先用串口 `/test api` 验证 API 和网络，再等待或降低 thinking；不要重复提交。若多次重试都只有响应头超时，可把单条提示缩短到约 200 字以内、适当提高 `api_retries` 或降低 thinking；这是网关排队行为，不是设备锁死 |
| 网页提示 `Model mixed a tool call with executable code` | 模型在同一条回复里同时给了工具调用和 Python 代码块。当前固件会把具体错误和纠正提示回传模型，按 `max_format_retries`（默认 1）自动重试；若仍失败，重试该任务或把要求拆成“先查/先执行，再给代码” |
| 网页突然回到登录页 | 设备很可能发生了硬件复位（尤其是 WDT_RESET），RAM 中的网页会话随之丢失。登录页会显示 `/api/setup.reset_note` 和复位原因；串口 `/status` 也可查看。重新登录后查看待审/恢复状态。长阻塞代码若无法调用 `__harness_check()`，看门狗仍会复位 |
| 网页对话一直忙 | 串口任务/另一次网页回合占用锁；串口只读命令可用。若刚点击“停止”，页面会等待设备真实回合结束，已开始的代码不能被强制取消 |
| 后台卡片长时间停在 running | 代码仍在该任务的 worker 线程执行；`elapsed` 和 `timeout_s` 可见，`/background status ID` 可看输出尾部。协作式钩子会在截止时间停止；阻塞在 C/SPI 中或不调用钩子的后台任务无法协作停止，通常只能重启。期间对话、前台运行、状态轮询和网页模型/Harness 配置仍可用；回退、Wi-Fi/CA 和恢复类操作返回 409 |
| 后台完成后没有自动继续 | AUTO 模式由设备主线程自动继续，不需要网页保持打开；检查 `auto_feedback` 是否关闭、`max_turns` 是否耗尽、模型/网络是否就绪。手动模式（AUTO 关闭）才会显示“继续 #N”和“丢弃结果”按钮，也可串口 `/continue [id]`。存在待执行代码、Skill 或问题时自动继续会等待。设备重启后可恢复 `/state/background.json` 中已完成的结果，未完成记录显示为 error |
| 定时任务到点没有执行 | 先看“任务 → 自动化任务”的“下次/上次”和 `last_error`：RTC 年份必须有效（NTP 已同步），任务必须启用，且设备当时不在模型回合、没有待执行代码/Skill/问题；设备离线或复位期间过期的任务会在下次空闲时补跑一次。代码任务只负责启动后台任务，执行失败会显示在后台结果里。连续失败 3 次后会停用一次性任务或回到下一个正常周期 |
| 后台任务显示 queued 很久 | 同时运行数达到 `background_max_jobs`（默认 4）；等某个任务完成或停止/丢弃一个排队任务。可在“设置 → Harness 行为”把并发数调到 1–4 |
| 启动后台报 `can't create thread` | ESP-IDF 内部堆不足以创建 worker 栈。Harness 会在启动前关闭空闲 HTTPS 连接、GC，并依次尝试 32/24/16/12/8 KB 栈；仍失败时降低 `background_thread_stack_kb`（例如 16）或 `background_max_jobs`，减少同时任务后重试，必要时重启设备释放内部 RAM |
| 停止后台程序后输出不完整 | 停止是协作式的，阻塞 C/SPI 调用不会立即返回；到检查点后结果标记 `cancelled`。后台输出由 `print()` 捕获，直接写 `sys.stdout` 的内容可能缺失 |
| 回退按钮提示 409 | 后台任务仍在运行，或另一个网页/串口操作占用了回合锁；等待任务完成或先停止轮询的回合 |
| 代码执行后设备复位 | 看门狗触发；重启后 pending 恢复但不会自动执行，审查后 `/run` |
| CA 保存后仍 TLS 失败 | 确认 PEM 有效、`ca_file` 路径正确、设备时间有效；大 bundle 建议部署安装 |
