---
name: security
description: 本项目安全规范——路径遍历防护、输入校验、沙箱写入。审查涉及文件操作、用户/LLM 输入、路径拼接、注入风险的代码时加载。
---

## 路径遍历防护（已实现）

本项目通过 `sandbox.py` 的 `resolve_safe_path()` 防止 LLM 输出的文件路径逃出
工作目录：

```python
def resolve_safe_path(workspace: Path, relative: str) -> Path:
    target = (workspace / relative).resolve()
    if not str(target).startswith(str(workspace.resolve())):
        raise PathTraversalError(f"路径 {relative} 逃出了工作区")
    return target
```

**核心逻辑**：`.resolve()` 会把 `../../../etc/passwd` 解析为绝对路径，然后
`startswith` 检查是否还在 workspace 下。

## 必须使用沙箱的场景

| 场景 | 正确做法 | 错误做法 |
|------|----------|----------|
| apply 端点写入代码 | `write_text_sandboxed(workspace, path, code)` | `open(path, 'w').write(code)` |
| 任何用户/LLM 提供的路径 | 先过 `resolve_safe_path()` | 直接拼接使用 |

## 输入校验原则

- LLM 的输出视为**不可信输入**（它可能输出 `../../../../etc/crontab`）
- 用户通过 API 传入的 `file_path` 也是不可信的
- 只有代码里硬编码的路径（如 `skills/` 目录）才可信

## 禁止事项

- 禁止 `os.system()`、`subprocess.run(shell=True)` + 用户输入
- 禁止 `eval()`、`exec()` 执行 LLM 输出
- 禁止把密钥放在代码里或 Git 历史里
