# providers/gemini.py
"""
Gemini Provider 适配器。

通过 google-generativeai SDK 接入 Google Gemini 系列模型。

Gemini API 和 Anthropic/OpenAI 的主要格式差异：
  1. 消息格式：role 只有 "user" / "model"（没有 "assistant"）
  2. 消息结构：content 用 Part 列表，不用 Block
  3. system prompt：单独传入 system_instruction，不放在消息历史里
  4. 工具调用：function_declarations 格式，和 OpenAI tool_calls 不同
  5. 流式：generate_content_async(stream=True) + async for chunk
"""
import uuid
from typing import AsyncGenerator
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from .base import BaseProvider
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, ToolResultBlock,
    Usage, TextDelta, MessageStart, MessageStop,
)


class GeminiProvider(BaseProvider):

    def __init__(self, api_key: str, model: str):
        genai.configure(api_key=api_key)
        self._model_name = model

    @property
    def model_name(self) -> str:
        return self._model_name

    def _to_gemini_messages(self, messages: list[Message]) -> list[dict]:
        """
        把内部消息格式转成 Gemini 格式。

        关键差异：
        - Gemini 的 role 是 "user" / "model"，没有 "assistant"
        - 工具调用：assistant 消息里的 ToolUseBlock → role="model" + function_call part
        - 工具结果：user 消息里的 ToolResultBlock → role="user" + function_response part
        """
        result = []
        for msg in messages:
            # Gemini 的 assistant 叫 "model"
            role = "model" if msg.role == "assistant" else "user"
            parts = []

            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append({"text": block.text})
                elif isinstance(block, ToolUseBlock):
                    parts.append({
                        "function_call": {
                            "name": block.name,
                            "args": block.input,
                        }
                    })
                elif isinstance(block, ToolResultBlock):
                    parts.append({
                        "function_response": {
                            "name": block.tool_use_id,
                            "response": {
                                "result": block.content,
                                "is_error": block.is_error,
                            },
                        }
                    })

            if parts:
                result.append({"role": role, "parts": parts})

        return result

    def _to_gemini_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """把工具定义转成 Gemini function_declarations 格式。"""
        return [{
            "function_declarations": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                }
                for t in tools
            ]
        }]

    def _build_model(self, system: str, tools: list[ToolDefinition] | None):
        """创建带有 system_instruction 和工具的模型实例。"""
        kwargs = {"model_name": self._model_name}
        if system:
            kwargs["system_instruction"] = system
        if tools:
            kwargs["tools"] = self._to_gemini_tools(tools)
        return genai.GenerativeModel(**kwargs)

    def _parse_response(self, response) -> ProviderResponse:
        """把 Gemini 响应转成内部 ProviderResponse 格式。"""
        content = []
        stop_reason = "end_turn"

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    content.append(TextBlock(text=part.text))
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    content.append(ToolUseBlock(
                        id=str(uuid.uuid4()),
                        name=fc.name,
                        input=dict(fc.args),
                    ))
                    stop_reason = "tool_use"

        usage = Usage()
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = Usage(
                input_tokens=response.usage_metadata.prompt_token_count or 0,
                output_tokens=response.usage_metadata.candidates_token_count or 0,
            )

        return ProviderResponse(
            content=content,
            stop_reason=stop_reason,
            usage=usage,
        )

    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:

        model = self._build_model(system, tools)
        gemini_messages = self._to_gemini_messages(messages)

        response = await model.generate_content_async(
            gemini_messages,
            generation_config=GenerationConfig(max_output_tokens=max_tokens),
        )

        return self._parse_response(response)

    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:

        model = self._build_model(system, tools)
        gemini_messages = self._to_gemini_messages(messages)

        yield MessageStart(usage=Usage())

        response = await model.generate_content_async(
            gemini_messages,
            generation_config=GenerationConfig(max_output_tokens=max_tokens),
            stream=True,
        )

        input_tokens = 0
        output_tokens = 0

        async for chunk in response:
            if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                input_tokens = chunk.usage_metadata.prompt_token_count or 0
                output_tokens = chunk.usage_metadata.candidates_token_count or 0

            if chunk.candidates:
                for part in chunk.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        yield TextDelta(text=part.text)

        yield MessageStop(
            stop_reason="end_turn",
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )
