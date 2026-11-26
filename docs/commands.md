# 命令手册

所有命令从 `/` 开始；其他输入会作为自然语言任务发送给本地 Agent。串口仍是完整控制面；网页命令页接受除少数交互表单和重启类操作之外的命令，生成/工作区代码通过主线程 handoff 执行。主机侧推荐用 `python tools/serial_console.py --port <PORT>` 连接；它是通用串口终端，按 UTF-8 字符边界限速发送，避免长中文输入被 UART RX 缓冲截断。Skill 名称只能包含字母、数字、下划线和连字符，最长 32 字符。

## 初始化与状态

| 命令 | 说明 |
|---|---|
| `/help` | 显示内置帮助 |
| `/setup` | 依次配置 Wi-Fi、模型和可选 CA |
| `/status` | 显示脱敏配置、内存、网络、历史、Skill、Memory 和工作区状态 |
| `/repl` | 确认后复位到维护 REPL，停止 Agent 以便部署 |
| `/wifi` | 重新配置并立即连接 Wi-Fi |
| `/model` | 配置 API 协议（openai/anthropic）、Base URL、模型、API Key、CA 和 TLS 验证；部署器会自动安装全量 CA，通常直接保留 `/ca.pem` 即可 |
| `/thinking [level]` | 查看或设置 thinking：disable/low/high/max（默认 high，保存并跨重启） |
| `/thinking show on\|off` | 显示或隐藏 reasoning 文本（默认显示） |
| `/auto [on\|off]` | 自动运行生成代码并自动反馈 |
| `/auto run on\|off` | 只切换自动运行生成代码 |
| `/native [on\|off]` | 查看/切换原生 function calling；关闭后使用文本 `tool` 块 |
| `/ca` | 通过串口粘贴并保存 PEM 根证书；仅私有 CA 或需要替换现有 bundle 时使用 |
| `/test` | 检查 Wi-Fi、NTP 和模型配置 |
| `/test api` | 发送真实的小型模型请求（瞬时失败按 API 重试策略处理） |
| `/mcp` | 查看 MCP 服务器、工具和错误；`/mcp add/remove/refresh/tools/enable/auto/timeout/protocol/read/prompt` 管理远程 MCP |
| `/web` | 显示 Web 控制台状态、STA/AP URL、端口和会话数 |
| `/web reset-password` | 确认后清除网页管理密码和会话，启动 AP 和 Web |
| `/factory-reset` | 确认并输入 `RESET` 后删除所有用户数据并重启 |

## Web 控制台

Web 控制台默认监听 `0.0.0.0:80`。正常 STA 连接后访问 `http://<设备IP>/`；首次使用或 Wi-Fi 失败时连接 AP：

```text
SSID: ESP32-Harness-XXXX
密码: esp32setup
URL:  http://192.168.4.1/
```

网页登录后可对话、执行命令页 `/` 命令、查看历史和修改 Wi-Fi/模型/API Key/CA/管理密码。网页与串口共享会话，模型回合互斥；网页触发的代码仍在串口主线程执行，串口只读命令在网页回合期间可用。

网页 `/api/command` 接受所有**非交互**命令，包括 `/status`、`/history`、`/context`、`/summary`、`/code`、`/discard`、`/undo`、`/rewind`、`/skills`、`/skill-show`、`/skill-delete`、`/thinking`、`/auto`、`/native`、`/search`、`/test`、`/compact`、`/clear`、`/memory`、`/workspace`（`run` 除外）、`/mcp`、`/http`、`/background`、`/bg` 和只读 `/web`。需要写文件或改变风险设置的命令会在网页弹出确认对话框；确认后重试执行。

`/run`、`/run bg`、`/workspace run` 和 `/continue` 也可从网页命令页执行；生成/工作区代码通过主线程 handoff 运行，执行前浏览器会请求确认。以下命令仍需要第二次输入或重启设备，使用对应的专用表单/按钮（`/repl` 仅限串口）：

