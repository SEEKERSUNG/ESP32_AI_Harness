# 安全、恢复与限制

## 动态代码不是沙箱

`/run` 执行的是拥有当前 MicroPython 进程权限的 Python。确认机制用于避免误操作，但不能使代码变得安全。生成代码仍可能：

- 死循环或耗尽内存；
- 修改、删除 Harness 和配置文件；
- 读取 `secrets.json`；
- 发起网络连接；
- 直接操作任何 GPIO、总线或外设，包括 Flash/PSRAM、USB 和控制台 UART 相关引脚。

不要执行来源不明或未审查的代码。`try/except` 不能解决硬 fault、死循环和全部内存问题。

## 看门狗与执行恢复

默认 `watchdog_ms = 30000`。串口等待和网络响应接收期间 Harness 持续喂狗，因此模型生成时间可以超过 30 秒。生成代码里内联的 `@micropython.viper`/`@micropython.native` 会在设备端 `compile()` 时发射原生代码，从 ESP-IDF 内部堆分配；同一个会话反复编译会留下碎片，可能在 `gc.mem_free()` 仍很大时报 `MemoryError`。这类函数应放进按 `.mpy` 部署的 import 模块，编译期 `MemoryError` 的观察文本会注明编译阶段、脚本未执行以及 GC/内部堆状态。生成代码执行期间，只有协作式截止钩子（`__harness_check()`、插桩后的 `range()`/所有 `while` 条件、代理后的 `time.sleep()`）会调用 `feed_execution()` 喂狗；钩子内部按计数摊还，快路径只做计数比较，喂狗约每 250 ms 一次、停止请求约每 25 ms 轮询一次，循环体变慢时自动恢复为每轮检查；不经过这些钩子的代码（如超长非 `range` 的 `for` 循环、单次迭代内长时间阻塞的 C/SPI 调用、`@micropython.viper`/`@micropython.native` 函数）仍会停止喂狗。

执行前保存：

- `recovery.py`：待运行源码；
- `recovery.json`：运行标记。

正常结束或捕获异常会删除二者。异常复位后，源码恢复为 pending 并显示警告，**不会自动重跑**。用户可以 `/code` 审查、`/discard` 丢弃，或再次 `/run`。

执行器默认在 `code_timeout_s=20` 秒时停止可协作代码，模型可在开围栏同一行声明 `timeout=`（上限 `code_timeout_max_s=300`）；闭合围栏后的独立 `timeout=` 行和代码块首个未再使用的独立 `timeout=N` 行会作为兼容形式识别，但其他说明文字不会。超时返回 `status=timeout`，设备不重启；自动反馈可以继续。超时只停止当前 `exec`，不终止之前代码创建的 Python 线程。看门狗仍是最后防线：C/SPI 级阻塞调用、超长非 `range` 的 `for` 循环、单次迭代内长时间阻塞、关闭钩子的代码和硬 fault 仍可能复位。看门狗是可用性机制，不是权限边界。

`/repl` 创建 `/maintenance.flag` 并复位；维护模式下 Agent 和看门狗都不启动。部署器会自动清除该标志。

## TLS 与秘密

- 默认 `tls_verify = true`，使用 SNI 和 CA 验证；Web 将自定义 CA 折叠为高级项只是简化界面，不会关闭验证；部署器会自动安装全量 bundle，保持 `/ca.pem` 即可；
- `/ca.pem` 可以安装 Mozilla 全量公共根证书 bundle，兼容多个 HTTPS 端点；这也会信任所有收录的公共 CA，比只安装所需根证书的信任面更大；
- 私有 CA、企业内网或自建 API 仍需单独安装对应根证书；
- `CERT_REQUIRED` 依赖正确 RTC，因此联网后自动同步 NTP（启动一次，默认每 6 小时刷新，失败 60 秒后重试），RTC 仍有效时允许 NTP 暂时失败；NTP 是无认证 UDP，恶意局域网可伪造时间；
- RTC 保存 UTC；`__time` 按 `timezone_offset_minutes`（默认 480，即 UTC+8）转换为固定偏移显示，不自动处理夏令时，也不把本地时间写回 RTC；
- 关闭验证会暴露 API Key、对话和代码给中间人；
- API Key、Wi-Fi 密码和 MCP Header 值不会由 `/status`、`/api/status`、`/api/config` 或 `/api/mcp` 输出，也不进入 Git；
- 设备上的 `/secrets.json` 和 `/mcp.json` 是明文文件，串口或物理访问可读取；`/mcp.json` 可能包含 GitHub PAT、MCP Token 等；
- `/state/conversation.json` 保存已提交对话、摘要和消息类型标记，可能包含用户粘贴的敏感文本；`/state/background.json` 在后台结果继续前短暂保存有界执行输出。两者默认开启，可在“设置 → Harness 行为”关闭持久化，`/clear` 删除；
- 用户粘贴到自然语言或 `/history` 的秘密不会被自动脱敏；
- `show_thinking` 默认 `true`，reasoning 文本会输出到串口，其中可能包含用户信息或中间推理，注意串口日志的保存和分享；可用 `/thinking show off` 关闭；
- thinking 级别越高，请求延迟和费用越高；流式响应头等待取 `stream_header_timeout`（默认 300 秒）与 thinking 预算的较大值；首段正文前另有 `stream_first_token_timeout`（默认 600 秒），正文开始后按字节空闲 `stream_idle_timeout`（默认 300 秒）和事件空闲 `stream_event_idle_timeout`（默认 300 秒）控制，持续 token 会刷新计时，但长请求期间串口和网页仍可能长时间等待；超时重试保持用户当前 thinking 等级，不会为了尽快成功而降级，因此仍可能多次以高等级重试并产生费用；已经输出正文后的超时不会自动重试。重试耗尽、工具/回合上限或用户取消导致回合停止时，Harness 会把本轮已有的用户消息、工具结果和一条“本轮未完成”说明写入持久对话；这些内容可能包含敏感信息。空正文、截断、超时和首字/最大间隔会作为模型结果元数据打印。

## Web 控制台

- Web 控制台使用**纯 HTTP**，没有服务器 TLS。管理密码、会话 Cookie、对话内容和配置在局域网内可被嗅探或篡改；只用于可信 LAN，绝不将 80 端口映射到公网。
- 管理密码使用随机盐 + SHA-256 保存，不保存明文；这不是密码 KDF，强密码、登录限速和短会话是实际防线。`/secrets.json` 仍是明文文件，物理或串口访问可绕过网页认证。
- 会话只保存在 RAM，最多 `web_max_sessions` 个，默认 12 小时绝对 TTL；重启后需要重新登录（登录页会显示上次复位原因，例如 WDT_RESET）。修改管理密码会清除全部旧会话。
- 登录按 IP 限速：默认 5 次失败锁 30 秒，指数递增到 5 分钟，返回 429 和 `Retry-After`。限速是局域网级别的，不防物理接触。
- 首次设置只接受 AP 网段客户端（`web_setup_ap_only=true`）；AP 默认密码 `esp32setup` 是公开的，附近知道默认密码的人可能抢先完成首次设置。设置完成后应立即修改 AP 密码。
- 所有 POST 要求 `X-Harness: 1`、精确 `Origin` 和 JSON Content-Type；Host 必须匹配当前 STA/AP IP、`127.0.0.1` 或 `localhost`；不发送 CORS 头。Cookie 为 `HttpOnly; SameSite=Strict`，HTTP 下没有 `Secure`。
- 管理员可以修改 `base_url`，从而把已保存的 API Key 发往新地址；管理员权限约等于凭据控制权。不要把管理员密码交给不可信的人。
- 网页触发的生成代码与串口 `/run` 拥有相同的完整 MicroPython 权限；网页自动模式与 `/auto` 共用全局配置，会自动执行代码并产生多次计费请求。普通超时由协作式截止时间停止；不经过钩子的死循环仍由看门狗复位。
- 后台执行最多同时运行 `background_max_jobs` 个任务（默认 4，可设 1–4），每个任务在自己的 worker 线程；它不改变权限、超时或看门狗规则。任务无法被硬杀：AI 的 `__background stop`、网页停止按钮、`/background stop` 和 `POST /api/background/stop` 只设置协作取消标志，在 `__harness_check()`、插桩后的 `range`、所有 `while` 条件或 `time.sleep()` 检查点抛出 `ExecutionCancelled`；阻塞在 C/SPI 调用中的任务可能只能重启设备。停止并丢弃（`stop_discard`、`/background stopdiscard`、`POST /api/background/stop {"discard":true}`）同样依赖协作检查点，但结果不会进入 ready、自动继续或重启恢复记录；排队任务会立即移除。`timeout=0`/`infinite` 允许后台无截止时间运行，但必须依赖这些检查点和显式停止，30 秒看门狗仍是最后防线。后台任务可与新对话、前台代码以及其他后台任务并行，因此代码之间可能竞争文件、网络和全局状态；不要同时写同一文件，也不要让多个任务同时驱动同一条 SPI/I2C 总线或同一设备。后台输出通过线程级 `print` 捕获，直接写 `sys.stdout` 可能丢失。后台记录写入 `/state/background.json`，完成但尚未继续的输出会在设备重启后保留，直到继续、丢弃、`/clear` 或恢复出厂设置；未完成记录在重启后变为 error 且不会自动重跑。并发任务不写单一的 `recovery.py` 标记，避免互相覆盖；复位后源码仍在对话历史中，可由用户重新审查。结果观察会标注原任务和其他仍在运行的任务；若其间留下待执行代码、Skill 或问题，自动继续会等待其处理完毕。AUTO 模式且 `auto_feedback=true` 时由设备主线程自动继续完成结果（不依赖网页保持打开），仍会产生计费模型请求；手动模式保留网页“继续/丢弃”选择。
- 定时任务保存在 `/state/schedules.json`，按 RTC 本地时间执行。提示词任务在设备空闲时自动发起模型回合，即使没有浏览器打开也会运行并产生计费请求；生成代码仍遵守 AUTO/确认，AUTO 关闭时只会留下待执行代码。代码任务会按保存的源码在后台自动运行，AI 通过 `__schedule` 创建、修改、启用或立即运行代码任务必须先开启 AUTO（停用/删除不受限）；网页表单由管理员确认后创建，不要求 AUTO。RTC 未同步（年份早于 2025）时全部跳过。失败后 60 秒重试、最多 3 次；重复任务错过多次只补跑一次。`/schedule` 和网页可随时停用/删除；恢复出厂设置删除全部任务。
- `__time action=set` 会直接写入 RTC，`action=timezone` 会修改显示时区并重排每日任务；只在用户明确要求时使用。手动时间可能在下一次 NTP 同步时被覆盖。
- `/undo` 和网页回退只删除对话上下文，不回滚已经执行的 GPIO、文件、网络或其他设备副作用；它还会清除待执行代码、Skill 提案、待回答问题和最近执行结果。后台任务运行期间不能回退。
- 网页 `/api/command` 接受配置、Memory/Skill/Workspace 写入、MCP、后台控制、`/http`、`/run`、`/run bg`、`/workspace run` 和 `/continue` 等命令；风险操作在浏览器确认后以 `confirm:true` 重试，生成/工作区代码仍通过主线程 handoff 执行。需要第二行输入或重启设备的 `/setup`、`/wifi`、`/model`、`/ca`、`/factory-reset`、`/web reset-password` 使用专用网页表单/按钮（`/repl` 仅串口；`/memory replace` 可用 `=>` 单行形式）。回退可通过 `/undo`/`/rewind` 或专用 `POST /api/rewind`。
- Web CA 上传只接受包含 PEM 标记、路径为绝对 `.pem` 且不含 `..` 的内容，上限 64 KB。全量 Mozilla bundle 建议通过部署或串口安装。
- 网页文件管理器可以列出整个 VFS，但写入、上传、重命名和删除只允许 `/workspace` 下的路径；`/workspace` 与 `/workspace/tmp` 只能管理内容，不能删除/重命名目录本身。固件、启动文件、`/harness`、`/skills`、`/state` 等系统文件只读。`/secrets.json` 和 `/mcp.json` 不出现在可读/下载接口，界面标记“敏感”。路径校验拒绝 `..`、反斜杠和控制字符，并按大小写不敏感方式拦截敏感文件名。上传只接受 UTF-8 文本并受 `workspace_file_limit` 和工作区总量限制；下载上限 256 KB。文件页沿用网页管理员会话，不是额外的权限边界。
- Web 线程只处理 HTTP 和模型请求，不直接执行前台生成代码、不调用 `os.dupterm`、不复位设备、不直接喂看门狗；前台生成代码和工作区脚本通过主线程 handoff 执行。后台 worker 线程只运行经过相同确认规则的任务，使用线程级输出隔离，也不调用 `os.dupterm`/`machine.reset`/喂狗。执行器的协作式钩子只在主线程调用 `feed_execution()`；后台 worker 的喂狗调用是空操作。因此网页触发的可协作长前台程序可以安全超过 30 秒；不经过钩子的前台阻塞（如单次 C/SPI 调用或超长非 `range` 的 `for` 循环）仍会停止喂狗并复位，而后台任务完全阻塞时主线程仍喂狗，该任务通常只能靠重启恢复。共享执行运行时必须在最后一个执行结束/线程结束后恢复 `builtins.print`、`builtins.__harness_check` 和 `time` 模块。
- 单 Web 线程意味着长对话期间其他网页请求排队；这不是并发隔离，不能用来承载多用户或公网访问。请求头和正文读取有 `web_request_timeout_s` 总超时，但长模型回合期间网络维护仍会延后。
- 网页“停止”按钮对本页持有的流通过关闭请求取消；对串口、AUTO 自动继续或定时任务则调用 `POST /api/stop`，设置主线程协作取消标志，在下一个网络检查点抛出 `LLMStreamCancelled`，主线程执行器也会在 `__harness_check()`、插桩后的 `range`、所有 `while` 条件或 `time.sleep()` 检查点返回 `cancelled`。阻塞在 C/SPI 调用中的代码仍无法被强制终止，代码副作用不会因点击停止而撤销。
- Web 配置布尔字段只接受 JSON `true`/`false`。首次设置和配置更新通过 `save_all()` 在写盘异常时恢复旧文件和 RAM；该回滚不能保证两个独立文件在掉电瞬间的全局原子性。

