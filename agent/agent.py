# agent/agent.py
import asyncio
from asyncio import Queue
from dataclasses import dataclass
from typing import AsyncGenerator
from agent.loop import run_agent_loop
from agent.executor import ToolExecutor
from providers.router import get_provider
from providers.types import Message, TextBlock
from tools.registry import ToolRegistry
from tools.builtin.read_file import ReadFileTool
from tools.builtin.run_python import RunPythonTool
from tools.builtin.search_code import SearchCodeTool
from tools.builtin.list_dir import ListDirTool
from tools.builtin.write_file import WriteFileTool
from persistence.session_store import SessionStore
from swarm.redis_client import create_redis_client

SYSTEM_PROMPT = """
你是一个专业的 Coding Agent，帮助用户完成代码相关任务。

你有以下工具：
- read_file：读取代码文件内容
- search_code：在代码库中搜索函数名、类名或关键词
- run_python：执行 Python 代码片段并返回结果
- list_dir：列出目录结构，了解项目文件布局
- write_file：将代码写入文件

工作原则：
1. 先用工具了解现有代码，再给出建议，不要凭空猜测
2. 发现问题时给出具体的文件名和行号
3. 生成代码后主动用 run_python 验证能否正常执行
4. 输出代码时使用代码块格式（```python）
"""


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    turn_count: int = 1


# 全局工具注册（启动时初始化一次）
_registry = ToolRegistry()
_registry.register(ReadFileTool(workspace="."))
_registry.register(RunPythonTool())
_registry.register(SearchCodeTool(workspace="."))
_registry.register(ListDirTool())
_registry.register(WriteFileTool())
_executor = ToolExecutor(_registry)

# 会话存储（第 10 章）：JSONL 落盘 + Redis 缓存最近历史
_store = SessionStore(base_dir="sessions/", redis_client=create_redis_client())


async def ask(question: str) -> AskResult:
    provider = get_provider()

    result = await run_agent_loop(
        prompt=question,
        provider=provider,
        system=SYSTEM_PROMPT,
        tools=_registry.get_all_definitions(),
        executor=_executor,
        max_turns=10,
    )

    return AskResult(
        text=result.text,
        input_tokens=result.total_usage.input_tokens,
        output_tokens=result.total_usage.output_tokens,
        turn_count=result.turn_count,
    )


async def ask_with_memory(question: str, session_id: str = "default") -> AskResult:
    """
    带多轮记忆的 Agent 调用（第 10 章）。

    流程：
    1. 从 SessionStore 加载历史对话（最近 10 轮）
    2. 把新问题接到历史末尾，构成完整消息列表
    3. 把完整消息列表传给 Agentic Loop（不再单独传 prompt 让它从零构造）
    4. 把这轮的问答追加写回 SessionStore
    """
    provider = get_provider()

    history = await _store.load_messages(session_id, max_turns=10)
    new_user_message = Message(role="user", content=[TextBlock(text=question)])
    all_messages = history + [new_user_message]

    await _store.append_message(session_id, "user", question)

    result = await run_agent_loop(
        prompt=question,
        provider=provider,
        system=SYSTEM_PROMPT,
        tools=_registry.get_all_definitions(),
        executor=_executor,
        max_turns=10,
        initial_messages=all_messages,
    )

    await _store.append_message(session_id, "assistant", result.text)

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
            tools=_registry.get_all_definitions(),
            executor=_executor,
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