- `/setup`、`/wifi`、`/model`、`/ca`、`/web reset-password`：使用设置页表单；
- `/factory-reset`：使用网页恢复出厂设置表单，需要管理密码和 `confirm:true`。

网页或单行场景使用 `/memory replace <match> => <replacement>`；只写 `/memory replace <match>` 时串口仍会提示第二行输入。

`/factory-reset` 会删除 `config.json`、`secrets.json`、`mcp.json`、`MEMORY.md`、`recovery.json`/`recovery.py`、`/state`（持久对话）、`/skills`、旧版 `/tools`、`/workspace` 和 RAM 中的对话状态，然后重启；固件、`boot.py`、`main.py`、`harness/` 和公共 CA（`/ca.pem`）保留。Web 端对应 `POST /api/factory-reset`，需要当前管理密码和 `confirm:true`。

`/code` 也会持有回合锁：它在串口可能进入 Skill 提案的保存或丢弃流程，因此不属于网页回合期间仍可使用的串口无锁只读命令。网页 `auto_run` 与串口 `/auto` 共用全局配置；开启需要二次确认。详见 [Web 控制台](web.md)。

## 会话

| 命令 | 说明 |
|---|---|
| `/history` | 显示当前有界历史；不要把它当作秘密管理器 |
| `/context` | 显示估算 token、消息数、摘要和自动压缩阈值 |
| `/compact` | 立即调用模型压缩较早历史，保留最近消息 |
| `/summary` | 显示当前会话摘要 |
| `/undo [N]` / `/rewind [N]` | 删除第 N 条最近用户消息及其后的全部回复（默认 N=1） |
| `/clear` | 清除历史、摘要、待执行代码、最近执行状态和持久化文件 |

默认 `persist_conversation=true`，已提交的历史、摘要和首个用户请求锚点会原子写入 `/state/conversation.json`，设备复位/重启后自动恢复；`/clear` 删除该文件，恢复出厂设置也会删除。网页“行为”设置可关闭持久化。对话可能包含用户粘贴的敏感文本，文件位于设备明文文件系统。上下文预算按近似 token 计算：ASCII 约 4 字符/token，非 ASCII 约 1 字符/token；默认 `context_tokens=200000`。达到消息数、字符数或 token 限制时，最旧消息会先被移除，但当前任务、源码和执行观察会被保留。单次任务超过 `input_limit`（默认 2000 字符）会被拒绝。

`/undo` 只回退对话上下文，不恢复工作区文件、Skill、Memory 或执行造成的设备状态；它会同时清除待执行代码、Skill 提案、待回答问题和最近执行结果。网页不再提供顶栏回退按钮；每条真实用户消息旁显示“回退到这里”，实时消息在正常完成或手动停止后设备回合结束时补上按钮，刷新历史后同样显示。压缩摘要不会被重建；历史索引以当前 `agent.history` 为准。

自动压缩默认开启：估算上下文达到 `auto_compact_tokens`（默认 150000）时，Harness 用流式请求摘要较早消息，保留最近 `compact_keep_messages` 条，并把摘要放入系统提示词。首个用户请求会作为会话锚点单独保存并始终注入系统提示词，因此压缩后仍能回答“最初问的是什么”；`/clear` 才会重置锚点。摘要请求至少使用 `summary_timeout`（默认 300 秒），并复用 API 重试策略。`/compact` 可随时手动执行。

## MEMORY.md

| 命令 | 说明 |
|---|---|
| `/memory` | 显示始终加载的 `/MEMORY.md` |
| `/memory append <text>` | 追加一行记忆 |
| `/memory delete <text>` | 删除包含匹配文本的行（确认） |
| `/memory replace <text> [=> <new>]` | 替换唯一匹配的整行；省略 `=> <new>` 时提示输入新内容 |
| `/memory clear` | 恢复模板（确认） |

