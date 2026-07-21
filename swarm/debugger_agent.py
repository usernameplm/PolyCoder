# swarm/debugger_agent.py
"""
Swarm 模式下的调试修复 Agent。
认领 ReviewerAgent 自动发布的 debug 任务。
"""
from .blackboard import Blackboard
from .agent_base import SwarmAgent
from .task_types import TASK_TYPE_DEBUG, TASK_TYPE_TEST_WRITE
from observability.logging import logger


class DebuggerSwarmAgent(SwarmAgent):

    def __init__(self, blackboard: Blackboard, agent_id: str = "debugger-1"):
        super().__init__(blackboard, agent_id)

    @property
    def task_types(self) -> list[str]:
        return [TASK_TYPE_DEBUG]

    async def handle(self, task) -> str:
        payload = task.payload
        code = payload.get("code", "")
        filename = payload.get("file", "")
        review_result = payload.get("review_result", "")

        base_system = """
你是一名调试工程师，根据代码审查结果修复 Bug。
输出格式：
1. Bug 根因（一句话）
2. 修复后的完整代码（```python 代码块）
3. 修复说明（改了什么）
"""
        prompt = (
            f"文件：{filename}\n\n"
            f"审查结果：\n{review_result}\n\n"
            f"原始代码：\n```python\n{code}\n```\n\n"
            "请根据审查结果修复所有 Critical 级别的问题。"
        )
        # 走 Agentic Loop：修复时 LLM 可按需 get_skill_guide 加载错误处理/异步等团队规范。
        result = await self._run_with_skills(base_system, prompt)

        # 修复完成后，自动发布 test_write 任务
        test_write_task_id = await self.blackboard.post_derived(
            parent_task_id=task.id,
            task_type=TASK_TYPE_TEST_WRITE,
            payload={
                "code": result,
                "file": filename,
                "origin_task": task.id,
            }
        )
        logger.info(
            "debugger_agent_fix_completed",
            agent_id=self.agent_id,
            test_write_task_id=test_write_task_id,
        )
        return result