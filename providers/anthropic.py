# providers/anthropic.py
"""
Anthropic Provider 适配器。

由于内部格式以 Anthropic 格式为基准，这个适配器的转换逻辑最少。
主要工作：把 Pydantic 模型转成 SDK 所需的字典格式。
"""
import anthropic
from typing import AsyncGenerator
from .base import BaseProvider
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, Usage,
    TextDelta, MessageStart, MessageStop,
)


class AnthropicProvider(BaseProvider):

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,  # 支持代理地址
        )
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def _to_sdk_messages(self, messages: list[Message]) -> list[dict]:
        """
        把内部 Message 对象列表转成 Anthropic SDK 所需的字典格式。

        内部格式和 SDK 格式基本一致，主要差异是 Pydantic 模型要转成 dict。
        """
        result = []
        for msg in messages:
            blocks = []
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                elif hasattr(block, "tool_use_id"):  # ToolResultBlock
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": block.content,
                        "is_error": block.is_error,
                    })
                else:  # TextBlock
                    blocks.append({"type": "text", "text": block.text})
            result.append({"role": msg.role, "content": blocks})
        return result

    def _to_sdk_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """把工具定义转成 Anthropic 格式。"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=self._to_sdk_messages(messages),
        )
        if tools:
            kwargs["tools"] = self._to_sdk_tools(tools)

        resp = await self._client.messages.create(**kwargs)

        # 把 SDK 响应转回内部格式
        content = []
        for block in resp.content:
            if block.type == "text":
                content.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        return ProviderResponse(
            content=content,
            stop_reason=resp.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0),
                cache_write_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0),
            ),
        )

    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=self._to_sdk_messages(messages),
        )
        if tools:
            kwargs["tools"] = self._to_sdk_tools(tools)

        async with self._client.messages.stream(**kwargs) as stream:
            # 先发出 MessageStart（包含初始 usage）
            yield MessageStart(usage=Usage())

            async for event in stream:
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield TextDelta(text=event.delta.text)

            # 流结束后取最终消息（包含完整 usage）
            final = await stream.get_final_message()
            yield MessageStop(
                stop_reason=final.stop_reason or "end_turn",
                usage=Usage(
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                ),
            )