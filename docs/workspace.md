# Workspace 工作区

`/workspace` 是 Harness 的共享工作区，用来保存用户明确要求保存的笔记、配置、校准数据、脚本和常用资料。它与 Skill 目录 `/skills/<name>/` 分开。

```text
/workspace/              持久数据
/workspace/tmp/          AI 临时数据，重启和更新后保留（不会自动清空）
```

## 模型如何使用

模型可以通过内置的 `__workspace` 工具操作工作区。查看目录或读取文件时应直接调用 `summary`/`list`/`read`/`stat`；不要为了“看一眼 Workspace”生成 `import os` 再 `listdir`/`open` 的 Python 代码，只有确实需要对文件内容做计算时才生成代码。

> 旧版模型自定义 Tool 已移除。工作区文件操作请使用 `__workspace`；生成代码需要硬件时直接 `import machine`/`neopixel` 等 MicroPython 原生模块。没有 `api` 对象，也没有 `api.workspace`。

````text
```tool
{"name":"__workspace","args":{"action":"summary"}}
```

```tool
{"name":"__workspace","args":{"action":"list","path":""}}
```

```tool
{"name":"__workspace","args":{"action":"read","path":"notes/board.md"}}
```

```tool
{"name":"__workspace","args":{"action":"run","path":"tmp/script.py"}}
```

```tool
{"name":"__workspace","args":{"action":"write","path":"notes/board.md","content":"..."}}
```

```tool
{"name":"__workspace","args":{"action":"append","path":"tmp/script.py","content":"..."}}
```

```tool
{"name":"__workspace","args":{"action":"replace","path":"tmp/script.py","match":"old text","content":"new text"}}
```

```tool
{"name":"__workspace","args":{"action":"mkdir","path":"datasets"}}
```

```tool
{"name":"__workspace","args":{"action":"remove","path":"notes/old.md"}}
```
````

`write` 默认覆盖已有文件；传入 `"overwrite": false` 可以拒绝覆盖。`append` 在文件末尾追加；`replace` 用 `match` 定位并用 `content` 替换，`match` 必须唯一，除非传 `"all": true`；`content` 为空表示删除匹配内容。`write`/`append`/`replace` 只修改文件，不执行代码。

### 使用约定

- 用户说“保存 / 记录 / 以后要用”时，写入 `/workspace` 持久区。
- 解析中间结果、临时脚本、一次性数据写入 `/workspace/tmp`。
- 需要多轮调试的较长程序：第一次用 `write` 写到 `/workspace/tmp/<name>.py`，之后用 `replace`/`append` 只发送改动的片段，再 `run` 这个文件；不要在每一轮重新返回完整程序，也不要同时再放一份相同的 python 代码块。短小的一次性程序仍可直接返回 python 代码块。
- 工作区脚本是数据，不会自动执行。开启 `/auto run on` 后，模型可用 `__workspace run` 直接执行 `.py` 文件并把输出作为工具结果；未开启时，Harness 会显示源码并等待串口确认，拒绝则取消执行。执行超时是 JSON `args.timeout` 字段（可选，受 `code_timeout_max_s` 限制），与 Python 代码块开围栏上的 `timeout=` 注解相互独立。`__workspace run` 和串口 `/workspace run` 都读取完整脚本，上限是 `workspace_file_limit`；`code_limit` 只限制模型生成的 python 代码块，不限制已经写入工作区的脚本。
- 短脚本优先前台执行。长时间运行或需要与用户继续交互时，传入 `"background": true`（或串口 `/workspace run bg <path>`）创建后台任务；后台脚本仍受 `background_execution`、最大并发、协作停止和看门狗约束，`timeout=0` 只表示无截止时间，不是硬杀。后台任务运行时，前台 `__workspace run` 仍通过主线程 handoff 执行；避免与后台任务同时读写同一个工作区文件。脚本访问硬件时直接 `import machine`/`neopixel` 等 MicroPython 原生模块。
- 可复用驱动可以放在 `/workspace` 下并在脚本里 `import`。MicroPython 默认会把已导入模块缓存在 `sys.modules` 中且不再检查源文件；Harness 会在每次执行前比较 `/workspace` 模块的 stat 戳（路径、大小、mtime/ctime），文件改写或删除后自动移除缓存，通过 `__workspace` 写入/删除时还会立即失效。因此改完驱动再运行同一脚本会加载新代码，不需要在脚本里手动 `sys.modules.pop()`。
- 不要把密钥、Wi-Fi 密码或 API Key 写进工作区；这些仍属于 `secrets.json`。
- 写入前先 `list`/`stat`，避免误覆盖用户文件。

## 用户命令

```text
/workspace                 # 汇总 + 根目录列表
/workspace list [path]
/workspace read <path>
/workspace run <path>      # 显示源码并确认后前台执行 .py
/workspace run bg <path> [timeout]  # 确认后后台执行；默认 code_timeout_max_s
/workspace mkdir <path>
/workspace remove <path>   # 需要确认
/workspace clean           # 清空 /workspace/tmp，需要确认
```

## 网页文件页

侧边栏“文件”页可以浏览整个设备 VFS、下载文件、查看/编辑文本、上传 UTF-8 文本、新建文件夹或文本文件、重命名和删除。只有 `/workspace` 下的路径可以写入、上传、重命名和删除；`/workspace` 与 `/workspace/tmp` 本身不能删除或重命名。固件、启动文件、`/harness`、`/skills`、`/state` 等系统文件在界面中标为“系统”，只能查看/下载。`/secrets.json` 和 `/mcp.json` 标记“敏感”，网页 API 不读取、不下载。上传只支持文本，受 `workspace_file_limit` 和工作区总量限制；下载单文件上限 256 KB。文件页是 `__workspace` 之外的图形化入口，不改变工作区限制，也不是代码执行沙箱。

## 限制

默认配置：

| 配置 | 默认值 |
|---|---:|
| `workspace_file_limit` | 32768 字符 |
| `workspace_total_limit` | 262144 字符 |
| `workspace_files_limit` | 64 个文件 |
| `workspace_read_limit` | 16384 字符 |
| `workspace_list_limit` | 100 项 |

路径可以是相对 `/workspace` 的相对路径，或以 `/workspace` 开头的绝对路径；禁止 `..`、反斜杠和非法字符，最多 4 层目录。`/workspace` 本身只作为根目录。

## 与 Skill 数据目录的区别

| 目录 | 用途 | 生命周期 |
|---|---|---|
| `/workspace` | 用户资料和共享数据 | 持久，启动保留 |
| `/workspace/tmp` | 模型临时文件 | 重启和更新后保留；需要时手动 `/workspace clean` |
| `/skills/<name>/` | Skill 指令包 | 持久；删除 Skill 时移除 |

工作区不是代码执行沙箱，也不是秘密存储。它只是 Harness 提供的受限文件接口。
