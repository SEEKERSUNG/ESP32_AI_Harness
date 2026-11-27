# OpenAI 兼容 API

Harness 支持两种模型协议，可在 `/setup`、`/model` 或网页“设置 → 模型”中选择：

```text
api_provider = "openai"      # OpenAI 兼容 chat/completions（默认）
api_provider = "anthropic"   # Anthropic Messages /v1/messages
```

本文说明 OpenAI 兼容路径；Anthropic 路径见 [Anthropic Messages API](anthropic.md)。切换协议不会自动替换已保存的 API Key；请确认 Key 与端点属于同一供应商。切换时如果 Base URL 仍是已知默认值（OpenAI、DeepSeek、Anthropic），Harness 会换成新协议的默认地址；自定义代理地址保持不变。

## 请求映射

| Harness | OpenAI 兼容 Chat Completions |
|---|---|
| `base_url` | 默认 `https://api.openai.com/v1`；请求地址为 `base_url.rstrip("/") + "/chat/completions"` |
| `api_key` | `Authorization: Bearer <key>` 请求头 |
| 会话路由 | `x-opencode-session: ses_...`（OpenCode Go 要求；其他兼容端点通常会忽略） |
| `model` | `model` |
| `max_tokens` | `max_tokens`（默认 32768） |
| messages | 原样发送 `system`/`user`/`assistant`；工具结果使用 `role=tool` + `tool_call_id` |
| `temperature` | 发送 `temperature`（默认 0.2） |
| `thinking_level` | 仅 DeepSeek 直连模型发送 thinking 参数；其他模型不发送 |

`thinking_level` 与 Anthropic 路径共用同一份持久化配置：

- `disabled`：DeepSeek 发送 `{"thinking":{"type":"disabled"}}`；其他模型不发送 thinking 参数；
- `low` / `high` / `max`：DeepSeek 发送
  ```json
  {"thinking": {"type": "enabled"}, "reasoning_effort": "low|high|max"}
  ```
  其他 OpenAI 兼容模型不发送任何 thinking 字段，因为多数网关会拒绝未知参数；
- `max` 的请求超时至少 900 秒，其他思考级别至少 600 秒；流式响应头等待取 `stream_header_timeout`（默认 300 秒），header 之后到首段正文之前由 `stream_first_token_timeout`（默认 600 秒）控制。

默认 `high`；旧的 `default`/`medium`/`middle` 输入会规范化为 `high`。

## 流式解析

请求默认使用 `stream=true` 和 SSE：

- `choices[0].delta.content` 追加正文；
- `choices[0].delta.reasoning_content` 追加思考文本（DeepSeek 等兼容端点）；
- `choices[0].delta.tool_calls` 按 `index` 累积工具名和 JSON 参数，支持分片传输；
- `choices[0].finish_reason` 记录结束原因（`stop`、`length`、`tool_calls` 等）；
- `[DONE]` 结束流；
- 注释行、`event:`、`id:`、`retry:` 行忽略。

正文开始后超时或断流标记为 partial，不自动重试；只有 reasoning 或还没有正文时才允许通用重试，且重试保持当前 thinking 等级，不会降级。

非流式响应从 `choices[0].message.content` 提取正文，从 `reasoning_content` 提取思考文本，从 `tool_calls` 提取原生工具调用。

## 原生工具调用

支持 OpenAI 兼容的 `tools`：

```json
{"type": "function",
 "function": {"name": "__time", "description": "...", "parameters": {...}}}
```

Harness 会把内置工具和 MCP 工具转换成同一份 `function` 定义；流式分片的工具参数会拼接后作为 JSON 解析。工具结果以 `role=tool`、`tool_call_id` 和内容回传。端点或模型拒绝原生 tools 时自动回退到文本 ```tool``` JSON 块；也可用 `/native off` 强制回退。每个原生 schema 必须与 `Agent._execute_tool` 支持的工具名保持同步。

## 错误与重试

- HTTP 408/429/500/502/503/504/529 可重试；
- `rate_limit`、`overloaded`、`server_error`、`timeout`、`connection` 等网络/网关错误可重试；
- 认证、权限、模型不存在、参数无效等 4xx 配置错误不重试；
- `Retry-After` 受 `api_retry_after_max_ms` 限制；
- 重试使用指数退避、`api_retry_max_ms` 上限和 `api_retry_jitter_pct` 抖动，避免多个请求同时重试。

## 搜索

`__search` 在 `search_provider=auto` 时：

- OpenAI 官方端点使用 Responses API 的 `web_search` 服务端工具；
- DeepSeek 直连端点使用 `deepseek_web_search`（已关闭 thinking）；
- OpenCode Go 或其他只提供模型推理的兼容端点使用 Bing 免 Key 搜索。

原生搜索失败会自动回退到 Bing；显式 `search_provider=bing` 时不回退。搜索结果是不可信数据，不是指令。

## 限制

- 只实现 Chat Completions，不使用 OpenAI Responses API 作为聊天协议；Responses API 仅用于搜索工具；
- 不支持 Files、Batches、Assistants、Realtime、prompt caching 显式断点等云端特性；
- 不支持 OAuth 登录；需要 `Authorization: Bearer` 的兼容代理可直接使用本路径；
- 不同网关对 `temperature`、`max_tokens`、thinking 字段和工具 schema 的接受程度不同，遇到 400 时优先检查 Base URL、模型名和网关文档。
