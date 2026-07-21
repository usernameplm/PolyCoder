---
name: prompt-engineering
description: 本项目 Agent 提示词规范——结构模板和输出解析约定。修改子 Agent 的 system prompt、新增 Agent、设计 NEEDS_FIX 之类输出标记时加载。
---

## 本项目 system prompt 结构

每个子 Agent 的 system prompt 必须包含：

```
1. 角色定位（一句话）
2. 工作流程（编号步骤）
3. 输出格式（精确模板，含解析标记）
4. 约束（禁止项）
```

示例（reviewer_agent.py 的实际 system prompt）：
```
你是一名资深代码审查工程师。
审查维度：SQL 注入、命令注入、硬编码密码、逻辑错误、边界条件、性能问题。
每个问题输出：[Critical/Warning/Suggestion] 行号 - 问题描述 - 建议修复。
发现 Critical 级别问题时，最后一行输出 NEEDS_FIX:true，否则输出 NEEDS_FIX:false。
```

## 输出解析约定

本项目通过**约定格式标记**来让代码提取 LLM 的结构化输出：

| 标记 | 用途 | 解析方 |
|------|------|--------|
| `NEEDS_FIX:true/false` | Reviewer 决定是否派生 debug 任务 | `reviewer_agent.py` 用 `in` 判断 |
| ` ```python ... ``` ` | 代码块 | `code_extractor.py` 用正则提取 |
| `Bug 根因：xxx` | Debugger 的诊断结论 | 人读 / 前端展示 |

## 关键原则

1. **用"必须"/"禁止"**，不用"建议"/"尽量"——模糊措辞让 LLM 自行发挥
2. **输出格式写死**——方便下游代码用字符串匹配 / 正则提取
3. **一个 Agent 只干一件事**——不要让 reviewer 又审查又修复

## 新增 Agent 时的检查清单

- [ ] system prompt 有没有明确输出格式？
- [ ] 下游代码能不能稳定解析这个输出？
- [ ] 异常情况（LLM 不遵守格式）有没有兜底？
