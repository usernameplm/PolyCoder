# coordinator/agent.py
from .planner import make_plan
from .dispatcher import dispatch


class CoordinatorAgent:
    """主协调 Agent：接收用户请求，规划任务，分发执行，聚合结果。"""

    async def run(self, user_request: str) -> str:
        print(f"[Coordinator] 收到请求：{user_request[:60]}")

        # 第一步：规划任务
        tasks, direct_reply = await make_plan(user_request)

        # 如果不需要子 Agent，直接返回 LLM 的回复
        if direct_reply is not None:
            return direct_reply

        if not tasks:
            return "无法理解请求，请提供更多信息。"

        print(f"[Coordinator] 规划了 {len(tasks)} 个子任务")

        # 第二步：分发执行
        results = await dispatch(tasks)

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