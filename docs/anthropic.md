# Anthropic Messages API

Harness 支持两种模型协议，可在 `/setup`、`/model` 或网页“设置 → 模型”中选择：

```text
api_provider = "openai"      # OpenAI 兼容 chat/completions（默认）
api_provider = "anthropic"   # Anthropic Messages /v1/messages
```

切换协议不会自动替换已保存的 API Key；请同时确认 Key 与端点属于同一供应商。切换时如果 Base URL 仍是已知默认值（OpenAI、DeepSeek、Anthropic），Harness 会换成新协议的默认地址；自定义代理地址保持不变。OpenAI 兼容路径见 [OpenAI 兼容 API](openai.md)。

## 请求映射

| Harness | Anthropic Messages |
|---|---|
| `base_url` | `https://api.anthropic.com`，自动追加 `/v1/messages`；以 `/v1` 结尾时追加 `/messages` |
| `api_key` | `x-api-key` 请求头 |
| 固定版本 | `anthropic-version: 2023-06-01` |
| 会话路由 | `x-opencode-session: ses_...`（OpenCode Go 的 Anthropic 端点要求；官方 Anthropic 会忽略未知头） |
| `model` | `model` |
| `max_tokens` | `max_tokens`（必填，默认 32768） |
| `system` 消息 | 顶层 `system`；多条 system 合并后作为一个带 `cache_control` 的 text 块 |
| user/assistant 历史 | `messages`；空消息丢弃；开头孤立的 assistant 丢弃；连续同角色纯文本合并 |
| `temperature` | **不发送**。当前 Claude 模型对自定义采样参数返回 400 |
| `thinking_level` | `adaptive` + `output_config.effort` |
| `stream` | **始终为 true**（`complete()` 也走 SSE）；非流式长回答会阻塞 TLS 读取并触发看门狗复位 |
| prompt caching | `prompt_cache=true`（默认）时在 system 末尾和最近一条 `tool_result` 末尾各打一个 `ephemeral` 断点；`usage.cache_read_input_tokens` 会回显到模型事件和状态页 |

`thinking_level` 只有四档，与 OpenAI 兼容路径共用同一份持久化配置：

- `disabled`（关闭）：不发送 `thinking` 和 `output_config`；
- `low` / `high` / `max`：发送
  ```json
  {"thinking": {"type": "adaptive", "display": "summarized"},
   "output_config": {"effort": "low|high|max"}}
  ```
- `max` 的请求超时至少 900 秒，其他思考级别至少 600 秒；流式响应头等待取 `stream_header_timeout`（默认 300 秒）与该预算的较大值，不会反向缩短；header 之后到首段正文之前另由 `stream_first_token_timeout`（默认 600 秒）控制。

默认 `high`，用户设置写入 `config.json` 并在重启/复位后恢复；旧的 `default`/`medium`/`middle` 输入会规范化为 `high`（因此不再有“完全不发送 thinking”的旧 `default` 语义）。

当前 Claude 模型（Opus 5.5、Sonnet 5.5、Fable 等）不接受 `budget_tokens`，也不接受关闭 thinking 的 `{"type":"disabled"}`。因此 `disabled` 只是不发送 thinking 参数，模型仍可能按其默认行为思考；需要更低成本时优先使用 `low` effort 或换更小的模型。

内部请求可以覆盖配置的思考等级：上下文压缩摘要固定使用 `thinking=disabled`，DeepSeek 的 Anthropic 端点还会在原生搜索请求中显式关闭 thinking；思考等级的截断降级也可能把本次请求改成 `thinking_fallback`。串口模型结果会打印 `thinking: configured=..., this request used=...`，`/api/status.thinking` 同时返回配置值 `level`、最近一次实际值 `effective` 和是否被覆盖 `overridden`。DeepSeek 模型在请求了 thinking 但整轮没有返回 reasoning 文本时，也会给出提示，避免把“provider 忽略 thinking”误判成配置未生效。

## 流式解析

支持 Anthropic SSE 事件：

- `message_start`（读取 `message.usage`，包括缓存命中/写入）
- `content_block_start`（text / thinking / tool_use 初始块）
- `content_block_delta`（`text_delta`、`thinking_delta`、`signature_delta`、`input_json_delta`）
- `content_block_stop`（此时才解析完整的工具参数 JSON）
- `message_delta`（`stop_reason`、`stop_details`、`usage.output_tokens`）
- `message_stop`
- `ping`
- `error`（`overloaded_error`、`rate_limit_error` 等）

`tool_use` 的 `input` 在流式响应里是分段 `partial_json`：开始时按 `index` 建立空缓冲区，所有 `input_json_delta` 只追加不解析，收到 `content_block_stop` 才 `json.loads`；完全没有参数的工具（没有 delta）按 `{}` 处理，解析失败会带 `error` 标记交给 Agent 记录为工具错误，而不是把半截 JSON 当参数执行。

