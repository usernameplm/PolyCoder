---
name: async-patterns
description: 本项目的 asyncio 规范——白板并发、后台任务、已踩过的坑。审查或编写涉及 async/await/asyncio、并发协程、Lock/Event/Condition 的代码时加载。
---

## 本项目的异步架构

```
main.py (FastAPI + uvicorn 事件循环)
  ├── lifespan()
  │     ├── asyncio.create_task(periodic_save())   ← 后台常驻
  │     └── asyncio.create_task(agent.start())     ← SwarmAgent 常驻循环
  └── HTTP 处理函数（与 Agent 共用同一事件循环）
```

## 后台常驻任务模板

```python
# 参考 main.py 的 periodic_save()
async def background_loop():
    while True:
        try:
            await do_work()
        except Exception as e:
            print(f"[Loop] 错误：{e}")
        await asyncio.sleep(interval)

# 启动
_task = asyncio.create_task(background_loop())
# 关闭
_task.cancel()
await asyncio.gather(_task, return_exceptions=True)
```

## 白板并发的关键约束

1. **claim() 必须在 Lock 内完成读+写**：从"找到 pending 任务"到"标记为 claimed"
   是原子操作，否则两个 Agent 会同时认领同一个任务。

2. **通知新任务用 Condition，不用 Event**：
   ```python
   # ✗ 有竞态（本项目踩过的坑）
   self._event.set()
   self._event.clear()   # 如果 Agent 在 set 和 clear 之间还没醒来，通知丢失

   # ✓ 正确做法
   async with self._condition:
       self._condition.notify_all()
   ```

3. **不要在 Lock 内做 I/O**：
   ```python
   # ✗ 持锁时间过长
   async with self._lock:
       data = await redis.get(...)    # 网络 I/O 阻塞其他协程获取锁

   # ✓ 先读再锁
   data = await redis.get(...)
   async with self._lock:
       self._tasks[tid] = Task(**data)
   ```

## asyncio.sleep vs time.sleep

- `await asyncio.sleep(5)` → 让出控制权，其他协程可以运行
- `time.sleep(5)` → 阻塞整个事件循环，所有 Agent 都卡住 5 秒

本项目全部使用 `asyncio.sleep`，任何出现 `time.sleep` 的代码都是 Bug。