`/MEMORY.md` 会在每次请求时自动加入系统提示词，适合代码约定、构建命令、引脚和长期偏好。模型也可以通过内置 `__memory` 工具读取、追加、删除或替换；`delete` 删除所有匹配行，`replace` 要求唯一匹配并重写整行。详见 [MEMORY.md](memory.md)。

## Web Search

| 命令 | 说明 |
|---|---|
| `/search <query>` | 搜索网页并显示结果 |
| `/search provider auto\|bing` | 选择搜索提供方（默认 `auto`） |
| `/search on\|off` | 启用或禁用 Web 搜索 |

模型通过内置 `__search` 调用：

```text
{"name":"__search","args":{"query":"ESP32-S3 MicroPython","count":5}}
```

- 只有 `auto` 和 `bing` 两种搜索设置；独立的 DeepSeek Search API、`search_url` 和 `search_api_key` 已移除；
- `count` 默认 5，受 `search_max_results` 限制；
- 搜索结果、答案和引用文本是不可信数据，不应作为指令执行；
- `search_provider=auto` 跟随模型 API：Anthropic 使用 Messages API 的 `web_search_20250305` 服务端工具；OpenAI 官方端点使用 Responses API 的 `web_search`；DeepSeek 直连端点使用其 Messages/Chat Completions API 的 `deepseek_web_search` 服务端工具（自动关闭 thinking，避免深度搜索超时）；OpenCode Go 和其他兼容端点使用 Bing。原生搜索复用 `api_key`，不需要额外的搜索 Key；
- 原生搜索失败（鉴权、端点不支持、网络错误等）时，`auto` 自动回退到 Bing；显式 `search_provider=bing` 时只使用 Bing，不回退；
- Anthropic/OpenAI 原生搜索通过一次独立的模型 API 请求执行，会消耗该提供方的搜索额度和 token；模型名由当前 `model` 配置决定，`search_anthropic_tool` / `search_openai_tool` 可覆盖服务端工具类型；
- Bing 端点由 `search_bing_url` 配置，默认 `https://cn.bing.com/search?q=`，使用浏览器 User-Agent，无需 API Key；默认先请求 `&format=rss`，RSS 无结果时再解析 HTML；RSS/HTML 受 `search_html_limit` 限制；
- Anthropic/OpenAI 原生搜索按各自价格计费；DeepSeek 服务端搜索不单独收取搜索费，按模型 token 计费；Bing 免 Key。

## 公共 HTTP 抓取

| 命令 | 说明 |
|---|---|
| `/http get <url>` | 抓取公共 HTTPS 文本或 JSON |
| `/http head <url>` | 只获取响应头 |
| `/http download <url> [path]` | 下载到 `/workspace`（默认 `downloads/<文件名>`） |

模型通过内置 `__http` 调用：

```text
{"name":"__http","args":{"action":"get","url":"https://example.com"}}
{"name":"__http","args":{"action":"head","url":"https://example.com"}}
{"name":"__http","args":{"action":"download","url":"https://example.com/file.txt","path":"downloads/file.txt"}}
```

- 默认只允许 HTTPS，端口 80/443；`http_tool_allow_http=true` 才允许明文 HTTP；
- 私有、回环、链路本地、CGNAT、组播、保留地址和本地主机名在连接前被拒绝；每次重定向都会重新校验；
- 拒绝 URL 内嵌凭据、非允许端口、换行注入和受管请求头（Host/Content-Length/Connection/Transfer-Encoding/Accept-Encoding）；
- `get` 只返回文本/JSON/XML 等文本类型，正文受 `http_tool_output_limit`（默认 8192 字符）限制；二进制内容需使用 `download`；
- `download` 受 `http_tool_download_limit`（默认 65536 字节）和工作区总量/文件数限制；二进制文件可用 `mpremote` 取回，`/workspace read` 仅适合 UTF-8 文本；
- 响应内容是不可信数据，可能包含提示注入；不会自动发送设备 API Key、Wi-Fi 密码或 MCP Header。