## 会话和连接

- 会话历史默认持久化到 `/state/conversation.json`，每次请求仍完整重发消息；网页登录会话本身只保存在 RAM；
- 不依赖长连接；HTTP/TLS 连接可以按请求复用或重新建立；
- 没有后台心跳、写前日志或云端 worker 自动重启；
- 经典 ESP32 的 TLS 预热、session worker 和恢复重启逻辑已移除；
- 自动压缩会额外调用一次模型，产生费用；摘要和原始历史一样可能包含敏感信息，并同时存在于 RAM 和持久化文件；
- API 瞬时失败会自动重试（默认 3 次），会增加延迟和请求次数，可能产生额外费用；401/403、证书错误、大小超限和内容截断不重试；流式失败后已打印的部分输出只存在于串口，不写入历史；
- SSE 正文、非流式正文、摘要和历史均有独立大小上限。

## Skills、内置工具与 Memory

- Skill 是 Markdown 指令包，不是可执行代码；`__skill read` 只加载上下文，不执行任何内容。模型可在用户明确要求时通过 `__skill delete` 删除整个 `/skills/<name>/` 目录；没有回收站，删除不可恢复。
- Harness 只提供内置工具（`__skill`、`__workspace`、`__memory`、`__search`、`__ask`、`__http`、`__background`、`__time`），不再支持模型自定义可执行 Tool；不会从 `/tools` 加载任意代码。
- 内置工具只访问 Skill 目录、Workspace、MEMORY.md 或内置搜索服务，不拥有任意文件或硬件访问能力；硬件访问只发生在生成代码中，生成代码可直接 `import machine`/`neopixel` 等原生模块。`__ask` 只把模型生成的问题和选项写入对话历史并结束回合；问题文本会持久化到 `/state/conversation.json`，可能包含敏感信息，回答作为普通用户消息处理。
- `__skill read` 会把 SKILL.md 返回给模型；这些内容可能包含提示注入。模型应把它们当作数据。
- `/MEMORY.md` 始终加载，模型可通过 `__memory` 追加、删除或替换记忆且不需逐次确认；串口 `/memory delete` 和 `/memory clear` 有确认提示。不要把秘密写入 MEMORY.md。
- `__search` 会把查询发送到配置的搜索后端：Anthropic/OpenAI/DeepSeek 的原生 Web 搜索服务，或 Bing。搜索内容、答案和引用文本都是不可信数据，可能包含提示注入，模型不应把其中的指令当作系统指令执行。
- Anthropic/OpenAI 原生搜索通过一次独立的模型 API 请求执行，会按各自的搜索和 token 价格计费；DeepSeek 直连的 `deepseek_web_search` 不单独收取搜索费，按模型 token 计费。Bing 免 Key，但查询会发送到 `cn.bing.com`，默认返回 RSS（失败后 HTML），受 `search_html_limit` 限制。独立的 DeepSeek Search API 和专用 Key 已移除。
- `auto` 模式会复用当前模型提供方的 `api_key` 访问其原生搜索服务，不需要单独的搜索 Key；OpenCode Go 和其他只提供模型推理的兼容端点使用 Bing。原生搜索失败时会自动回退到 Bing，并在结果中注明；显式 `bing` 不会静默换源。
- `/workspace` 是共享可写工作区，模型可按用户要求写入持久文件或 tmp；不要在其中保存 Wi-Fi 密码、API Key 等秘密。
- `/workspace/tmp` 重启和更新后保留，只有 `/workspace clean` 会清空；工作区脚本默认只是数据，不会自动执行。`/workspace run` 需要人工确认；`__workspace run` 在 `auto_run` 开启时直接执行，关闭时显示源码并要求串口确认。
- 自动模式 `auto_run` 会立即执行生成的 Python，默认关闭，必须由用户在可信会话中显式开启。
- 自动模式仍不会在启动时执行恢复代码；明显无限循环的代码会被阻止自动执行。
- 自动模式下，模型在解释中向用户提问并同时返回代码时，Harness 会把“提问 + 代码”作为协议错误要求模型纠正，不会先执行代码；这是保守文本启发式，模型仍可能用其他措辞绕过，不能替代用户审查。
- 一次性的普通生成代码仍是用户确认后执行的完整 MicroPython 程序，拥有当前进程权限；核心文件保护主要针对 Workspace API，不是对任意代码的强隔离。
- `/factory-reset` 是破坏性维护操作：串口需要确认并输入 `RESET`；网页需要勾选确认并提供当前管理密码。它只删除用户数据（配置、秘密、`mcp.json`、`MEMORY.md`、recovery 文件、Skill、旧版 Tool 目录、`/state` 持久对话、工作区和对话状态）后重启，不擦除 Flash、不改固件，并保留 `boot.py`、`main.py`、`harness/` 和公共 CA `ca.pem`。

