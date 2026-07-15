---
name: error-handling
description: 本项目的异常处理规范——降级策略、日志格式、自定义异常。审查或编写涉及 try/except、错误处理、降级逻辑的代码时加载。
---

## 分层捕获原则

本项目的分层结构决定了异常在哪里捕获：

| 层 | 文件 | 策略 |
|----|------|------|
| HTTP 接口层 | `main.py` | 捕获 → 转为 HTTPException（404/409/422） |
| Swarm 层 | `swarm/*.py` | 不捕获，让 `agent_base.py` 的 `start()` 统一 `fail()` |
| 工具层 | `sandbox.py` | 抛出明确异常（`PathTraversalError`） |
| 外部依赖 | Redis/LLM API | 捕获 → 降级 + 单次日志 |

## 降级模式（已验证有效）

参考 `main.py` 中 `periodic_save()` 的实现：

```python
fail_count = 0
while True:
    try:
        await blackboard.save_to_redis(redis)
        fail_count = 0
    except Exception as e:
        fail_count += 1
        if fail_count == 1:
            print(f"[Save] Redis 保存失败：{e}")  # 只打一次
        if fail_count >= 3:
            await asyncio.sleep(60)  # 连续失败则降速
            continue
    await asyncio.sleep(5)
```

核心：**不刷屏、不崩溃、自动降速**。

## 日志格式

```python
# 正确：模块名 + 操作 + 原因 + 上下文 ID
print(f"[Blackboard] 保存到 Redis 失败（降级为纯内存模式）：{e}")
print(f"[{self.agent_id}] 任务 {task.id} 处理失败：{e}")

# 错误：
print("出错了")              # 没有上下文
print(f"Error: {e}")         # 不知道是哪个模块
```

## 自定义异常

- 继承 `ValueError`：用户输入不合法 → `PathTraversalError`
- 继承 `RuntimeError`：运行时状态异常 → `TaskTypeUnknownError`
- 类名必须以 `Error` 结尾
