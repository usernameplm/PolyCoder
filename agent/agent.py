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


async def ask(question: str, session_id: str | None = None) -> AskResult:
    """
    调用 Agent 回答问题。

    session_id 为 None（默认）时无记忆，每次调用互不影响；
    传入 session_id 时会从 SessionStore 加载历史（最近 10 轮）、
    拼到本次问题前面一起跑，并把这轮问答追加写回 SessionStore（第 10 章）。
    """
    provider = get_provider()

    initial_messages = None
    if session_id is not None:
        history = await _store.load_messages(session_id, max_turns=10)
        new_user_message = Message(role="user", content=[TextBlock(text=question)])
        initial_messages = history + [new_user_message]
        await _store.append_message(session_id, "user", question)

    result = await run_agent_loop(
        prompt=question,
        provider=provider,
        system=SYSTEM_PROMPT,
        tools=_registry.get_all_definitions(),
        executor=_executor,
        max_turns=10,
        initial_messages=initial_messages,
    )

    if session_id is not None:
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
