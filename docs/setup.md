# 部署和初始化

## 1. 目标设备

参考配置：

```text
ESP32-S3 N16R8
16 MB Flash
8 MB Octal PSRAM
USB 串口（CH343/CH340/CP210x 等）
```

文中 `<PORT>` 替换为实际串口：Windows 为 `COMx`，Linux 常见 `/dev/ttyACM0`，macOS 常见 `/dev/cu.usbmodem*`。主机工具支持 `HARNESS_PORT` 或自动选择唯一的 USB 串口；`mpremote` 和 `esptool` 需要显式端口。

参考固件：

```text
MicroPython v1.29.0
ESP32_GENERIC_S3-SPIRAM_OCT
_mpy = 11014
```

可用以下命令确认：

```powershell
mpremote connect <PORT> exec "import sys,os,gc,esp,esp32; print(sys.implementation); print(os.uname()); print(esp.flash_size()); gc.collect(); print(gc.mem_free()); print(esp32.idf_heap_info(esp32.HEAP_DATA))"
```

正常应看到约 `8 MB` 的 GC 空闲堆和 `16 MB` Flash。

## 2. 更新 MicroPython 固件

N16R8 必须使用 **SPIRAM_OCT** 变体，普通 ESP32-S3 固件不能正确使用 Octal PSRAM。

从 <https://micropython.org/download/ESP32_GENERIC_S3/> 下载 v1.29.0 的 `SPIRAM_OCT` 镜像，保存到 `build/firmware/`。文件名会随构建日期变化，下面的 `<firmware>.bin` 替换为实际文件名：

```powershell
New-Item -ItemType Directory -Force build\firmware | Out-Null
# 将下载的 ESP32_GENERIC_S3-SPIRAM_OCT-*-v1.29.0.bin 放入 build\firmware\
```

确认芯片：

```powershell
esptool --port <PORT> flash-id
```

擦除并写入：

```powershell
esptool --port <PORT> erase-flash
esptool --port <PORT> --baud 460800 write-flash 0 build\firmware\<firmware>.bin
```

固件写入会清空整个 Flash，包括旧文件和配置。刷写前应先备份 `boot.py`、`main.py` 等需要保留的文件。

## 3. 安装主机依赖和预编译器

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

`requirements.txt` 包含 `mpremote`、`pyserial` 和 `mpy-cross`。`mpy-cross` 版本必须与设备 `_mpy` 匹配；当前固件为 `11014`。

## 4. 部署并做本地烟雾测试

```powershell
python tools/deploy.py --port <PORT> --clean --no-reset
mpremote connect <PORT> run tools/device_smoke.py
mpremote connect <PORT> reset
python tools/console_smoke.py --port <PORT>
```

预期标志：

```text
DEVICE_SMOKE_OK
CONSOLE_SMOKE_OK
```

设备烟雾测试还覆盖 Web 认证、localhost HTTP、静态资源、`_thread` 32 KB 栈和 `os.dupterm` 主线程 handoff。

中文输入测试：

```powershell
python tools/input_smoke.py --port <PORT>
```

维护模式：

```powershell
python tools/maintenance_smoke.py --port <PORT>
```

普通增量升级不要使用 `--clean`，否则会删除设备配置、CA 和技能：

```powershell
python tools/deploy.py --port <PORT>
```

若设备当前显示 `esp32>`，先在串口执行 `/repl`。

## 5. 进入控制台

```powershell
mpremote connect <PORT> repl
```

串口 115200 8N1；应用提示符是 `esp32>`。

中文对话或长文本建议使用仓库自带的简单串口终端：

```powershell
python tools/serial_console.py --port <PORT>
```

它按 UTF-8 字符边界把输入切成小块并限速发送，避免长中文输入被 UART RX 缓冲截断；同时过滤设备对当前输入行的回显，避免本地终端回显和设备回显重复。也可以用于调试其他串口设备。`:quit` 或 `:exit` 退出，`Ctrl-C` 向设备发送 `0x03`。`--baud` 切换波特率，`--chunk`/`--delay` 调整发送节奏。普通串口终端不会等待设备空闲，设备处理期间请不要连续发送长文本。输入 Wi-Fi 密码/API Key 时本地终端仍会回显，注意周围环境。

## 6. 配置 Wi-Fi、模型和 CA

```text
/setup
```

- Wi-Fi 密码：空输入保留旧值，`-` 清除；
- API Key：空输入保留旧值，`-` 清除；
- Base URL：不要附加 `/chat/completions`；
- CA 路径：默认 `/ca.pem`；全量 bundle 已安装时直接回车保留，通常无需自定义；
- TLS 验证：建议保持开启。

项目包含：

