# swarm/test_writer_agent.py
"""
Swarm 模式下的测试生成 Agent。
认领 DebuggerSwarmAgent 自动发布的 test_write 任务，链路收尾：
review → debug → test_write。
"""
from .blackboard import Blackboard
from .agent_base import SwarmAgent
from .task_types import TASK_TYPE_TEST_WRITE
from observability.logging import logger


class TestWriterSwarmAgent(SwarmAgent):

    def __init__(self, blackboard: Blackboard, agent_id: str = "test-writer-1"):
        super().__init__(blackboard, agent_id)

    @property
    def task_types(self) -> list[str]:
        return [TASK_TYPE_TEST_WRITE]

    async def handle(self, task) -> str:
        """
        处理一个 test_write 任务。

        task.payload 格式（由 DebuggerSwarmAgent 发布，见 debugger_agent.py）：
        {
            "code": "修复后的代码（DebuggerSwarmAgent 的完整输出，含说明文字）",
            "file": "文件名",
            "origin_task": "最初 code_review 任务的 id（仅用于追溯，不参与生成逻辑）"
        }
        """
        payload = task.payload
        code = payload.get("code", "")
        filename = payload.get("file", "unknown.py")

        logger.info("test_writer_agent_started", agent_id=self.agent_id, file=filename)

        from providers.router import get_provider
        from providers.types import Message, TextBlock

        provider = get_provider()

        base_system = """
你是一名测试工程师，专注于编写高质量的 pytest 单元测试。

测试覆盖原则：
1. 正常路径（happy path）
2. 边界条件：空值、最大值、最小值、空列表等
3. 异常路径：非法参数、外部依赖失败时的行为
4. 安全场景（如涉及用户输入）：SQL 注入、XSS 尝试

输出格式：
```python
# test_xxx.py
import pytest
# ... 完整测试代码
```
测试覆盖说明：列出覆盖了哪些场景
"""
        prompt = (
            f"文件：{filename}\n\n"
            f"以下是修复后的代码（可能夹杂修复说明文字，重点关注其中的代码块）：\n"
            f"{code}\n\n"
            "请为这段代码编写 pytest 单元测试。"
        )
        # 用完整 prompt 作为 Skill 搜索上下文，不截断——触发 Skill 的关键词
        # （如 asyncio、subprocess）可能在代码靠后位置，截断会漏掉该加载的团队规范。
        system = self._enhance_system(base_system, prompt)

        response = await provider.chat(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=system,
        )

        result = ""
        for block in response.content:
            if hasattr(block, "text"):
                result += block.text

        logger.info("test_writer_agent_completed", agent_id=self.agent_id)
        return result
