---
name: api-design
description: 本项目 FastAPI 端点设计规范——路由、状态码、响应模型
triggers:
  - 接口
  - API
  - REST
  - 路由
  - endpoint
  - FastAPI
  - HTTP
  - 状态码
---

## 已有端点参考（main.py）

| 方法 | 路由 | 用途 |
|------|------|------|
| POST | `/ask` | 通用对话入口 |
| POST | `/swarm/ask` | 发布 Swarm 任务 |
| GET | `/swarm/tasks/{task_id}` | 按 ID 查任务状态 |
| GET | `/swarm/tasks` | 任务列表摘要 |
| POST | `/swarm/tasks/{task_id}/apply` | 将已完成任务的代码写入文件 |

## 路由命名规则

- 资源名词复数：`/tasks`、`/sessions`
- 层级嵌套表示从属：`/swarm/tasks/{id}/apply`
- 操作用 HTTP 方法，不用动词路由（`/getTask` ✗）

## 状态码使用（本项目已有的模式）

```python
# 404 - 资源不存在
if not task:
    raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

# 409 - 状态冲突
if task.status != "done":
    raise HTTPException(status_code=409, detail=f"任务状态为 {task.status}，只有 done 状态可以 apply")
```

## 响应模型（Pydantic）

每个端点必须有明确的 Response 模型：

```python
class SwarmAskResponse(BaseModel):
    task_id: str
    status: str
    result: str | None = None
    derived_task_ids: list[str] = []
```

禁止直接返回 `dict`——Pydantic 模型提供自动校验和 OpenAPI 文档。

## 新增端点的清单

1. 在 `main.py` 定义 Request/Response 模型
2. 实现端点函数
3. 在 `static/index.html` 加对应的前端调用
4. 手动用 curl 或浏览器测试一遍