## 公共 HTTP 抓取

- `__http` 只允许公共 HTTP(S) 目标；默认只允许 HTTPS、端口 80/443。私有、回环、链路本地、CGNAT、组播、保留地址和本地主机名在连接前被拒绝，每次重定向都会重新校验。
- 校验通过的 DNS 结果会固定为该次 socket 的 IPv4 连接地址，避免连接阶段再次解析时被 DNS rebinding 改到内网；TLS 的 SNI/Host 仍使用原主机名。URL 和请求头中的换行、控制字符会被拒绝，避免出站请求头注入。
- 跨源重定向会丢弃调用方提供的全部 Header，避免 `Authorization` 等凭据跟随跳转到第三方；HTTPS 跳转到明文 HTTP 一律拒绝。同源重定向保留 Header。
- 这仍是尽力而为的 SSRF 防护，不是完整沙箱：恶意 DNS 解析、路由层重定向和非常规 URL 解析仍可能造成风险；不要把设备暴露在不可信网络或让它访问敏感内网。
- `http_tool_allow_http=true` 会允许明文 HTTP，内容可被中间人读取或篡改；只应在可信网络临时开启。
- 响应正文是不可信数据，可能包含提示注入；模型不应执行其中的指令。`get` 只返回文本类型并受输出上限限制；二进制内容必须 `download`。
- 下载写入 `/workspace`，受 `http_tool_download_limit`（默认 65536 字节）和工作区总量/文件数限制；下载的文件不会被自动执行。
- 请求不会自动携带设备 API Key、Wi-Fi 密码、MCP Header 或 Cookie；模型只能使用用户在对话中提供的请求头。受管请求头（Host、Content-Length、Connection、Transfer-Encoding、Accept-Encoding）不能被覆盖；Header 名称必须是合法 token，值不能包含换行或其他控制字符。

