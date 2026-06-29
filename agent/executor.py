# agent/executor.py
"""
并行工具执行器。

LLM 在一轮里可能同时决定调用多个工具（比如"查天气"和"查汇率"同时执行）。
ToolExecutor 用 asyncio.gather() 并行执行，比串行快很多。

任意一个工具失败时，返回 is_error=True 的结果，而不是整体崩溃，
这样 LLM 能看到错误信息并决定如何继续（重试、换工具、或告知用户）。
"""
import asyncio
from providers.types import ToolUseBlock, ToolResultBlock


class ToolExecutor:

    def __init__(self, registry):
        """
        registry：工具注册表，根据工具名找到工具实现。
        第 5 章会实现 ToolRegistry，现在暂时传入 None 也能运行（没有工具的情况）。
        """
        self.registry = registry

    async def execute_all(self, tool_calls: list[ToolUseBlock]) -> list[ToolResultBlock]:
        """
        并行执行所有工具调用。

        返回和 tool_calls 顺序一致的 ToolResultBlock 列表。
        即使某个工具失败，也会返回对应位置的错误结果，不跳过。
        """
        if not tool_calls:
            return []

        tasks = [self._execute_one(tc) for tc in tool_calls]
        return list(await asyncio.gather(*tasks))

    async def _execute_one(self, tool_call: ToolUseBlock) -> ToolResultBlock:
        """执行单个工具调用，捕获所有异常，包装成 ToolResultBlock。"""

        if self.registry is None:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content="错误：未配置工具注册表（ToolRegistry）",
                is_error=True,
            )

        tool = self.registry.get(tool_call.name)

        if tool is None:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"错误：工具 '{tool_call.name}' 未注册。已注册的工具：{self.registry.list_names()}",
                is_error=True,
            )

        try:
            result = await tool.execute(tool_call.input)
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=str(result),
                is_error=False,
            )
        except Exception as e:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"工具执行失败（{tool_call.name}）: {type(e).__name__}: {e}",
                is_error=True,
            )