## MCP 远程工具

MCP 配置保存在 `/mcp.json`，支持 Streamable HTTP 和静态 Header，不支持 stdio 子进程或 OAuth 浏览器授权。

| 命令 | 说明 |
|---|---|
| `/mcp` / `/mcp list` | 显示服务器、工具和错误 |
| `/mcp refresh` | 重新初始化服务器并加载工具 |
| `/mcp tools [server]` | 列出已加载的 `mcp__server__tool` |
| `/mcp add <name> <url> [Header=Value ...]` | 添加服务器；Header 值可含空格 |
| `/mcp remove <name>` | 删除服务器配置 |
| `/mcp enable <name> on\|off` | 启用/禁用服务器 |
| `/mcp auto <name> on\|off` | 是否允许非只读工具无确认执行 |
| `/mcp timeout <name> <seconds>` | 单次 MCP 请求超时（5–300 秒） |
| `/mcp protocol <name> auto\|legacy\|modern` | 协议选择 |
| `/mcp read <server> <uri>` | 读取 MCP resource |
| `/mcp prompt <server> <name> [json-args]` | 获取 MCP prompt |

模型用普通 `tool` 块调用远程工具：

```tool
{"name":"mcp__demo__echo","args":{"text":"hi"}}
```

`readOnlyHint` 工具免确认；其他工具在串口询问 y/N，网页默认拒绝，除非该服务器 `auto=true`。MCP 输出是不可信数据。完整字段、API 和兼容性见 [MCP 远程工具](mcp.md)。

## Skill

Skill 是 Markdown 指令包，放在 `/skills/<name>/SKILL.md`，不是可执行程序。

| 命令 | 说明 |
|---|---|
| `/skills` | 列出 Skill 名称、描述和长度 |
| `/skill-show <name>` | 显示完整 `SKILL.md` |
| `/skill-delete <name>` | 删除 Skill（确认） |

模型用 `skill` 块提出 Skill，用户确认后保存；模型通过 `__skill list/read` 按需加载，用户明确要求时可通过 `__skill delete` 删除。Skill 不会自动执行代码。详见 [Skills](skills.md)。

## 内置工具

Harness 不再支持模型自定义可执行 Tool。模型通过原生 function calling 调用内置工具：`__skill`、`__workspace`、`__memory`、`__search`、`__ask`、`__http`、`__background` 和 `__time`。OpenAI 兼容端点使用 `tools`，Anthropic 使用 `input_schema`；工具结果以原生 assistant/tool 消息回传。端点拒绝原生工具时自动回退到文本 `tool` 块，也可用 `/native off` 强制使用文本协议。单次任务最多 `max_tool_calls` 次连续调用（默认 16）。`__background` 只查看/请求停止后台任务；`__time` 读取 RTC 并按配置时区返回本地时间/UTC；内置工具直接读写 Skill、Workspace、MEMORY、Search/HTTP 或后台状态，不加载任意本地代码。

`__time` 用于回答“现在几点/今天几号”，直接读取 `machine.RTC().datetime()`，不依赖模型猜测：

```json
{"name":"__time","args":{}}
```

返回值包含 `local`（带 UTC 偏移和星期）、`utc`、Unix epoch、配置偏移和 `valid` 标记。时区通过 `timezone_offset_minutes` 配置，默认 `480`（UTC+8）；设备 RTC 本身仍保存 UTC。时区是固定偏移，不自动处理夏令时。

`__ask` 是唯一会结束当前模型回合的内置工具：

```text
{"name":"__ask","args":{"question":"Which approach?","header":"Approach","options":["I2C","SPI"],"allow_custom":true}}
```

