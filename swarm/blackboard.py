# swarm/blackboard.py
"""
任务白板（Blackboard）：Swarm 中所有 Agent 共享的任务池。

黑板模式（Blackboard Pattern）：
  - 发布者（任何 Agent 或用户）往白板上写任务
  - 消费者（专家 Agent）从白板上认领自己能做的任务
  - 白板保证：同一个任务不会被两个 Agent 重复认领（加锁保证原子性）
"""
import asyncio
import dataclasses
import json
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """白板上的一个任务。"""
    id: str
    type: str                # 任务类型（Agent 根据类型决定要不要认领）
    payload: Any             # 任务内容（自由格式）
    status: str = "pending"  # pending | claimed | done | failed
    owner: str | None = None # 认领这个任务的 Agent ID
    result: Any = None       # 任务完成后的结果
    error: str | None = None # 任务失败时的错误信息
    derived_task_ids: list[str] = field(default_factory=list)  # 由这个任务自动派生出的子任务 ID


class Blackboard:
    """
    线程安全的任务白板。

    核心保证：claim() 操作是原子的——即使多个 Agent 同时调用，
    每个任务只会被一个 Agent 认领。
    """

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()
        self._new_task_event = asyncio.Event()   # 有新任务时触发，唤醒等待的 Agent
        self._known_types: set[str] = set()       # 已注册消费者的任务类型，post() 时校验用

    def register_consumer(self, task_types: list[str]):
        """
        登记"有 Agent 能处理这些任务类型"。

        由 SwarmAgent.__init__ 在创建实例时自动调用（见 agent_base.py），
        不需要手动调用。post() 发布任务时会检查类型是否在这个集合里，
        避免出现"发布了但没有任何 Agent 会认领"的任务永远卡在 pending。
        """
        self._known_types.update(task_types)

    async def post(self, task_type: str, payload: Any) -> str:
        """
        发布一个新任务到白板。返回任务 ID。

        任何 Agent 或外部代码都可以调用这个方法发布任务。

        如果 task_type 不在任何已注册 Agent 的 task_types 里，说明发布了
        一个没人处理的任务类型（比如手写字符串时的笔误），直接抛异常，
        而不是让任务静默地永远停在 pending。
        """
        if self._known_types and task_type not in self._known_types:
            raise ValueError(
                f"未知任务类型：'{task_type}'。已注册消费者的类型：{sorted(self._known_types)}"
            )

        task_id = str(uuid.uuid4())[:8]   # 短 ID，方便日志查看
        task = Task(id=task_id, type=task_type, payload=payload)

        async with self._lock:
            self._tasks[task_id] = task

        # 通知所有等待中的 Agent 有新任务了
        self._new_task_event.set()
        self._new_task_event.clear()

        print(f"[Blackboard] 新任务 {task_id}（类型：{task_type}）")
        return task_id

    async def claim(self, task_type: str, agent_id: str) -> Task | None:
        """
        尝试认领一个指定类型的 pending 任务。

        这是白板最关键的方法——必须保证原子性（用锁）：
        从"读取 pending 任务"到"标记为 claimed"这两个操作不可被打断，
        否则两个 Agent 可能同时认领同一个任务。

        返回认领的任务，如果没有匹配的 pending 任务则返回 None。
        """
        async with self._lock:
            for task in self._tasks.values():
                if task.type == task_type and task.status == "pending":
                    task.status = "claimed"
                    task.owner = agent_id
                    return task
        return None

    async def complete(self, task_id: str, result: Any):
        """标记任务为已完成并保存结果。"""
        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = "done"
                self._tasks[task_id].result = result
                print(f"[Blackboard] 任务 {task_id} 完成")

    async def fail(self, task_id: str, error: str):
        """标记任务为失败并记录错误。"""
        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = "failed"
                self._tasks[task_id].error = error
                print(f"[Blackboard] 任务 {task_id} 失败：{error}")

    async def wait_for_task(self, timeout: float = 5.0):
        """等待新任务到来（供 Agent 的轮询循环使用）。"""
        try:
            await asyncio.wait_for(self._new_task_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def post_derived(self, parent_task_id: str, task_type: str, payload: Any) -> str:
        """
        Agent 处理任务时自动派生新任务用这个方法，而不是直接调 post()。

        效果跟 post() 完全一样（一样会经过 _known_types 校验、一样会唤醒等待中的
        Agent），多做的唯一一件事是：把新生成的 child_id 记到父任务的
        derived_task_ids 里，让"这个任务后来触发了什么"变得可查询，而不是只存在于
        日志里。
        """
        child_id = await self.post(task_type, payload)

        async with self._lock:
            parent = self._tasks.get(parent_task_id)
            if parent is not None:
                parent.derived_task_ids.append(child_id)
            else:
                print(f"[Blackboard] post_derived: 父任务 {parent_task_id} 不存在，"
                      f"子任务 {child_id} 仍正常发布，但不会出现在任何 derived_task_ids 里")

        return child_id

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def get_all(self) -> list[Task]:
        return list(self._tasks.values())

    def summary(self) -> dict:
        """返回白板状态摘要（用于监控）。"""
        from collections import Counter
        counts = Counter(t.status for t in self._tasks.values())
        return dict(counts)

    async def save_to_redis(self, redis_client):
        """
        把当前白板状态保存到 Redis，支持重启后恢复。

        Redis 只能存字符串/字节，不能直接存 Python 对象，所以要先把
        Task（dataclass）转成 dict（dataclasses.asdict），再转成 JSON 字符串（json.dumps）。
        """
        try:
            async with self._lock:
                tasks_data = {tid: dataclasses.asdict(t) for tid, t in self._tasks.items()}
            await redis_client.set("blackboard:tasks", json.dumps(tasks_data))
        except Exception as e:
            print(f"[Blackboard] 保存到 Redis 失败（降级为纯内存模式）：{e}")

    async def load_from_redis(self, redis_client):
        """从 Redis 恢复白板状态（进程启动时调用一次）。"""
        try:
            data = await redis_client.get("blackboard:tasks")
        except Exception as e:
            print(f"[Blackboard] 连接 Redis 失败，跳过恢复，从空白板启动：{e}")
            return

        if not data:
            print("[Blackboard] Redis 里没有历史任务数据，从空白板启动")
            return

        tasks_data = json.loads(data)
        async with self._lock:
            for tid, t_dict in tasks_data.items():
                if t_dict["status"] in ("pending", "claimed"):
                    t_dict["status"] = "pending"
                    t_dict["owner"] = None
                    self._tasks[tid] = Task(**t_dict)
        print(f"[Blackboard] 从 Redis 恢复了 {len(self._tasks)} 个未完成任务")