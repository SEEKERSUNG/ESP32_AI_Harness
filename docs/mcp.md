# MCP 远程工具

Harness 实现了一个面向设备约束的 MCP 客户端：只连接远程 HTTP/SSE 服务器，不启动本地子进程。工具通过系统提示词和原生 function calling 暴露给模型；启用原生工具时以 `mcp__server__tool` 名称和 `input_schema` 发送，端点拒绝原生工具时回退到与内置工具相同的 `tool` JSON 块。OpenAI/Anthropic 的函数名上限为 64 字符；超过该长度的 MCP 工具不会进入原生工具列表，但仍可通过 `/mcp` 或文本回退使用。

## 支持的协议

| 能力 | 状态 |
|---|---|
| Streamable HTTP（2025-03-26 / 2025-06-18 / 2025-11-25） | 支持：`initialize`、`notifications/initialized`、`MCP-Session-Id`、`MCP-Protocol-Version`、JSON 或 SSE 响应、`tools/list` 分页、`tools/call` |
| 无状态 2026-07-28 | 支持：每请求 `_meta`、`MCP-Protocol-Version`、`Mcp-Method`、`Mcp-Name`、JSON/SSE 响应 |
| 旧版 HTTP+SSE（2024-11-05） | 不支持；服务器需提供 Streamable HTTP 端点 |
| `resources/read`、`prompts/get` | 支持串口命令和内部客户端；模型默认只看到 tools |
| stdio MCP | 不支持：ESP32 不能启动子进程 |
| OAuth 2.1 / DCR / PKCE | 不支持：没有浏览器授权流程；使用静态 Header |
| sampling / roots / elicitation / server-initiated requests | 不支持；流上的服务器请求会被忽略 |
| 独立 GET SSE 通知流 | 不保持；`tools/list` 按 `mcp_refresh_s` 轮询刷新 |
| `notifications/cancelled` | 未发送；断流不视为取消，工具可能已经执行 |

协议选择：

- `protocol: "auto"`（默认）：先按 2025-11-25 初始化；如果服务器以 `-32022`/protocol version 错误或 method-not-found 拒绝，则切换到无状态 2026-07-28。
- `protocol: "legacy"`：只使用 initialize/session。
- `protocol: "2026-07-28"`：直接使用无状态请求。

## 配置

MCP 配置保存在设备 `/mcp.json`，不会提交到 Git：

```json
{
  "version": 1,
  "servers": [
    {
      "name": "github",
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": {"Authorization": "Bearer <GitHub PAT>"},
      "enabled": true,
      "auto": false,
      "timeout_s": 60,
      "protocol": "auto",
      "max_tools": 32
    }
  ]
}
```

字段：

- `name`：1–24 位字母、数字、`_`、`-`，唯一；用于工具前缀。
- `url`：`http://` 或 `https://`，长度 ≤512。
- `headers`：最多 8 个静态 Header；值 ≤1024，不能包含换行。禁止覆盖 `Host`、`Content-Length`、`Connection`、`Accept`、`Content-Type`、`MCP-Protocol-Version`、`MCP-Session-Id`、`Mcp-Method`、`Mcp-Name`。
- `enabled`：是否加载。
- `auto`：是否允许该服务器的非只读工具无确认执行。默认 `false`。
- `timeout_s`：单次 HTTP 请求超时，5–300 秒。
- `protocol`：`auto` / `legacy` / `2026-07-28`。
- `max_tools`：该服务器最多加载的工具数，1–64。

`/api/mcp` 和 `/mcp list` 只返回 `headers_set` 和 Header 名称，不返回值。网页保存时，如果某个服务器对象省略 `headers` 字段，会保留原 Header；显式写 `"headers": {}` 才会清空。

## 串口命令

```text
/mcp                         显示服务器、工具和错误
/mcp list                    同上
/mcp refresh                 重新初始化所有服务器并加载工具
/mcp tools [server]          列出已加载工具
/mcp add <name> <url> [Header=Value ...]
/mcp remove <name>
/mcp enable <name> on|off
/mcp auto <name> on|off
/mcp timeout <name> <seconds>
/mcp protocol <name> auto|legacy|modern
/mcp read <server> <uri>
/mcp prompt <server> <name> [json-args]
```