```text
certs/ca-bundle.pem                 # Mozilla 全量公共根证书
certs/digicert-global-root-g2.pem   # 最小 DigiCert 根证书
```

推荐使用全量 bundle，换 API 地址或访问其他公共 HTTPS 服务时不需要重新配置：

```powershell
mpremote connect <PORT> repl
# 在控制台执行 /repl，进入维护模式
mpremote connect <PORT> fs cp certs/ca-bundle.pem :ca.pem
mpremote connect <PORT> reset
```

如果只固定使用 DeepSeek，也可以安装最小根证书：

```powershell
mpremote connect <PORT> fs cp certs/digicert-global-root-g2.pem :ca.pem
```

全量 bundle 会信任 Mozilla 收录的所有公共根 CA；单根证书信任面更小但兼容性更差。详见 [certs/README.md](../certs/README.md)。不想自定义 CA 时，保持 `/ca.pem` 和 TLS 验证开启即可，不需要重复上传或替换。`/setup` 检测到已有 bundle 时，替换提示回答 `N`；Web 的“高级：自定义 CA”仅用于私有 CA、企业内网或自建 API。

也可以使用设备脚本预置非敏感 DeepSeek 配置：

```powershell
mpremote connect <PORT> run tools/configure_deepseek.py
mpremote connect <PORT> reset
```

DeepSeek OpenAI 兼容配置：

```text
Base URL: https://api.deepseek.com
Model:    deepseek-flash
```

随后 `/setup` 中保留 Base URL 和模型，输入 API Key；检测到 `/ca.pem` 已存在时，替换提示回答 `N`。

OpenCode Go（Go 套餐）配置：

```text
Base URL: https://opencode.ai/inference/go/openai/v1
Model:    deepseek-v4.1-flash
CA:       /ca.pem
```

`/model` 中填写以上值即可（API format 保持 `openai`）。Harness 会为每个会话生成稳定的 `x-opencode-session: ses_...` 请求头，并发送 `esp32-harness/0.4` User-Agent；`/clear` 会轮换会话 ID。OpenCode 的免费模型仅限 OpenCode 客户端，其他客户端需使用付费模型或 Go 套餐。

Anthropic Messages 配置：

```text
API format: anthropic
Base URL:   https://api.anthropic.com
Model:      claude-opus-5-5
API key:    sk-ant-...
CA:         /ca.pem
```

`/setup`、`/model` 或网页设置页均可选择。Anthropic 路径使用 `x-api-key`、`anthropic-version` 和 `x-opencode-session`，thinking 使用 adaptive + effort，不发送新模型拒绝的 `temperature`；详见 [Anthropic Messages API](anthropic.md)。

OpenCode Go 的 Anthropic 端点示例：

```text
API format: anthropic
Base URL:   https://opencode.ai/inference/go/anthropic
Model:      deepseek-v4.1-flash
API key:    <OpenCode Go key>
CA:         /ca.pem
```

该端点要求 `x-opencode-session` 路由头；Harness 已自动发送并在 `/clear` 时轮换。

内置 `__search` 只有 `auto` 和 `bing` 两种设置。`auto` 跟随模型 API：Anthropic 使用 Messages API 的 `web_search_20250305` 服务端工具；OpenAI 官方端点使用 Responses API 的 `web_search`；DeepSeek 直连端点（OpenAI 或 Anthropic 协议）使用 `deepseek_web_search` 服务端工具，并显式关闭 thinking；OpenCode Go 和其他只提供模型推理的兼容端点使用 Bing 免 Key 搜索。原生搜索复用对应的 `api_key`，不需要额外的搜索 Key；失败时自动回退到 Bing。可用 `/search <query>` 验证，`/search provider auto|bing` 切换提供方。Anthropic/OpenAI 按各自搜索与 token 价格计费；DeepSeek 服务端搜索不单独收取搜索费，按模型 token 计费；Bing 免 Key。

## 7. Web 控制台与 AP 配网

Web 控制台默认启用（`web_enabled=true`，端口 `80`）。首次使用、没有网页管理密码或 Wi-Fi 连接失败时，设备会开启 WPA2 AP：

```text
SSID:     ESP32-Harness-XXXX
密码:     esp32setup
地址:     http://192.168.4.1/
```

手机/电脑连接 AP 后打开页面，设置管理密码（8–128 字符），可选填写 Wi-Fi、Base URL、Model 和 API Key。保存后自动登录；AP 在 STA 稳定后保留约 5 分钟（`ap_grace_ms`），管理密码未设置时不会自动关闭。重复对话不会重置该倒计时，真实 STA 恢复或重新配置目标网络才会开始新的宽限期。

正常 STA 连接后：

```text
/status
/web
```

`/web` 显示监听状态、STA/AP URL、端口和会话数。浏览器访问 `http://<设备IP>/`。

