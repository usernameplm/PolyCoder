# providers/base.py
"""
BaseProvider：所有 Provider 适配器必须实现的接口契约。

上层代码（Agentic Loop）只和这个接口交互，
不关心底层是哪家 Provider，不关心 API 格式差异。
"""
from abc import ABC, abstractmethod
from typing import AsyncGenerator
from .types import Message, ToolDefinition, StreamChunk, ProviderResponse


class BaseProvider(ABC):

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        """
        非流式调用：等待 LLM 生成完整响应再返回。

        参数：
            messages  - 对话历史（user/assistant 交替）
            system    - 系统提示词（给 LLM 的工作说明）
            tools     - 可用工具列表（None 或空列表表示不用工具）
            max_tokens - 最大生成 Token 数
        """
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        流式调用：边生成边 yield StreamChunk。
        调用方用 async for chunk in provider.stream(...) 处理。
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """当前 Provider 使用的模型名称（用于日志和监控）。"""
        ...

    @property
    def supports_tool_use(self) -> bool:
        """
        是否支持工具调用。
        不支持的 Provider（如某些本地模型）覆盖此属性返回 False。
        默认返回 True（主流 Provider 都支持）。
        """
        return True