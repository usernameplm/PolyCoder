# agent/api.py
# 对外暴露的简洁接口：ask() 和 ask_stream()
# cli.py、main.py、测试代码都从这里导入，不直接接触 loop.py

import asyncio
from asyncio import Queue
from dataclasses import dataclass
from typing import AsyncGenerator

from .loop import run_agent_loop, LoopResult
from providers.router import get_provider

SYSTEM_PROMPT = "你是一个智能助手，请用中文回答问题，回答要简洁准确。"


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    turn_count: int = 1


async def ask(question: str) -> AskResult:
    """非流式调用，使用 Agentic Loop。"""
    provider = get_provider()

    result: LoopResult = await run_agent_loop(
        prompt=question,
        provider=provider,
        system=SYSTEM_PROMPT,
        max_turns=10,
    )

    return AskResult(
        text=result.text,
        input_tokens=result.total_usage.input_tokens,
        output_tokens=result.total_usage.output_tokens,
        turn_count=result.turn_count,
    )


async def ask_stream(question: str) -> AsyncGenerator[str, None]:
    """流式调用，通过 on_text_delta 回调实时传出文本。"""
    queue: Queue[str | None] = Queue()

    def on_delta(text: str):
        queue.put_nowait(text)

    async def run_loop():
        provider = get_provider()
        await run_agent_loop(
            prompt=question,
            provider=provider,
            system=SYSTEM_PROMPT,
            max_turns=10,
            on_text_delta=on_delta,
        )
        queue.put_nowait(None)

    loop_task = asyncio.create_task(run_loop())

    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        yield chunk

    await loop_task