`options` 可以是字符串或 `{"label":"...","description":"..."}`，最多 8 个；`allow_custom` 默认 `true`。如果模型直接在正文中写出 `--- question for you: ... ---` 格式（而不是调用工具），Harness 也会在代码围栏外识别并转换为待回答问题；系统提示词要求模型不要手写该格式。Harness 把问题写入对话历史并结束回合。串口打印编号选项，网页显示问题卡；用户的下一条消息（编号、选项标签或自定义文本）就是回答。提问不阻塞设备线程，也不占代码/Skill 的待处理槽位；发送新任务会清除未回答的问题。系统提示词会说明当前 `auto_run` 模式下代码是否会自动执行，并要求模型不要向用户索要执行输出，需要决策时使用 `__ask`。

自动模式下，如果模型在解释中提问同时又返回 python 代码块，Harness 会把它作为协议错误要求模型纠正（改用 `__ask` 或只返回代码），不会先执行代码。示例、片段和伪代码应使用 text 代码块；python 块一律按可执行代码处理。

## 待执行代码

| 命令 | 说明 |
|---|---|
| `/code` | 显示待执行代码或待保存 Skill |
| `/run` | 确认后在串口主线程前台执行待执行代码 |
| `/run bg [备注]` | 确认后创建后台任务；可继续生成/创建更多任务，立即返回提示符 |
| `/continue [ID]` | 提交某个（或最早完成的）后台结果并继续模型回合 |
| `/background` | 列出所有后台任务的编号、备注、状态和耗时 |
| `/background status ID` | 查看单个任务及输出尾部 |
| `/background stop [ID]` | 请求停止一个任务；省略 ID 时停止全部活动任务 |
| `/background note ID TEXT` | 修改任务备注 |
| `/background discard ID` | 丢弃已完成/失败的任务记录 |
| `/discard [ID]` | 丢弃待执行代码、Skill 提案或已完成的后台结果 |

已有待执行代码时，新任务会被拒绝，避免未审代码被静默替换。执行结果默认反馈给模型；修复代码仍需确认，除非开启自动运行。

执行超时按整数秒处理：正的小数会向上取整到至少 1 秒，`0` 才表示无限（仅后台允许）。执行超时默认 `code_timeout_s=20` 秒，模型可在代码块**开围栏同一行**声明 `timeout=45`（即紧跟在语言标记 `python` 之后），有限值上限 `code_timeout_max_s=300`；后台任务可声明 `timeout=0`/`infinite`/`inf`/`unlimited` 表示无截止时间，串口/网页前台运行会拒绝这种形式。写在闭合围栏之后的普通文字不生效；为兼容模型常见写法，Harness 也会识别紧跟在闭合围栏后的单独一行 `timeout=45`，以及代码块第一行是独立 `timeout=45` 且后续源码不再使用该名字的情况（也接受空格、`s` 后缀和常见结尾标点），但开围栏形式是首选。Harness 会在执行前插桩 `range()` 和 `while True:`，并代理 `time.sleep()`；长 `while` 循环应主动调用 `__harness_check()`。截止时间到达后抛出内部 `ExecutionTimeout`，返回 `status=timeout` 和“设备未重启”的观察，自动反馈循环可以继续。超时只停止当前 `exec`，不会停止之前代码创建的 Python 线程。不调用任何钩子的 C 级阻塞仍由 30 秒看门狗兜底；自动模式仍会阻止明显无限循环。

### 后台执行