`/mcp add` 的 Header 值可以包含空格：`Authorization=Bearer abc` 会把后续不含 `=` 的参数并入值。串口回显会显示输入内容；不要把含 Token 的命令粘贴到公开日志。

## 网页

设置页“MCP（远程工具）”提供：

- 启用/关闭 MCP；
- 服务器 JSON 编辑（只显示非秘密字段）；
- 保存并刷新；
- 刷新按钮和工具/错误状态。

API：

```text
GET  /api/mcp              # 公共服务器列表、工具数、错误
POST /api/mcp              # {"servers":[...], "confirm":true?}
POST /api/mcp/refresh      # 重新加载工具
```

任何服务器 `auto=true` 时，POST 必须带 `"confirm": true`；网页会额外弹出确认对话框。刷新是同步的，受 `mcp_refresh_budget_s`（默认 60 秒）限制；服务器超时可能让请求接近网页请求超时。

## 工具命名与调用

暴露名称：

```text
mcp__<server>__<tool>
```

工具名中除字母、数字、`_`、`-` 外的字符替换为 `_`；完整名称超过 96 字符或与已有名称冲突时跳过。系统提示词最多列出 `mcp_tool_limit`（默认 32）个工具，schema 总预算 `mcp_prompt_chars`（默认 12000），超出时截断。

模型调用：

```tool
{"name":"mcp__github__list_pull_requests","args":{"owner":"octocat","repo":"hello-world"}}
```

工具参数受 `mcp_args_limit`（默认 8192 字符）限制。工具结果按以下顺序格式化：`isError` 标记、text 块、image/audio/resource_link 占位、嵌入 resource 文本、`structuredContent` JSON。输出受 `mcp_output_limit`（默认 8192 字符）限制。MCP 输出是**不可信数据**，不能当作指令执行。

## 确认与安全

- `annotations.readOnlyHint: true` 的工具免确认。
- 其他工具：`auto=false` 时，串口询问 y/N；网页直接拒绝并提示启用 auto 或使用串口。
- `auto=true` 会让该服务器的非只读工具无确认执行；只在信任服务器和 Token 权限时开启。
- MCP 工具可以读写外部系统，风险高于内置的只读工具；建议给 Token 最小权限。
- 服务器 URL 使用与模型请求相同的 `tls_verify` 和 `ca_file`；纯 HTTP MCP 只在可信局域网使用。
- `/mcp.json` 含明文 Header，串口/物理访问可读取；恢复出厂设置会删除。
- 刷新失败只影响 MCP，不会阻止普通对话；错误会显示在 `/mcp` 和网页状态中。

## 兼容性示例

以下远程服务器通常接受静态 Bearer Token（以供应商最新文档为准）：

```text
/mcp add github https://api.githubcopilot.com/mcp/ Authorization=Bearer <PAT>
/mcp add context7 https://mcp.context7.com/mcp Authorization=Bearer <KEY>
/mcp add deepwiki https://mcp.deepwiki.com/mcp
/mcp add microsoft https://learn.microsoft.com/api/mcp
```

需要 OAuth 的服务器（例如部分 Linear/Supabase 配置）如果没有静态 Token 选项，当前设备端无法完成授权。可以在主机侧用 `mcp-remote` 等桥接工具暴露一个本地/远程 Streamable HTTP 端点，但不要把它暴露到公网。

## 调试

1. `/mcp list` 确认 URL、协议、Header 名称和 enabled/auto；
2. `/mcp refresh` 查看初始化错误；
3. `/mcp tools` 确认工具名；
4. 串口 `/test api` 只测试模型 API，不测试 MCP；
5. 失败时检查 401/403（Token/权限）、404（URL/会话）、400（协议版本）和超时；
6. 某些服务器要求 `Accept: application/json, text/event-stream`，Harness 已自动发送。
