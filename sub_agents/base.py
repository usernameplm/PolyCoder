# sub_agents/base.py
"""
SubAgent 基类。

所有专家子 Agent 都继承这个类，只需要实现 system_prompt 和 run() 方法。
run() 方法接收任务描述（字符串），返回处理结果（字符串）。
"""
from abc import ABC, abstractmethod
from agent_core.loop import run_agent_loop
from agent_core.executor import ToolExecutor
from providers.router import get_provider
from providers.types import Usage
from tools.registry import ToolRegistry
from skills.enhancer import enhance_system_prompt
from observability.logging import logger


class SubAgent(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """子 Agent 的名称标识（和任务计划里的 agent 字段一致）。"""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """
        子 Agent 的系统提示词（工作说明书）。

        写作要点：
        1. 明确角色：你是谁，专注什么领域
        2. 明确职责：你能做什么，不能做什么
        3. 输出格式：你的结果应该是什么格式
        """
        ...

    @property
    def tools(self):
        """子 Agent 可用的工具列表（默认无工具，子类按需覆盖）。"""
        return []

    async def run(self, task: str, context: dict | None = None, session_id: str | None = None) -> tuple[str, Usage]:
        """
        执行任务。

        参数：
            task    - 任务描述（自然语言）
            context - 前置任务的结果（由 dispatcher 注入）
            session_id - 会话标识（用于日志追踪）

        返回：
            (任务结果文字, 本次调用的 Token 用量) —— Coordinator 需要汇总各子 Agent
            的用量才能给 /ask 返回完整的 usage 统计（第 10 章记忆功能引入的需求）。
        """
        log = logger.bind(agent=self.name, session_id=session_id) if session_id else logger.bind(agent=self.name)
        log.info("sub_agent_start", task=task[:80])

        # 把上下文注入到任务描述里
        full_task = task
        if context:
            context_str = "\n".join(f"【{k}的结果】\n{v}" for k, v in context.items())
            full_task = f"{task}\n\n参考信息（来自前置任务）：\n{context_str}"

        # 用任务描述搜索相关 Skill，动态增强 system prompt
        system = enhance_system_prompt(
            base_system=self.system_prompt,
            context=full_task[:300],
            agent_name=self.name,
        )

        provider = get_provider()

        # 使用 Agentic Loop 执行任务
        registry = ToolRegistry()
        for tool in self.tools:
            registry.register(tool)

        executor = ToolExecutor(registry) if self.tools else ToolExecutor(None)

        result = await run_agent_loop(
            prompt=full_task,
            provider=provider,
            system=system,
            tools=registry.get_all_definitions() if self.tools else None,
            executor=executor,
            max_turns=10,
            session_id=session_id,
        )

        log.info("sub_agent_done", result_chars=len(result.text))
        return result.text, result.total_usage