短程序应优先在前台执行；只有长时间运行、需要与用户继续交互并行，或用户明确要求时才使用后台，并应说明原因。`background_execution=true`（默认）时，网页 AUTO 运行和手动“后台运行”会创建后台任务；每个任务在自己的 worker 线程执行。`__workspace action=run` 支持 `background=true`，串口可用 `/workspace run bg <path>` 直接把工作区脚本放入后台。`/api/chat` 或 `/api/run` 的 NDJSON 流在任务入队后结束，浏览器通过 `/api/status.backgrounds` 轮询所有任务。AUTO 模式（`auto_run=true`）且 `auto_feedback=true` 时，设备主线程在 `esp32>` 空闲时会自动把已完成结果写入上下文并继续模型对话；网页只显示状态，不再要求手动选择“继续/丢弃”，即使浏览器标签页关闭或隐藏也能继续。自动继续期间网页轮询 `GET /api/live` 增量显示回复，完成后以 `/api/history` 为准。AUTO 关闭或 `auto_feedback=false` 时保留手动“继续/丢弃”按钮和串口 `/continue`。最多同时运行 `background_max_jobs` 个（默认 4，可在网页设置中改为 1–4），超出的任务保持 `queued`，槽位空出后自动启动。结果观察会带上原任务摘要，并提示“其他后台任务可能仍在运行、后续用户消息可能无关”，避免把另外插入的问题混进原任务。任务记录保存在 `/state/background.json`（受 `persist_conversation` 控制，默认最多 8 条）；设备重启后已完成结果可恢复，未完成记录显示为 `error` 且不会自动重跑。降低 `background_max_records` 只会裁剪失败/非 ready 记录，不会静默丢弃可继续的 ready 结果。

后台运行期间**仍可发送新对话、前台运行代码或再创建后台任务**。AUTO 开启时，新生成的代码会创建新的后台任务；槽位满则排队。手动待执行卡片提供“前台运行”和“后台运行”两个按钮；前台运行回到串口主线程，与后台任务并行。`__workspace run` 也回到主线程，可以运行。配置写入、回退、`/clear`、`/repl`、恢复出厂设置等维护操作在任务运行期间仍被拒绝。AUTO 模式下新的对话结束后，如果某个后台结果已就绪，设备主线程会按 `auto_feedback` 自动继续该任务（手动模式由网页轮询继续）；若存在待执行代码、Skill 提案或问题，则先等待处理，之后再自动继续。`/api/status` 的 `turn_busy` 只表示模型回合锁，`busy` 还包含后台任务。

AI 可通过内置 `__background` 工具管理多个任务：

```tool
{"name":"__background","args":{"action":"start","source":"print('hello')","note":"demo","timeout":45}}
{"name":"__background","args":{"action":"list"}}
{"name":"__background","args":{"action":"status","id":2}}
{"name":"__background","args":{"action":"stop","id":2}}
{"name":"__background","args":{"action":"stop"}}
{"name":"__background","args":{"action":"note","id":2,"text":"dashboard"}}
{"name":"__background","args":{"action":"discard","id":2}}
```

`action=start` 只在 **AUTO 模式（`auto_run=true`）** 下允许，模型可直接提交裸 MicroPython 源码、备注和可选超时，无需再生成 python 代码块。AUTO 关闭时会返回错误并要求模型改为普通 python 代码块，由用户确认前台/后台执行。`source` 最大为 `code_limit`（默认 12288 字符）；解析器为 `start` 放宽工具参数上限以容纳源码，其他 `__background` 动作仍使用 `tool_args_limit`。明显无限循环默认拒绝；确实要长期运行的任务必须显式传 `timeout=0`。启动后模型不应再重复提交同一段代码。`start` 启动的任务仍受最大并发数、结果记录上限、协作停止和看门狗规则约束。

