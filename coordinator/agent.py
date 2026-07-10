# coordinator/agent.py
from .planner import make_plan
from .dispatcher import dispatch
from observability.logging import logger


class CoordinatorAgent:
    """主协调 Agent：接收用户请求，规划任务，分发执行，聚合结果。"""

    async def run(self, user_request: str, session_id: str | None = None) -> str:
        log = logger.bind(session_id=session_id) if session_id else logger
        log.info("coordinator_start", request=user_request[:80])

        # 第一步：规划任务
        tasks, direct_reply = await make_plan(user_request, session_id=session_id)

        # 如果不需要子 Agent，直接返回 LLM 的回复
        if direct_reply is not None:
            log.info("coordinator_direct_reply", reply_chars=len(direct_reply))
            return direct_reply

        if not tasks:
            log.warning("coordinator_empty_plan")
            return "无法理解请求，请提供更多信息。"

        log.info("coordinator_plan", task_count=len(tasks),
                 agents=[t.agent for t in tasks],
                 task_ids=[t.id for t in tasks])

        # 第二步：分发执行
        results = await dispatch(tasks, session_id=session_id)

        # 第三步：聚合结果
        return _aggregate(tasks, results)


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