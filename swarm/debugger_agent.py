# swarm/debugger_agent.py
"""
Swarm 模式下的调试修复 Agent。
认领 ReviewerAgent 自动发布的 debug 任务。
"""
from .blackboard import Blackboard
from .agent_base import SwarmAgent
from .task_types import TASK_TYPE_DEBUG, TASK_TYPE_TEST_WRITE


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

        from providers.router import get_provider
        from providers.types import Message, TextBlock

        provider = get_provider()

        system = """
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

        response = await provider.chat(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=system,
        )

        result = ""
        for block in response.content:
            if hasattr(block, "text"):
                result += block.text

        # 修复完成后，自动发布 test_write 任务
        await self.blackboard.post(
            task_type=TASK_TYPE_TEST_WRITE,
            payload={
                "code": result,
                "file": filename,
                "origin_task": task.id,
            }
        )
        print(f"[{self.agent_id}] 修复完成，已发布 test_write 任务")
        return result