`list` 返回编号、备注、状态、耗时和超时；`status` 额外返回输出尾部。网页每个任务提供停止按钮，手动模式下还提供继续/丢弃按钮，对应 `POST /api/background/stop`、`POST /api/continue` 和 `POST /api/background/discard`；AUTO 模式由设备主线程自动继续，不显示这两个选项。自动继续可能在没有串口用户时运行，因此不会为 Skill 提案或需要确认的 MCP 工具弹出阻塞式串口确认：Skill 提案保持待处理（可从网页卡片或之后的串口 `/code` 处理），非 `auto` 服务器的 MCP 工具会被拒绝并返回错误。停止是协作式的，只在 `__harness_check()`、插桩后的 `range`/`while True` 或 `time.sleep()` 检查点生效；阻塞在 C/SPI 调用中的代码可能只能等看门狗。被取消/停止的任务是用户的明确操作，自动继续只报告停止结果，不会自动重启同一个后台程序，除非用户再次要求。后台代码可在开围栏写 `timeout=0`（或 `timeout=infinite`/`inf`/`unlimited`）请求无截止时间；该形式只允许后台执行，串口前台 `/run` 会拒绝并保留 pending。无限后台程序必须依赖协作检查点和显式停止，30 秒看门狗仍对完全阻塞的代码生效。后台输出通过 `print()` 捕获；直接写 `sys.stdout` 的内容可能丢失。如果脚本长时间阻塞且不调用任何钩子，看门狗仍会复位设备，RAM 中的网页会话会丢失，登录页会显示上次复位原因（例如 WDT_RESET）。若启动时报 `can't create thread`，说明 ESP-IDF 内部堆不足；Harness 会先关闭空闲 HTTPS 连接、GC，并自动尝试更小的 worker 栈（32/24/16/12/8 KB），仍失败时请降低 `background_thread_stack_kb` 或 `background_max_jobs` 后重试，必要时重启设备。

## 自动模式

默认关闭。开启后：

```text
/auto                 # 显示当前状态
/auto on              # 自动运行代码并自动反馈
/auto off
/auto run on          # 只自动运行生成代码
/auto turns 32        # 设置自动反馈最大轮数（1-50，默认 32）
```

- `auto_run`：模型返回 python 代码块后立即本地执行并自动反馈；连续修复受 `max_turns` 限制（默认 32，可用 `/auto turns N` 或网页设置调整）。明显包含 `while True`/`while 1` 的代码会被阻止自动执行，需要人工 `/run`。
- Skill 保存仍然需要确认。
- 复位恢复的待审代码不会在启动时自动运行。
- 自动模式绕过逐次确认，适合可信的本地开发会话。网页对话框旁的 AUTO 控制 `auto_run`，开启需要确认；`auto_feedback` 和 `background_execution` 仍在网页设置页单独配置。
- 后台执行与自动模式相互独立：它只改变执行等待方式，不改变确认/权限规则；AUTO 关闭时手动“前台运行/后台运行”仍可选择。AUTO 开启且 `auto_feedback=true` 时，新代码会创建后台任务，完成后由设备主线程自动继续，不需要网页或用户点击“继续/丢弃”。

## 硬件访问

`/peripherals` 命令已移除，生成代码直接使用 MicroPython 原生外设库：

```python
from machine import Pin, SPI, I2C, UART, PWM, ADC, TouchPad, RTC, time_pulse_us
from neopixel import NeoPixel
```

GPIO 26–37（Flash/PSRAM）、43/44（控制台 UART）、19/20（原生 USB）和 0/3/45/46（strapping）应避开，除非明确知道后果。SPI 硬件总线 `id` 为 1 或 2，写入型设备可把 `miso` 设为 `None`；需要任意引脚时使用 `SoftSPI`。I2C、UART、PWM、ADC、触摸和 RTC 均使用标准 MicroPython 类。详见 [生成代码的硬件访问](hardware.md) 与 [安全、恢复与限制](safety.md)。

## Workspace

| 命令 | 说明 |
|---|---|
| `/workspace` | 汇总和根目录 |
| `/workspace list [path]` | 列出文件 |
| `/workspace read <path>` | 显示文件 |
| `/workspace run <path>` | 显示并确认后前台执行工作区 `.py` 脚本 |
| `/workspace run bg <path> [timeout]` | 显示并确认后在后台执行工作区 `.py` 脚本；默认 `code_timeout_max_s`，`0` 表示无截止时间 |
| `/workspace mkdir <path>` | 创建目录 |
| `/workspace remove <path>` | 删除文件/空目录（确认） |
| `/workspace clean` | 清空 `/workspace/tmp`（确认） |

