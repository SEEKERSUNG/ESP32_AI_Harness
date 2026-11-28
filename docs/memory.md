# MEMORY.md

`/MEMORY.md` 是始终加载的持久项目上下文，类似 Claude Code 的 `CLAUDE.md`：

- 代码约定；
- 构建和部署命令；
- 引脚约定；
- 长期偏好和项目背景。

它在每次模型请求时自动加入系统提示词，和按需加载的 Skills 互补。

## 文件

```text
/MEMORY.md
```

首次启动自动创建模板。内容有 `memory_char_limit`（默认 8192 字符）限制。

## 用户命令

```text
/memory
/memory append Board serial uses 115200 baud.
/memory replace 115200
/memory delete 115200
/memory clear
```

`/memory append` 会在末尾追加一行；`/memory replace <匹配文本>` 会先显示唯一匹配的整行，再提示输入新的整行内容；`/memory delete <匹配文本>` 会列出所有匹配行并要求确认；`/memory clear` 需要确认并恢复模板。匹配是行内包含匹配，区分大小写。

## 模型接口

````text
```tool
{"name":"__memory","args":{"action":"read"}}
```

```tool
{"name":"__memory","args":{"action":"append","content":"Use 115200 baud."}}
```

```tool
{"name":"__memory","args":{"action":"delete","match":"old note text"}}
```

```tool
{"name":"__memory","args":{"action":"replace","match":"old note text","content":"Updated note text."}}
```
````

模型追加内容和替换内容都受 `memory_append_limit`（默认 2048 字符）和总长度限制。`delete` 删除所有包含 `match` 的行；`replace` 要求恰好一行匹配，并用 `content` 重写该整行。模型修改记忆前应先 `read`。不要把 API Key、Wi-Fi 密码写进 MEMORY.md。

## 建议内容

```markdown
# Project Memory

- 串口：COMx，115200
- 目标：ESP32-S3 N16R8 / MicroPython 1.29
- 代码：单文件、在 `code_timeout_s` 内完成（默认 20 秒；较长任务在开围栏同一行声明 `timeout=`）、不访问核心文件
- 外设：I2C0 SDA=16 SCL=15，SSD1306 地址 0x3C
- 复用：把稳定流程写成 Skill；需要执行时生成一次性代码
```

保持简短；细节和长流程应放到 Skills，数据放 Workspace。
