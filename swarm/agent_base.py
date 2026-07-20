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
from skills import SKILL_INDEX, get_skill_guide_tool
from agent_core.loop import run_agent_loop
from agent_core.executor import ToolExecutor
from tools.registry import ToolRegistry
from providers.router import get_provider
from observability.logging import logger


class SwarmAgent(ABC):

    def __init__(self, blackboard: Blackboard, agent_id: str | None = None):
        self.blackboard = blackboard
        # 允许子类传固定 agent_id（方便看日志）；不传则自动生成一个，
        # 保证同一类型开多个实例做负载均衡时 ID 不会撞在一起
        self.agent_id = agent_id or f"{type(self).__name__}-{id(self) % 10000}"
        self._running = False
        # 把自己声明的 task_types 登记到白板，供 post() 校验任务类型是否有人处理
        self.blackboard.register_consumer(self.task_types)

    @property
    @abstractmethod
    def task_types(self) -> list[str]:
        """这个 Agent 处理哪些类型的任务。"""
        ...

    @abstractmethod
    async def handle(self, task: Task) -> str:
        """
        处理一个任务，返回结果字符串。

        任务处理失败时抛出异常（SwarmAgent 会自动标记为 failed）。
        """
        ...

    async def _run_with_skills(self, base_system: str, prompt: str,
                               extra_tools: list | None = None) -> str:
        """公共执行入口：system 拼索引表 + tools 含 get_skill_guide，走 Agentic Loop。

        取代旧的 _enhance_system（TF-IDF 预注入）。Swarm 子 Agent 由此获得
        和 Coordinator 一致的"按需加载 Skill"能力——单次 provider.chat() 里 LLM 没有
        "先调工具再继续"的机会，改走 run_agent_loop 才能让 LLM 自主调 get_skill_guide。
        """
        system = f"{base_system}\n\n{SKILL_INDEX}" if SKILL_INDEX else base_system

        registry = ToolRegistry()
        registry.register(get_skill_guide_tool())
        for tool in (extra_tools or []):
            registry.register(tool)

        result = await run_agent_loop(
            prompt=prompt,
            provider=get_provider(),
            system=system,
            tools=registry.get_all_definitions(),
            executor=ToolExecutor(registry),
            max_turns=99,
            session_id=self.agent_id,
            agent_name=type(self).__name__,
        )
        return result.text

    async def start(self):
        """启动 Agent，持续监听白板上的任务。"""
        self._running = True
        logger.info("swarm_agent_started", agent_id=self.agent_id, task_types=self.task_types)

        while self._running:
            claimed_any = False

            for task_type in self.task_types:
                task = await self.blackboard.claim(task_type, self.agent_id)
                if task:
                    claimed_any = True
                    try:
                        result = await self.handle(task)
                        await self.blackboard.complete(task.id, result)
                    except Exception as e:
                        await self.blackboard.fail(task.id, str(e))

            if not claimed_any:
                # 没有任务，等待新任务到来（最多等 5 秒，然后再轮询）
                await self.blackboard.wait_for_task(timeout=5.0)

    def stop(self):
        """停止 Agent。"""
        self._running = False
        logger.info("swarm_agent_stopped", agent_id=self.agent_id)