模型通过 `__workspace` 工具读写；`/workspace` 持久保存用户资料，`/workspace/tmp` 启动时清空。工作区脚本不会自动执行；开启 `/auto run on` 后，模型可用 `__workspace run` 直接执行 `.py` 并拿到输出，否则 Harness 会显示源码并要求串口确认。短脚本用前台 `run`；长时间运行的脚本可传 `background=true`（或串口 `/workspace run bg <path>`）创建后台任务。详见 [Workspace](workspace.md)。

## 典型流程

```text
esp32> 写一个程序打印当前设备信息
# 模型显示代码
esp32> /run
Execute this code locally on ESP32? [y/N] y
# 查看执行结果和模型后续回答
```

创建 Skill：

```text
esp32> 把这段板级约定保存为 Skill
# 模型返回 skill 块，确认保存
esp32> /skills
esp32> /skill-show board-notes
```

`Ctrl-C` 取消当前串口输入。串口 UART RX 缓冲有限，一次性粘贴很长的文本可能被截断；建议分段发送。单行仍受 `input_limit` 限制。

## 思考与响应诊断

```text
/thinking                  # 显示当前级别、文本显示状态和选项
/thinking show             # 查看 reasoning 文本显示状态
/thinking show on          # 打印 reasoning 文本
/thinking show off         # 隐藏 reasoning 文本
/thinking disable          # 关闭思考（别名 disable/off）
/thinking low
/thinking high             # 默认
/thinking max
```

只保留 `disabled`（关闭）、`low`、`high`、`max` 四档，默认 `high`。设置会写入 `config.json`，下一次对话以及重启、复位后继续生效；旧的 `default`、`medium`、`middle` 仍可作为输入，会规范化为 `high`。

reasoning 文本默认显示（`show_thinking=true`）；执行 `/thinking show off` 后只显示 reasoning 字符数，`/thinking show on` 恢复显示思考正文。如果流式响应在第一段正文之前超过 `stream_first_token_timeout`（默认 600 秒），或在正文开始后于 `stream_idle_timeout`（默认 300 秒字节空闲）/`stream_event_idle_timeout`（默认 300 秒事件空闲）内没有进展，且尚未输出正文，Harness 会**保持当前 thinking 等级**按 `api_retries`（默认 3）重试，不会从 `max`/`high` 降到 `low`；已经输出正文后不会自动重试。若模型在 `max_tokens` 内先耗尽 reasoning、返回 `finish_reason=length`，Harness 会先把预算提高到 `max(当前值, truncation_retry_max_tokens)`（默认 49152）；仍截断时才按 `thinking_fallback` 降级，作为避免空回复的最后手段。默认 `max_tokens=32768`；流式响应头等待由 `stream_header_timeout`（默认 300 秒）控制，非流式请求仍按 thinking 级别使用至少 600/900 秒。流式正文开始后按字节和事件两层空闲计时，持续到达的真实 token 会刷新计时，不会因为固定总时长被截断；SSE ping 只能刷新字节层。该规则对所有模型生效，非 DeepSeek 模型只是不发送 `thinking`/`reasoning_effort` 参数。

每次模型请求默认使用 SSE 流式响应，正文实时打印；reasoning 文本仅在 `/thinking show on` 后打印。结果元数据仍会打印：

```text
--- model request: thinking=high stream=True ---
--- model result: status=ok stream=True finish=stop elapsed=...ms header_timeout=300000ms idle=120000ms event_idle=300000ms |
    headers=...ms first_token=...ms chunks=... max_gap=...ms tail_gap=...ms |
    reasoning=... chars content=... chars body=... chars ---
```

`reasoning` 为空表示模型没有返回思考内容；`content=0`、`finish=length` 或 `status=error` 会被明确打印，不会静默写入空回复。流式正文受 `stream_response_limit` 限制；`stream=false` 可回退非流式。
