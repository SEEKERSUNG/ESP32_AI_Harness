# 增加外设、Skill、模型供应商或 MCP

## 增加外设能力

外设默认关闭，必须通过 `/peripherals` 显式配置。生成代码通过受控的全局 `api` 对象（`HardwareAPI`）访问已配置硬件：`api.gpio`、`api.spi`、`api.i2c`、`api.uart`、`api.neopixel`、`api.adc`；所有调用仍经过启用开关、允许引脚、总线配置和传输大小限制。添加一种新总线或驱动时：

1. 在 `hardware.py` 增加配置校验、保留引脚规则、`HardwareManager` 底层方法和 `HardwareAPI` 包装方法；
2. 在 `console.py` 的 `/peripherals` 子命令中登记配置入口，并更新 `Board.capabilities()` 与系统提示词中的 `api` 能力说明；
3. 记录引脚、电压、有效电平、允许范围和安全状态；
4. 对输出限幅、超时、断联安全使用明确规则；
5. 添加 host Fake 测试和真实硬件测试，先空载，再连接执行器；
6. 更新 `docs/setup.md`、`docs/commands.md`、`docs/safety.md` 和本文。

高风险负载不应仅依赖模型自律或串口确认。硬件层只暴露已配置的引脚和总线，但不是完整的电气安全层。

## 增加 Skill

Skill 不是模型供应商适配器，也不是可执行代码；它是 `/skills/<name>/SKILL.md` 指令包：

```text
/skills/<name>/SKILL.md
```

新增或修改时：

- `skills.py`：`SKILL.md` frontmatter、名称校验、读取和删除；
- `agent.py`：`skill` 提案解析和内置 `__skill` 接口；
- `console.py`：`/skills`、`/skill-show`、`/skill-delete` 命令；
- host Fake 测试以及真实设备烟雾测试；
- 安全文档中的提示注入和删除风险。

不要在 `agent.py` 或 `console.py` 中加入供应商或具体外设型号分支。Harness 不再支持模型自定义可执行 Tool；需要复用流程时请使用 Skill 记录步骤，需要执行时生成一次性代码。

## 增加 OpenAI 兼容供应商

通常只需通过 `/model` 修改：

- Base URL；
- 模型名；
- API Key；
- 对应 CA。

兼容前提是供应商支持：

```text
POST <base_url>/chat/completions
Authorization: Bearer <api_key>
stream = true
```

并返回 SSE `data:` 事件或非流式 `choices[0].message.content` 字符串/文本内容块。若供应商使用不同鉴权、请求字段或响应结构，在 `llm.py` 增加独立适配器，不要把提供商分支散落到 Agent 中。将 `stream=false` 可退回非流式响应。

OpenCode Go 等网关要求额外的 `x-opencode-session` 会话头；Harness 在 `llm.py` 统一附加稳定会话 ID，并在 `/clear` 时轮换，不需要在 Agent 或 Console 中增加供应商分支。

## 增加 Anthropic Messages 兼容供应商

Anthropic 路径独立在 `llm_anthropic.py`，通过 `create_llm()` 根据 `api_provider` 选择，不把分支散落到 Agent：

- 端点默认 `<base_url>/v1/messages`，以 `/v1` 结尾时追加 `/messages`；
- Header 使用 `x-api-key` 和 `anthropic-version: 2023-06-01`；
- system 消息合并到顶层 `system`，user/assistant 保留；
- thinking 使用 `{"type":"adaptive","display":"summarized"}` + `output_config.effort`；
- 流式解析 `content_block_delta` 的 `text_delta`/`thinking_delta`，`stop_reason` 映射为 Harness 的 `finish_reason`；
- 新模型可能拒绝 `temperature`，适配器不发送。

如果供应商只接受 `Authorization: Bearer` 或不同事件结构，建议先在适配器内加一个显式的鉴权/事件模式配置，而不是修改 Agent。Anthropic 路径使用原生 `tools`/`input_schema`，流式解析 `tool_use` 与 `input_json_delta`；端点拒绝原生工具时 Agent 自动回退到文本 `tool` 块，也可用 `/native off` 强制文本协议。

## 增加 MCP 传输或能力

`mcp.py` 目前只实现 Streamable HTTP（legacy initialize/session 和无状态 2026-07-28）、tools/resources/prompts 读取。扩展时：

1. 在 `MCPClient._request` 增加传输/响应模式，保持 JSON-RPC id 匹配和大小上限；
2. 在 `MCPStore.validate` 增加配置字段，但 Header 值永远不要进入公共 API；
3. 在 `MCPManager` 增加工具/资源暴露和确认策略；
4. 保持工具输出为不可信数据，限制输出、schema 和分页；
5. 添加 Fake HTTP 测试（JSON/SSE/404/401/协议降级）和真实远程服务器测试；
6. 更新 `docs/mcp.md` 和 `docs/safety.md`。

不支持 stdio 是因为 ESP32 不能启动子进程；不要在设备端模拟子进程。OAuth 需要浏览器/回调，设备端没有安全的重定向 URI；优先使用静态 Token 或主机侧桥接。

## 增加后台自主任务

`background_execution` 现在支持多个 worker 线程任务与前台并行，但仍不是模型自主调度器：任务由用户确认或 AUTO 创建，最多 `background_max_jobs` 个同时运行，仍受协作式停止、确认规则和看门狗约束，结果由用户/UI 或自动继续触发。增加定时、重启后继续或无人自主任务前至少需要：

- 可持久化的调度策略和任务依赖/重试语义；
- 独立的安全状态与停止机制（当前 worker 线程无法硬杀）；
- 不依赖协作式取消的执行隔离；
- 网络断开和反复复位处理；
- Flash 写入频率限制；
- 用户可见的权限和审计记录。

纯 MicroPython 的 `exec()` 不是进程级沙箱，无法可靠强制终止任意代码。需要强隔离时应评估定制原生监督层或其他 VM，而不是继续堆叠提示词限制。
