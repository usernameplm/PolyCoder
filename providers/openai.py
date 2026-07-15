# providers/openai.py
"""
OpenAI Provider 适配器。

同时支持所有兼容 OpenAI Chat Completions API 的服务：
  - OpenAI（GPT-4o、o1 等）
  - Ollama（本地模型，如 llama3、qwen2.5）
  - DeepSeek（deepseek-chat、deepseek-reasoner）
  - Azure OpenAI
  - vLLM、LM Studio 等自托管服务

切换方式：只改 .env 里的 OPENAI_BASE_URL 和 OPENAI_MODEL，代码零改动。
"""
import json
from openai import AsyncOpenAI
from typing import AsyncGenerator
from .base import BaseProvider
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, ToolResultBlock,
    Usage, TextDelta, MessageStart, MessageStop,
)
from observability.logging import logger


class OpenAIProvider(BaseProvider):

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def _to_openai_messages(self, messages: list[Message], system: str) -> list[dict]:
        """
        把内部消息格式转成 OpenAI 格式。

        关键转换：
        1. system 放在最前面，作为独立消息（OpenAI 不支持顶层 system 参数）
        2. ToolResultBlock 需要转成 role="tool" 的独立消息
        3. ToolUseBlock 需要放到 assistant 消息的 tool_calls 字段
        """
        result = []

        if system:
            result.append({"role": "system", "content": system})

        for msg in messages:
            # 分析这条消息里有什么
            text_content = ""
            tool_calls = []
            tool_results = []

            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_content = block.text
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append({
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input, ensure_ascii=False),
                        },
                    })
                elif isinstance(block, ToolResultBlock):
                    tool_results.append(block)

            if tool_results:
                # 工具结果：每个结果一条独立的 role="tool" 消息
                for tr in tool_results:
                    result.append({
                        "role": "tool",
                        "tool_call_id": tr.tool_use_id,
                        "content": tr.content,
                    })
            elif tool_calls:
                # assistant 消息包含工具调用
                m = {"role": "assistant", "content": text_content or None, "tool_calls": tool_calls}
                result.append(m)
            else:
                result.append({"role": msg.role, "content": text_content})

        return result

    def _to_openai_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def _parse_stop_reason(self, finish_reason: str | None) -> str:
        # OpenAI 的 "tool_calls" 对应内部的 "tool_use"
        if finish_reason == "tool_calls":
            return "tool_use"
        return finish_reason or "end_turn"

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
            messages=self._to_openai_messages(messages, system),
        )
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)

        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]

        content: list = []
        if choice.message.content:
            content.append(TextBlock(text=choice.message.content))
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    parsed = json.loads(tc.function.arguments)
                except json.JSONDecodeError as e:
                    logger.warning("openai_tool_args_parse_failed",
                                   tool_name=tc.function.name,
                                   tool_id=tc.id,
                                   error=str(e),
                                   raw_args=tc.function.arguments[:200])
                    parsed = {"_parse_error": str(e), "_raw_args": tc.function.arguments}
                content.append(ToolUseBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=parsed,
                ))

        if resp.usage is None:
            logger.warning("openai_chat_no_usage", model=self._model,
                           has_choices=bool(resp.choices),
                           resp_keys=list(resp.model_dump().keys()) if hasattr(resp, "model_dump") else "n/a")

        return ProviderResponse(
            content=content,
            stop_reason=self._parse_stop_reason(choice.finish_reason),
            usage=Usage(
                input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                output_tokens=resp.usage.completion_tokens if resp.usage else 0,
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
            messages=self._to_openai_messages(messages, system),
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)

        yield MessageStart(usage=Usage())

        final_usage = Usage()

        async for chunk in await self._client.chat.completions.create(**kwargs):
            if not chunk.choices:
                if chunk.usage:
                    final_usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                    )
                continue

            choice = chunk.choices[0]
            if choice.delta.content:
                yield TextDelta(text=choice.delta.content)

        if final_usage.input_tokens == 0 and final_usage.output_tokens == 0:
            logger.warning("openai_stream_no_usage", model=self._model)

        yield MessageStop(stop_reason="end_turn", usage=final_usage)