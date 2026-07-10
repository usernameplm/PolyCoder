# tools/registry.py
"""
工具注册表（ToolRegistry）。

统一管理所有工具的注册和查找。
Agentic Loop 里的 ToolExecutor 通过注册表找到工具实现。
"""
from .base import BaseTool
from providers.types import ToolDefinition
from observability.logging import logger


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> "ToolRegistry":
        """注册一个工具。返回 self，支持链式调用。"""
        self._tools[tool.name] = tool
        logger.info("tool_registered", tool_name=tool.name)
        return self

    def get(self, name: str) -> BaseTool | None:
        """根据名称查找工具，找不到返回 None。"""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """返回所有已注册工具的名称列表（用于错误提示）。"""
        return list(self._tools.keys())

    def get_all_definitions(self) -> list[ToolDefinition]:
        """
        返回所有工具的 ToolDefinition 列表。
        发给 LLM 时传入这个列表，LLM 就知道有哪些工具可用。
        """
        return [t.definition for t in self._tools.values()]