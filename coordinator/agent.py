# coordinator/agent.py
import asyncio
from asyncio import Queue
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from .planner import make_plan
from .dispatcher import dispatch, _sum_usage
from observability.logging import logger
from observability.tracing import tracer
from persistence.session_store import SessionStore
from persistence.redis_client import create_redis_client
from providers.types import Message, Usage

# 会话存储：JSONL 落盘 + Redis 缓存最近历史（第 10 章）。与 agent/agent.py 用的是
# 同一套 SessionStore/redis_client 配置，两条架构各自维护自己的 SessionStore 实例，
# 但读写的是同一份基于 session_id 的历史，互不冲突。
_store = SessionStore(base_dir="sessions/", redis_client=create_redis_client())


async def clear_session(session_id: str):
    """清除一个会话的历史（文件 + Redis 缓存），供 FastAPI /session/clear 调用。"""
    await _store.clear(session_id)


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    turn_count: int = 1


class CoordinatorAgent:
    """主协调 Agent：接收用户请求，规划任务，分发执行，聚合结果。"""

    async def run(self, user_request: str, session_id: str | None = None) -> AskResult:
        # 根 span：把整个请求（规划 + 分发给所有子 agent）串成一条 trace，
        # 否则每个子 agent 的 agent_loop span 因为没有激活的父上下文，各自变成新的根 trace。
        with tracer.start_as_current_span("ask_request", attributes={"session_id": session_id or ""}):
            log = logger.bind(session_id=session_id) if session_id else logger
            log.info("coordinator_start", request=user_request[:80])

            history: list[Message] | None = None
            if session_id is not None:
                history = await _store.load_messages(session_id, max_turns=10)
                await _store.append_message(session_id, "user", user_request)

            # 第一步：规划任务（把历史传进去，让规划器理解上下文中的指代）
            tasks, direct_reply, plan_usage = await make_plan(
                user_request, session_id=session_id, history=history,
            )
            usages = [plan_usage]

            if direct_reply is not None:
                log.info("coordinator_direct_reply", reply_chars=len(direct_reply))
                text = direct_reply
            elif not tasks:
                log.warning("coordinator_empty_plan")
                text = "无法理解请求，请提供更多信息。"
            else:
                log.info("coordinator_plan", task_count=len(tasks),
                         agents=[t.agent for t in tasks],
                         task_ids=[t.id for t in tasks])

                # 第二步：分发执行
                results, dispatch_usage = await dispatch(tasks, session_id=session_id)
                usages.append(dispatch_usage)

                # 第三步：聚合结果
                text = _aggregate(tasks, results)

            if session_id is not None:
                await _store.append_message(session_id, "assistant", text)

            total = _sum_usage(usages)
            return AskResult(text=text, input_tokens=total.input_tokens, output_tokens=total.output_tokens)

    async def ask_stream(self, user_request: str, session_id: str | None = None) -> AsyncGenerator[str, None]:
        """
        流式版本的 run()：不需要子 Agent 时整块推送回复；需要子 Agent 时按任务
        完成顺序逐个推送，不等全部任务聚合完。session_id 用法跟 run() 一致。

        推送粒度是"子任务完成"，不是逐 token——规划阶段要拿到完整 JSON 才能解析出
        任务列表，没法边生成边流。
        """
        log = logger.bind(session_id=session_id) if session_id else logger
        log.info("coordinator_stream_start", request=user_request[:80])

        with tracer.start_as_current_span("ask_stream_request", attributes={"session_id": session_id or ""}):
            history: list[Message] | None = None
            if session_id is not None:
                history = await _store.load_messages(session_id, max_turns=10)
                await _store.append_message(session_id, "user", user_request)

            tasks, direct_reply, _plan_usage = await make_plan(
                user_request, session_id=session_id, history=history,
            )

            queue: Queue[str | None] = Queue()
            collected: list[str] = []

            def emit(text: str):
                collected.append(text)
                queue.put_nowait(text)

            async def run_loop():
                if direct_reply is not None:
                    emit(direct_reply)
                elif tasks:
                    def on_task_done(spec, text):
                        emit(f"**[{spec.agent}]**\n{text}\n\n---\n\n")

                    await dispatch(tasks, session_id=session_id, on_task_done=on_task_done)
                else:
                    emit("无法理解请求，请提供更多信息。")

                if session_id is not None:
                    await _store.append_message(session_id, "assistant", "".join(collected))

                queue.put_nowait(None)

            loop_task = asyncio.create_task(run_loop())

            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk

            await loop_task


def _aggregate(tasks, results: dict[str, str]) -> str:
    """把多个子任务结果拼合成完整回复。"""
    if len(results) == 1:
        # 只有一个任务，直接返回
        return next(iter(results.values()))

    parts = []
    for task in tasks:
        if task.id in results:
            parts.append(f"**[{task.agent}]**\n{results[task.id]}")

    return "\n\n---\n\n".join(parts)