`signature_delta` 会与 thinking 文本一起保存。原生工具调用回传 `tool_result` 时，assistant 消息会按原始顺序原样带回完整的 content blocks（text、带 `signature` 的 thinking、`redacted_thinking` 和 tool_use），满足扩展思考/交错思考下的回传要求；只有 `thinking_level=disabled` 时才省略 thinking 块。

`stop_reason` 映射到现有 Harness 逻辑：

| Anthropic | Harness |
|---|---|
| `end_turn` / `stop_sequence` | `stop` |
| `max_tokens` | `length`（触发 token 预算重试；若在工具参数中途截断，清空工具调用并报错，绝不执行半截工具） |
| `tool_use` | `tool_calls` |
| `pause_turn` | `pause`（主对话请求直接报错不执行；原生搜索会带上 assistant `content` 最多续传 3 次） |
| `refusal` | 直接报错，并带上 `stop_details.category` |

正文开始后超时/断流标记为 partial，不自动重试；只有 reasoning 或没有正文时才允许通用重试，且重试保持当前 thinking 等级，不会降级。

## 工具结果配对

Anthropic 要求一条 assistant 消息中的每个 `tool_use` 都在紧邻的下一条 user 消息里有对应的 `tool_result`。转换器会：

- 把同批所有工具结果合并到**一条** user 消息，`tool_result` 块放最前面；
- 工具执行失败或超时时使用 `"is_error": true`，而不是只把 `status=error` 写进文本；
- `tool_result.content` 为空时写成 `(no output)`；
- 如果本地事件/通知恰好落在工具调用和结果之间，将其文本并入同一条 `tool_result` user 消息的末尾，不拆散配对；
- 缺失的工具结果补一条 `is_error` 的占位，避免历史损坏导致整个请求 400；没有对应 `tool_use` 的孤立结果会被丢弃。

## 用量

每个流式响应都会记录 `input_tokens`、`output_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens`，并在串口 `--- model result`、网页模型事件和 `/api/status.model.usage` 中显示。`cache_read_input_tokens > 0` 表示提示缓存命中。

## 错误与重试

- HTTP 408/429/500/502/503/504/529 可重试；
- `overloaded_error`、`rate_limit_error`、`api_error`、`timeout_error` 可重试；
- `authentication_error`、`permission_error`、`not_found_error`、`invalid_request_error` 不重试；
- `Retry-After` 受 `api_retry_after_max_ms` 限制；
- 401/403 不会反复重试，避免把配置问题拖成多次超时。

## 限制

- 支持 Anthropic 原生 `tools`：内置工具和 MCP 工具以 `name`/`description`/`input_schema` 发送，流式解析 `content_block_start` 的 `tool_use` 与 `content_block_delta` 的 `input_json_delta`，并在 `content_block_stop` 后才解析完整参数，用带 `is_error` 的 `tool_result` 内容块回传结果。端点拒绝原生 tools 时回退到文本 `tool` JSON 块；也可用 `/native off` 强制回退。
- 支持 Messages API 显式提示缓存断点（`prompt_cache`，默认开启；DeepSeek 的 Anthropic 兼容端点自动跳过）。system 不足最小缓存长度时服务端会静默不缓存，不影响请求；如果某个第三方兼容网关不接受 `cache_control`，把 `prompt_cache` 设为 `false` 可恢复纯字符串 `system`。
- 会发送 `x-opencode-session`，兼容 OpenCode Go 的 Anthropic 路由要求；官方 Anthropic 不使用该头但会忽略它。
- 不支持 OAuth 登录；使用 `x-api-key`。需要 `Authorization: Bearer` 的 Anthropic 兼容代理请优先使用其 OpenAI 兼容端点，或调整代理的鉴权方式。
- 不支持 Claude 的 Files、Batches、Managed Agents 等云端特性；提示缓存只使用请求级 `cache_control` 断点。
- `__search` 在 `search_provider=auto` 时会复用 Anthropic `api_key` 调用 Messages API 的 `web_search_20250305` 服务端搜索工具，不需要单独的搜索 Key；网关不支持该工具时自动回退到 Bing 免 Key 搜索。若 Base URL 指向 DeepSeek 的 Anthropic 兼容端点，`auto` 改用 DeepSeek 的 `deepseek_web_search` 服务端工具（显式关闭 thinking），不再调用已移除的独立 DeepSeek Search API。搜索设置只有 `auto` 和 `bing` 两个值。
- 当前模型可能拒绝自定义 `temperature`；Harness 在 Anthropic 路径始终省略该字段。

## 示例

串口：

```text
/model
API format openai|anthropic [openai]: anthropic
Base URL [https://api.anthropic.com]: 
Model [claude-opus-5-5]: claude-opus-5-5
API key (empty=keep, '-'=clear): sk-ant-...
```

网页侧边栏“设置 → 模型”选择“Anthropic Messages”，Base URL 填 `https://api.anthropic.com`，Model 填例如 `claude-opus-5-5`。保存后 `/test api` 会发送一个最小请求验证鉴权、TLS 和协议。
