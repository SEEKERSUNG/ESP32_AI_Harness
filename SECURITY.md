# 安全策略

> **English summary:** Report vulnerabilities privately through GitHub Security Advisories (Security → Report a vulnerability). Never paste API keys, Wi-Fi passwords, admin passwords, session cookies, `secrets.json` or sensitive serial logs into a public issue.

## 支持范围

安全修复面向当前主分支和最新发布版本（`0.4.x`）。旧版本不保证单独回补。

## 报告漏洞

请**不要**在公开 Issue 中粘贴 API Key、Wi-Fi 密码、管理密码、会话 Cookie、设备 `secrets.json` 或含敏感信息的串口日志。

优先通过本仓库的 GitHub Security Advisories（Security → Report a vulnerability）私下联系维护者，并尽量提供：

- 受影响的版本或提交；
- 复现步骤和最小示例；
- 影响范围（例如能否读取秘密、绕过确认、跨网段访问）；
- 建议的修复方向（如有）。

维护者确认后会在修复发布时致谢；未经同意不会公开报告者身份。

## 部署安全要求

本项目按“可信局域网内的单用户开发工具”设计，不是多租户或公网服务：

- **不要**把 Web 端口映射到公网，也不要启用 DMZ、UPnP 端口转发或反向代理暴露管理页面；
- Web 控制台是纯 HTTP，管理密码、Cookie、对话和配置在局域网内可被嗅探或篡改；
- 首次设置后立即修改默认 AP 密码 `esp32setup`，并使用强管理密码；
- 保持 `tls_verify=true` 和有效的 `/ca.pem`；关闭 TLS 验证会把 API Key、对话和代码暴露给中间人；
- 不要把 API Key、Wi-Fi 密码或其他秘密写入 `MEMORY.md`、Skill、Workspace 或公开日志；
- `/mcp.json` 保存 MCP 服务器 Header（可能含 Bearer Token），`/state/conversation.json` 保存对话和摘要；两者都是设备明文文件，串口或物理访问可读取；
- MCP 工具输出是不可信数据，可能包含提示注入；非只读 MCP 工具默认需要确认，`auto=true` 只应在信任服务器和最小权限 Token 时开启；
- 生成代码和 Workspace 脚本拥有设备进程权限，确认机制不是安全沙箱；
- 自动执行（`auto_run`）默认关闭，只应在可信会话中临时开启；
- 生成代码直接使用 MicroPython 原生硬件库，没有 `api` 对象或引脚 allowlist；请核对接线、电压和保留引脚（Flash/PSRAM、USB、控制台 UART、strapping）后再执行；
- 物理串口是信任边界：能访问串口的人可以读取 `secrets.json`、重置网页密码和修改配置。

## 已知非漏洞

以下行为属于设计边界，不属于安全漏洞：

- 拥有物理或串口访问权限的攻击者可以读取设备文件和重置密码；
- 已确认执行的代码可以修改 Harness 文件、读取 `secrets.json` 或发起网络连接；
- 单 Web 线程在长模型回合期间会让其他网页请求排队；
- 执行超时是协作式的：`range()`、`while True`、`time.sleep()` 和 `__harness_check()` 可以停止，但绕过所有钩子的 C 级阻塞或复杂循环仍由看门狗复位；这不是任意代码的强制终止；
- 明文 HTTP 下的局域网嗅探。
