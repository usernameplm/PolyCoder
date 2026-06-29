# agent.py（替换原有内容）
from dataclasses import dataclass
from typing import AsyncGenerator
from providers.router import get_provider
from providers.types import Message, TextBlock, Usage


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int


SYSTEM_PROMPT = "你是一个智能助手，请用中文回答问题，回答要简洁准确。"


async def ask(question: str) -> AskResult:
    """非流式调用：等待完整响应。"""
    provider = get_provider()   # 根据 .env 自动选择 Provider

    response = await provider.chat(
        messages=[Message(role="user", content=[TextBlock(text=question)])],
        system=SYSTEM_PROMPT,
    )

    # 从响应内容中提取文本
    text = ""
    for block in response.content:
        if isinstance(block, TextBlock):
            text += block.text

    return AskResult(
        text=text,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


async def ask_stream(question: str) -> AsyncGenerator[str, None]:
    """流式调用：逐 token yield 文本片段。"""
    from providers.types import TextDelta

    provider = get_provider()

    async for chunk in provider.stream(
        messages=[Message(role="user", content=[TextBlock(text=question)])],
        system=SYSTEM_PROMPT,
    ):
        if isinstance(chunk, TextDelta):
            yield chunk.text