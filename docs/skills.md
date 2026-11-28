# Skills

Skill 是对齐 Claude Code Agent Skills 的**指令包**，不是可执行程序。

```text
/skills/<name>/SKILL.md
```

`SKILL.md` 使用 YAML frontmatter：

```markdown
---
name: board-notes
description: How to inspect this ESP32 board
---

# Board notes

Use `device.info()` first.
```

## 渐进式加载

- 系统提示词只加载技能名称和 `description`；
- 模型需要时调用 `__skill read` 加载完整 `SKILL.md`；
- 技能正文只作为上下文，不会自动执行；
- 技能不能直接调用函数；需要执行时应让模型生成一次性代码，由用户确认或 `auto_run` 执行。Harness 不再支持模型自定义可执行 Tool。

模型接口：

````text
```tool
{"name":"__skill","args":{"action":"list"}}
```

```tool
{"name":"__skill","args":{"action":"read","name":"board-notes"}}
```

```tool
{"name":"__skill","args":{"action":"delete","name":"board-notes"}}
```
````

`delete` 会删除整个 `/skills/<name>/` 目录；系统提示词要求模型仅在用户明确要求删除时调用。

## 创建与查看

模型可以用 `skill` 块提出一个技能：

````text
```skill
{"name":"board-notes","description":"How to inspect this board"}
---
# Board notes

Use `device.info()` first.
```
````

保存需要用户确认。命令行：

```text
/skills
/skill-show board-notes
/skill-delete board-notes
```

Skill 没有版本回滚；更新直接覆盖 `SKILL.md`。技能目录中的其他文件可以手工作为补充资料，当前 `__skill read` 只加载 `SKILL.md`。

网页侧边栏“Skill”页列出已保存的 Skill，可展开查看完整 `SKILL.md`；对话中的提案仍显示为独立气泡，保存或丢弃需要用户点击。网页 Skill 页当前只读，删除仍使用 `/skill-delete`。

## 与生成代码的区别

| | Skill | 一次性生成代码 |
|---|---|---|
| 本质 | Markdown 指令 | Python 代码块 |
| 是否执行 | 否 | 用户确认或 `auto_run` 后执行 |
| 存放 | `/skills/<name>/SKILL.md` | 仅 pending/recovery，不进入 Skill 目录 |
| 典型用途 | 约定、流程、构建命令 | 计算、读写数据、一次性任务 |
