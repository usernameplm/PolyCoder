# Swarm 模式排查与修复记录

日期：2026-07-03

背景：给前端加了 Swarm 模式调用入口后，在实际测试 `POST /swarm/ask` 的过程中，
陆续发现了几个跟"预期不符"的现象，这份文档记录：

1. 本次真正动手修的问题：`task_type` 没有值域校验、`test_write` 任务没人处理
2. 顺带排查但**不是代码问题**的几个现象（端口冲突、字段含义误解等），避免以后重复排查

---

## 一、本次修复：task_type 值域校验 + 补全 test_write 消费者

### 问题

`task_type` 在三处各自为战，互不校验：

| 位置 | 改动前 |
|---|---|
| `main.py` `SwarmAskRequest.task_type` | 裸 `str`，只有 `description` 文档提示，不做值域校验 |
| `swarm/blackboard.py` `Task.type` | 裸 `str`，`post()` 不检查任何合法性 |
| 各 `SwarmAgent.task_types` | 各自声明能处理的类型，但只在 `claim()` 时按字符串匹配，跟"发布"这一侧没有联动 |

后果就是实际发生过的情况：`DebuggerSwarmAgent` 修复完成后会发布一个
`task_type="test_write"` 的任务（`swarm/debugger_agent.py`），但当时
`swarm/` 目录下**没有任何 Agent 声明处理 `test_write`**——任务被静默地
永远卡在 `pending`，不报错、不提示，只能靠人工翻日志才能发现。

### 改动前 / 改动后对比

**API 层校验**（`main.py`）：

```diff
- task_type: str = Field(..., description="任务类型，如 code_review / debug", examples=["code_review"])
+ task_type: TaskType = Field(..., description="任务类型，范围见 swarm/task_types.py", examples=["code_review"])
```

`TaskType` 是新增的 `swarm/task_types.py` 里定义的 `Literal["code_review", "debug", "test_write"]`。
传入范围外的值现在会在 Pydantic 校验阶段直接返回 422，而不是等任务进了白板才发现没人处理。

**白板运行时校验**（`swarm/blackboard.py`）：

```diff
  def __init__(self):
      self._tasks: dict[str, Task] = {}
      self._lock = asyncio.Lock()
      self._new_task_event = asyncio.Event()
+     self._known_types: set[str] = set()

+ def register_consumer(self, task_types: list[str]):
+     """登记有 Agent 能处理这些任务类型，post() 时用于校验"""
+     self._known_types.update(task_types)

  async def post(self, task_type: str, payload: Any) -> str:
+     if self._known_types and task_type not in self._known_types:
+         raise ValueError(f"未知任务类型：'{task_type}'。已注册消费者的类型：{sorted(self._known_types)}")
      task_id = str(uuid.uuid4())[:8]
      ...
```

`SwarmAgent.__init__`（`swarm/agent_base.py`）里自动调用
`self.blackboard.register_consumer(self.task_types)`，每个 Agent 一创建就把
自己能处理的类型登记进白板，不需要手动维护一份额外的注册表。

这两层校验分工不同：
- **API 层（Literal）**：挡住外部调用方传入的非法字符串，属于"请求格式错误"，422。
- **白板层（register_consumer）**：挡住内部代码的疏漏（比如某个 Agent 手写字符串时打错字、
  或者发布了一个类型但忘了实现对应的消费者），属于"服务内部配置错误"，
  在 `main.py` 里捕获后转成 `SwarmAskResponse(status="failed", error=...)`，
  避免直接 500。这一层就是精确对应"test_write 没人处理"这个真实发生过的问题。

**补全消费者**：新增 `swarm/test_writer_agent.py::TestWriterSwarmAgent`，声明
`task_types = ["test_write"]`，`handle()` 里调 LLM 为修复后的代码生成 pytest
测试（对齐已有的 `sub_agents/test_writer.py` 的测试覆盖原则：正常路径/边界条件/
异常路径/安全场景）。在 `main.py` 的 `lifespan` 里加进常驻 Agent 列表：

```diff
  agents = [
      ReviewerSwarmAgent(_blackboard),
      DebuggerSwarmAgent(_blackboard),
+     TestWriterSwarmAgent(_blackboard),
  ]
```

链路现在能真正跑完整：`code_review` → （Critical 问题）→ `debug` →
（修复完成）→ `test_write` → 生成测试，每一步都有 Agent 认领并执行。

### 涉及改动的文件

- 新增 `swarm/task_types.py`（集中定义合法类型）
- 新增 `swarm/test_writer_agent.py`（`test_write` 的消费者）
- `swarm/blackboard.py`：`register_consumer()` + `post()` 校验
- `swarm/agent_base.py`：`__init__` 里自动注册
- `swarm/reviewer_agent.py` / `swarm/debugger_agent.py`：硬编码字符串改成引用
  `task_types.py` 里的常量，避免以后再打错字
- `main.py`：`SwarmAskRequest.task_type` 改用 `TaskType`；`agents` 列表加入
  `TestWriterSwarmAgent`；`post()` 调用加 `try/except ValueError`
- `static/index.html`：Swarm 面板下拉框加 `test_write` 选项，方便直接测试

### 验证结果

