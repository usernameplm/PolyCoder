# swarm/reviewer_agent.py
"""
Swarm 模式下的代码审查 Agent。

持续运行，从 Blackboard 认领 code_review 任务，
执行完后把结果写回 Blackboard，并可自动发布后续任务（如需要修复则发 debug 任务）。
"""
import asyncio
from .blackboard import Blackboard
from .agent_base import SwarmAgent
from .task_types import TASK_TYPE_CODE_REVIEW, TASK_TYPE_DEBUG
from observability.logging import logger


class ReviewerSwarmAgent(SwarmAgent):

    def __init__(self, blackboard: Blackboard, agent_id: str = "reviewer-1"):
        super().__init__(blackboard, agent_id)

    @property
    def task_types(self) -> list[str]:
        """声明这个 Agent 能处理哪些类型的任务。"""
        return [TASK_TYPE_CODE_REVIEW]

    async def handle(self, task) -> str:
        """
        处理一个 code_review 任务。

        task.payload 格式：
        {
            "code": "要审查的代码字符串",
            "file": "文件名（可选，仅用于显示）",
            "focus": "重点关注的方面（可选，如 'security'）"
        }
        """
        payload = task.payload
        code = payload.get("code", "")
        filename = payload.get("file", "unknown.py")
        focus = payload.get("focus", "全面审查")

        logger.info("reviewer_agent_started_review", agent_id=self.agent_id, file=filename)

        # 调用 LLM 执行审查
        from providers.router import get_provider
        from providers.types import Message, TextBlock

        provider = get_provider()

        base_system = """
你是一名资深代码审查工程师。
审查维度：SQL 注入、命令注入、硬编码密码、逻辑错误、边界条件、性能问题。
每个问题输出：[Critical/Warning/Suggestion] 行号 - 问题描述 - 建议修复。
发现 Critical 级别问题时，最后一行输出 NEEDS_FIX:true，否则输出 NEEDS_FIX:false。
"""
        prompt = f"文件：{filename}\n重点关注：{focus}\n\n```python\n{code}\n```"
        system = self._enhance_system(base_system, prompt[:300])

        response = await provider.chat(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=system,
        )

        result = ""
        for block in response.content:
            if hasattr(block, "text"):
                result += block.text

        # 如果发现严重问题，自动发布 debug 任务（这就是 Swarm 的涌现式行为）
        if "NEEDS_FIX:true" in result:
            debug_task_id = await self.blackboard.post_derived(
                parent_task_id=task.id,
                task_type=TASK_TYPE_DEBUG,
                payload={
                    "code": code,
                    "file": filename,
                    "review_result": result,
                    "origin_task": task.id,
                }
            )
            logger.info(
                "reviewer_agent_critical_found",
                agent_id=self.agent_id,
                debug_task_id=debug_task_id,
            )

        return result