## MCP 远程工具

- MCP 只支持远程 HTTP/SSE，不支持 stdio 子进程；设备不会执行 MCP 服务器代码。
- `/mcp.json` 保存服务器 URL 和静态 Header，可能含 Bearer Token；串口/物理访问可读取，API 只返回 Header 名称。
- 非只读 MCP 工具默认需要确认；`readOnlyHint` 免确认；`auto=true` 会无确认执行，等同于把该服务器的权限交给模型。
- MCP 工具可以修改外部系统（GitHub、数据库、云资源等），风险高于本地只读工具；使用最小权限 Token，并定期轮换。
- MCP 返回内容是**不可信数据**，可能包含提示注入；模型不应执行其中的指令。
- 服务器 URL 使用与模型请求相同的 TLS 验证和 CA；纯 HTTP MCP 只应在可信局域网使用。
- 不支持 OAuth 浏览器授权；不要把 OAuth 客户端密钥写进 `/mcp.json` 期望自动刷新。
- `mcp_refresh_budget_s` 限制一次刷新总时长；单个服务器超时只影响该服务器，不会阻止普通对话。

## 原生硬件访问和固件边界

- 生成代码直接运行在 MicroPython 上，可以 `import machine`、`neopixel`、`framebuf`、`esp`、`esp32` 等模块；没有全局 `api` 对象、引脚 allowlist 或 `/peripherals` 配置。确认机制只防止误执行，不是硬件沙箱。
- GPIO 26–37 用于 Flash/Octal PSRAM；43/44 用于控制台 UART0；19/20 为原生 USB；0/3/45/46 为启动 strapping。除非明确知道后果，不要在这些引脚上接外设或输出信号；误用可能导致设备无法启动、文件系统损坏或需要重新刷机。
- 生成代码可以构造 `Pin`/`SPI`/`I2C`/`UART`/`PWM`/`ADC`/`TouchPad`/`RTC`，也可以调用 `machine.reset()`、`machine.WDT` 等底层接口。这些能力不会被 Harness 拦截；请只运行经过审查的代码。
- 板载 RGB LED 是 GPIO48 上的单颗 WS2812，可直接使用 `neopixel.NeoPixel(Pin(48), 1)`；外接灯带时注意 5 V 供电、电平和电流，不要由 GPIO 直接供电。
- ADC 输入电压必须在 0–3.3 V 范围内；不要接入负电压或超过 3.3 V 的信号。
- 接线前确认 3.3 V 电平、供电、上拉电阻和设备地址；错误接线或错误驱动可能损坏外设或开发板。
- 多任务同时访问同一条 SPI/I2C 总线可能交错；不要同时驱动同一设备。
- 部署脚本只复制/删除 VFS 文件，不调用 `esptool`，不擦除 Flash，不修改 Bootloader、分区表或 MicroPython 固件。

固件更新是单独、显式的维护操作，必须使用正确的 `ESP32_GENERIC_S3-SPIRAM_OCT` 镜像。

## 已知限制

- 默认 SSE 流式、非压缩 HTTP/1.1；`stream=false` 可回退到非流式 JSON；
- 上下文 token 数是近似估算，不是供应商精确 tokenizer 计数；
- 串口 UART RX 缓冲有限，快速粘贴超长文本可能被截断；建议分段发送或使用自动化工具限速；
- 仅支持 DNS/IPv4 风格 URL，不支持 IPv6 URL；
- 没有进程隔离、每脚本内存配额或可靠的协作式强制终止；多个后台任务共享同一个 MicroPython 解释器、堆、文件系统和网络栈，可能互相干扰；
- 普通生成代码仍拥有完整进程权限，确认机制不是安全边界；
- 已提交历史、摘要、消息类型标记和最多 8 条未继续的后台记录默认持久化在 `/state`；`/clear` 或恢复出厂设置删除，网页可关闭持久化；Skill、MEMORY.md、Workspace 和 MCP 配置也持久化在 VFS；
- Skill 没有版本回滚；
- 自动反馈、自动压缩和内置工具调用会产生额外模型请求和费用；
- 真实云端兼容性取决于供应商、模型、证书和网络。