- 网页与串口共享同一份历史和 Agent；模型回合互斥；
- 网页触发的代码执行回到串口主线程，看门狗仍有效；
- 串口只读命令在网页回合期间仍可用；
- 忘记管理密码：串口执行 `/web reset-password`，确认后清除密码和会话并启动 AP；
- 修改 Wi-Fi 时先开 AP，再切换 STA；原页面可能短暂断线，改连 AP 或新 IP。首次保存后 STA 约需 2–5 秒完成扫描/关联，AP 信道也可能随 STA 变化，浏览器短暂断线不等于连接失败，以串口 `/status` 的 `Wi-Fi:` 行为准；
- ESP32-S3 只支持 2.4 GHz Wi-Fi；连接失败时串口 `/status` 会显示 `Wi-Fi error:`，可区分密码错误、找不到 AP（检查 SSID、2.4 GHz 频段和信号）等情况。新连接开始后 ESP-IDF 可能短暂沿用上一次的 `NO_AP_FOUND`，固件会等待约 5 秒再判定失败，因此首次保存后请耐心等待，不要立即重复提交；
- 连接前会和 ESP-IDF 的在途连接状态同步：旧连接尝试未结束时先断开并等待，失败/超时后清理；出现 `Wifi Internal State Error` 的旧固件请升级后重试；
- 已连接后突然掉线时，默认等待 `wifi_lost_grace_ms`（30 秒）再开 AP，避免短暂抖动导致来回切换；主动保存 Wi-Fi 时会先启动 AP；
- 纯 HTTP，仅用于可信局域网，不要端口映射到公网；首次设置后建议修改默认 AP 密码。

完整 API、NDJSON 事件、安全边界和配置项见 [Web 控制台](web.md)。

## 8. 验证

```text
/status
/test
/test api
```

- `/status` 显示内存、网络、模型、TLS 和技能状态，不显示秘密；
- `/test` 检查 Wi-Fi、安全时间和模型配置；
- `/test api` 发送一次真实小请求，会产生少量费用；
- `/thinking` 查看或切换思考级别；reasoning 文本默认显示（`show_thinking=true`），可用 `/thinking show off` 隐藏；正文实时打印，并显示耗时、finish_reason、reasoning/正文长度；
- `/context` 查看近似 token 使用和自动压缩阈值；`/compact` 手动压缩较早历史，`/summary` 查看摘要。
- `/peripherals` 查看外设配置；GPIO/I2C/SPI/UART/NeoPixel/ADC 默认关闭，必须显式配置后启用。启用后生成代码可用全局 `api` 对象（`api.status()`、`api.gpio`、`api.spi`、`api.i2c`、`api.uart`、`api.neopixel`、`api.adc`）；只允许已配置的允许引脚和总线，禁止 `import machine`。
- `/peripherals scan 0` 可确认 I2C 设备地址，例如 SSD1306 的 `0x3C`。
- `/auto run on` 自动执行生成代码并自动反馈；默认关闭，建议只在可信会话开启。
- 网页“行为”设置可关闭 `background_execution`（默认开启），并调整 `background_max_jobs`（默认 4，范围 1–4）。开启时网页可同时运行多个后台任务、继续发消息或前台运行其他代码；每个任务显示编号、备注和耗时，可分别停止。AUTO 模式且 `auto_feedback` 开启时，设备主线程会自动继续完成的后台结果，不要求用户选择“继续/丢弃”，也不依赖网页保持打开；手动模式仍显示继续/丢弃按钮。AI 或网页按钮可请求停止；`timeout=0`/`infinite` 可让后台任务无截止时间运行，但停止仍依赖协作检查点。串口可用 `/run bg [备注]`、`/background` 和 `/continue [id]`。`background_thread_stack_kb`（默认 32）和 `background_max_records`（默认 8）通常无需修改。它不改变确认、超时或权限规则。

## 9. 设备文件

| 文件 | 内容 |
|---|---|
| `config.json` | 非秘密运行参数 |
| `secrets.json` | Wi-Fi 密码、API Key、网页密码加盐哈希和 AP 密码（明文/哈希） |
| `ca.pem` | API 根证书或全量 CA bundle |
| `MEMORY.md` | 始终加载的项目上下文 |
| `skills/<name>/SKILL.md` | 显式保存的 Skill 指令包 |
| `workspace/` | 持久工作区；`workspace/tmp/` 启动时清空 |
| `recovery.py` / `recovery.json` | 生成代码执行中的恢复数据 |
| `maintenance.flag` | 维护模式标志 |

真实配置不纳入 Git。设备文件系统没有系统级用户权限隔离，能访问串口或物理设备的人可能读取秘密。
