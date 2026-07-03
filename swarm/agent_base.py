# swarm/agent_base.py
"""
Swarm Agent 基类。

和 SubAgent 的区别：
  - SubAgent：无状态，每次调用是独立的
  - SwarmAgent：持久化运行，有自己的 agent_id，持续监听白板
"""
import asyncio
from abc import ABC, abstractmethod
from .blackboard import Blackboard, Task


class SwarmAgent(ABC):

    def __init__(self, blackboard: Blackboard):
        self.blackboard = blackboard
        self.agent_id = f"{self.name}-{id(self) % 10000}"
        self._running = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 类型名称。"""
        ...

    @property
    @abstractmethod
    def handles(self) -> list[str]:
        """这个 Agent 处理哪些类型的任务。"""
        ...

    @abstractmethod
    async def process(self, task: Task) -> str:
        """
        处理一个任务，返回结果字符串。

        任务处理失败时抛出异常（SwarmAgent 会自动标记为 failed）。
        """
        ...

    async def start(self):
        """启动 Agent，持续监听白板上的任务。"""
        self._running = True
        print(f"[{self.agent_id}] 已启动，监听任务类型：{self.handles}")

        while self._running:
            claimed_any = False

            for task_type in self.handles:
                task = await self.blackboard.claim(task_type, self.agent_id)
                if task:
                    claimed_any = True
                    try:
                        result = await self.process(task)
                        await self.blackboard.complete(task.id, result)
                    except Exception as e:
                        await self.blackboard.fail(task.id, str(e))

            if not claimed_any:
                # 没有任务，等待新任务到来（最多等 5 秒，然后再轮询）
                await self.blackboard.wait_for_task(timeout=5.0)

    def stop(self):
        """停止 Agent。"""
        self._running = False
        print(f"[{self.agent_id}] 已停止")