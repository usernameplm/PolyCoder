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
    StreamChunk, TextBlock, ToolUseBlock, ImageBlock, Usage,
    TextDelta, MessageStart, MessageStop, ToolUseStart, ToolInputDelta,
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
    
    @property
    def supports_vision(self) -> bool:
        # Claude 3 及之后的全系列模型（含本项目默认的 claude-sonnet-4-6）原生支持图片输入。
        return True

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
                elif isinstance(block, ImageBlock):          # ← 新增这一段
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": block.media_type,
                            "data": block.data,
                        },
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

    def _system_param(self, system: str):
        """
        构造发给 SDK 的 system 参数。

        给 system prompt 打上 cache_control 标记以启用 Anthropic Prompt Cache
        （第 8.5 节）：同一 system 在 5 分钟内重复调用可命中缓存，费用约降至 10%。
        空 system 直接原样传（空字符串不能包成 text block，否则 API 报错）。
        """
        if not system:
            return system
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
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
            system=self._system_param(system),
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
                cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
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
            system=self._system_param(system),
            messages=self._to_sdk_messages(messages),
        )
        if tools:
            kwargs["tools"] = self._to_sdk_tools(tools)

        async with self._client.messages.stream(**kwargs) as stream:
            # 先发出 MessageStart（包含初始 usage）
            yield MessageStart(usage=Usage())

            # Anthropic 流式事件用 index 标识内容块，delta 事件不带工具 id；
            # 这里维护 index → tool_id 映射，好让工具参数增量关联到正确的工具块。
            index_to_tool_id: dict[int, str] = {}

            async for event in stream:
                if event.type == "content_block_start":
                    if event.content_block.type == "tool_use":
                        index_to_tool_id[event.index] = event.content_block.id
                        yield ToolUseStart(
                            tool_id=event.content_block.id,
                            tool_name=event.content_block.name,
                        )
                elif event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield TextDelta(text=event.delta.text)
                    elif hasattr(event.delta, "partial_json"):
                        yield ToolInputDelta(
                            tool_id=index_to_tool_id.get(event.index, ""),
                            partial_json=event.delta.partial_json,
                        )

            # 流结束后取最终消息（包含完整 usage）
            final = await stream.get_final_message()
            yield MessageStop(
                stop_reason=final.stop_reason or "end_turn",
                usage=Usage(
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                    cache_read_tokens=getattr(final.usage, "cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
                ),
            )