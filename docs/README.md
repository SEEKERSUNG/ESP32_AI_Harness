# 文档索引

> English entry point: [README.en.md](../README.en.md). The detailed guides below are currently in Chinese.

## 初次使用

1. [部署和初始化](setup.md)：固件、依赖、部署、串口、Wi-Fi/模型/CA。
2. [命令手册](commands.md)：串口和网页命令、白名单、典型流程。
3. [Web 控制台与 AP 配网](web.md)：登录、AP 配网、HTTP API、NDJSON 事件。

## 理解实现

- [架构与执行时序](architecture.md)：线程模型、会话、上下文预算、执行和 handoff。
- [开发、测试和调试](development.md)：测试矩阵、MicroPython 兼容注意事项、API 调试。
- [安全、恢复与限制](safety.md)：威胁模型、看门狗、秘密和设备边界。

## 扩展能力

- [Skills](skills.md)：Markdown 指令包和渐进式加载。
- [MEMORY.md](memory.md)：始终加载的项目上下文。
- [Anthropic Messages API](anthropic.md)：协议映射、thinking/effort、Header 和限制。
- [MCP 远程工具](mcp.md)：Streamable HTTP、服务器配置、确认和限制。
- [Workspace 工作区](workspace.md)：持久资料和临时文件。
- [生成代码的硬件 API](hardware.md)：`api.gpio/spi/i2c/uart/neopixel/adc/pwm/touch/rtc/pulse/system`、配置和 ST7735 骨架。
- [增加外设或模型供应商](extending.md)：GPIO/I2C/SPI/UART/NeoPixel/ADC 和模型端点。

## 其他

- [CA 证书说明](../certs/README.md)：公共根证书和 Mozilla bundle。
- [安全策略](../SECURITY.md)：漏洞报告和部署安全要求。
- [变更记录](../CHANGELOG.md)：版本历史。