```bash
# 非法 task_type，现在会被 422 拦下，并且报错信息里带出了合法范围
curl -s -X POST http://127.0.0.1:8002/swarm/ask \
  -H "Content-Type: application/json" \
  -d '{"task_type":"foo_bar","payload":{"code":"x=1","file":"a.py"}}'
# {"detail":[{"type":"literal_error","loc":["body","task_type"],
#   "msg":"Input should be 'code_review', 'debug' or 'test_write'", ...}]}
# HTTP 422

# 直接测 test_write，现在有 Agent 会认领并返回真实结果，不再永远 pending
curl -s -X POST http://127.0.0.1:8002/swarm/ask \
  -H "Content-Type: application/json" \
  -d '{"task_type":"test_write","payload":{"code":"def add(a,b): return a+b","file":"calc.py"},"timeout":40}'
# {"task_id":"...", "status":"done", "result":"```python\n# test_calc.py\n...", "error":null}
```

---

## 二、排查过程中遇到的其他问题（记录用，非代码 bug）

这些是这次测试过程中依次冒出来的疑问，排查完发现都不是代码问题，记录下来避免
下次又花时间重新排查一遍。

| # | 现象 | 排查结论 | 关键点 |
|---|---|---|---|
| 1 | 前端提交后终端打印 `POST /swarm/ask HTTP/1.1" 422` | 请求体没通过 Pydantic 校验（早期 `timeout` 超出 `ge=1/le=120` 范围之类），FastAPI 在进入 `swarm_ask_endpoint` 函数体之前就拒绝了，所以看不到 `[Blackboard]` 之类的业务日志——这是符合预期的行为，不是 bug | 422 的具体原因要看响应体（浏览器 DevTools → Network → Response），终端只记录状态码不记录校验详情 |
| 2 | 用 `python -u main.py` 启动后，怀疑是 `print()` 缓冲导致终端看不到业务日志 | 排查后发现真正原因是**端口冲突**：之前临时起的后台测试服务（`--host 127.0.0.1 --port 8002`）没关，跟新启动的服务（`host="0.0.0.0"`, `--port 8002`）同时监听 8002。浏览器访问 `localhost` → `127.0.0.1`，Windows/Linux 下"更具体地址的监听者优先匹配"，请求全被旧的后台进程接走了，新进程什么都没收到 | `0.0.0.0` 和 `127.0.0.1` 是不同的 socket 地址，同端口可以并存不报错；用 `netstat -ano | grep ":8002"` 能看到俩 PID 同时在监听，这才是判断依据，不是靠猜缓冲 |
| 3 | 审查结果里出现 `文件：auth.py`，但本地根本没有这个文件，以为是读了什么文件 | `swarm/reviewer_agent.py` 里 `filename = payload.get("file", "unknown.py")` 只是一个自由文本标签，塞进 prompt 里给 LLM 当上下文标注用，全程没有任何 `open()`/磁盘读取操作，跟本地文件是否存在无关 | Swarm 版的 Reviewer/Debugger 不带工具调用（跟 `sub_agents/` 下 Coordinator 版的 `read_file` 工具不是一套东西），全部输入都来自请求体里的 `payload.code` |
| 4 | 提交 `code_review` 后前端只显示一段文字，看不到自动派生的 `debug`/`test_write` 的结果 | `SwarmAskResponse` 只有 `task_id/status/result/error` 四个字段，`main.py` 里的轮询逻辑也只盯着**当次提交的那个 task_id**；派生任务是全新的、独立的 `task_id`，接口设计上就没有把它们带出来，而且响应返回那一刻派生任务往往还没跑完 | 如果要在前端看到完整链路，需要额外加一个按 `task_id` 查询任意任务状态的接口（比如 `GET /swarm/tasks/{task_id}`），当前代码没有这个接口 |
| 5 | `debugger_agent.py` 发布了 `test_write` 任务，日志显示"已发布"，但没有 Agent 处理 | 本文档"一、"里已修复。根因：`swarm/` 目录下没有声明处理 `test_write` 的 Agent，`main.py` 的 `agents` 列表里也没注册 | 对比 `coordinator/dispatcher.py::_get_agent()`——Coordinator 模式对"未知 agent 名字"会显式 `raise ValueError`，Swarm 模式之前完全没有这层校验，属于两种模式在健壮性上的差异 |
| 6 | `task_type` 到底有没有限定范围，范围定义在哪 | 改动前：没有，纯字符串，范围只是"当前所有 `SwarmAgent.task_types` 的并集"，且分散在各文件里，没有汇总校验。改动后：见本文档"一、" | 新增 `swarm/task_types.py` 作为唯一定义来源 |

---

## 三、后续可以考虑但本次未做的事

- `GET /swarm/tasks/{task_id}`：按 `task_id` 补查任意任务状态，配合前端展示完整
  `code_review → debug → test_write` 链路（问题 #4 的后续方案）
- `GET /swarm/tasks`：返回白板整体状态摘要（`Blackboard.summary()` 已经有现成方法，
  只是没接到 HTTP 路由上）
- Redis 持久化白板状态（指南第 7.6 节提到，当前是纯内存，进程重启任务状态就丢了）
