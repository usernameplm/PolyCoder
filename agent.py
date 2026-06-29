"""
agent.py — 阶段 1：最小 Agent（直接调 OpenAI API）

这是整个项目最简单的核心：接收问题，调用 LLM，返回回答。
没有工具、没有记忆、没有框架——就是最纯粹的"问 → 答"。
"""

from openai import AsyncOpenAI
from dataclasses import dataclass
from core.config import settings


@dataclass
class AskResult:
    """封装一次对话的结果，包括回答文字和 Token 用量。"""
    text: str           # Agent 的回答文字
    input_tokens: int   # 本次消耗的输入 Token
    output_tokens: int  # 本次消耗的输出 Token


async def ask(question: str) -> AskResult:
    """
    向 LLM 提问，返回回答和 Token 用量。

    参数：
        question - 用户的问题（字符串）

    返回：
        AskResult 对象，包含 text、input_tokens、output_tokens
    """
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )

    response = await client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": "你是一个智能助手，请用中文回答问题，回答要简洁准确。"},
            {"role": "user", "content": question},
        ],
    )

    return AskResult(
        text=response.choices[0].message.content,
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
    )