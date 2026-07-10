# coordinator/dispatcher.py
"""
任务分发器：根据任务计划，按拓扑顺序执行子任务（并行/串行）。
"""
import asyncio
from collections import defaultdict, deque
from .planner import TaskSpec
from observability.logging import logger


def _get_sub_agents() -> dict:
    """
    返回"agent名称 → SubAgent实例"的映射。
    ★ 替换点：添加新子 Agent 时在这里注册。
    """
    from sub_agents.code_writer import CodeWriterAgent
    from sub_agents.code_reviewer import CodeReviewerAgent
    from sub_agents.debugger import DebuggerAgent
    from sub_agents.test_writer import TestWriterAgent
    return {
        "code_writer":   CodeWriterAgent(),
        "code_reviewer": CodeReviewerAgent(),
        "debugger":      DebuggerAgent(),
        "test_writer":   TestWriterAgent(),
    }


_agents = None


def _get_agent(name: str):
    global _agents
    if _agents is None:
        _agents = _get_sub_agents()
    return _agents.get(name)


async def dispatch(tasks: list[TaskSpec], session_id: str | None = None) -> dict[str, str]:
    """
    按拓扑顺序执行所有任务，返回 {task_id: 结果文字} 的映射。

    算法（拓扑排序 + 波次并行）：
    1. 找出所有没有依赖的任务（入度为 0）→ 波次 1
    2. 并行执行当前波次
    3. 更新依赖计数，找出新解锁的任务 → 波次 2
    4. 重复直到所有任务完成
    """
    log = logger.bind(session_id=session_id) if session_id else logger
    if not tasks:
        return {}

    log.info("dispatch_start", task_count=len(tasks))

    # 建立索引
    spec_by_id = {t.id: t for t in tasks}
    results: dict[str, str] = {}
    errors: dict[str, str] = {}

    # 依赖计数（入度）
    in_degree = {t.id: len(t.depends_on) for t in tasks}

    # 反向依赖：dependents["t1"] = ["t2", "t3"] 表示 t2、t3 依赖 t1
    dependents: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        for dep in t.depends_on:
            dependents[dep].append(t.id)

    # 初始就绪队列（入度为 0 的任务）
    ready = deque(t.id for t in tasks if not t.depends_on)

    while ready:
        # 取出当前所有就绪任务（这一批并行执行）
        wave = list(ready)
        ready.clear()

        # 分类：可执行 vs 被阻断（前置任务失败）
        runnable = []
        for tid in wave:
            spec = spec_by_id[tid]
            failed_dep = next(
                (dep for dep in spec.depends_on if dep in errors),
                None,
            )
            if failed_dep:
                errors[tid] = f"前置任务 '{failed_dep}' 失败，跳过本任务"
                log.warning("dispatch_task_skipped", task_id=tid, reason=errors[tid])
            else:
                runnable.append(spec)

        # 并行执行这一波次的所有任务
        if runnable:
            coros = [_run_one(spec, results, log) for spec in runnable]
            done = await asyncio.gather(*coros, return_exceptions=True)

            for spec, outcome in zip(runnable, done):
                if isinstance(outcome, Exception):
                    errors[spec.id] = str(outcome)
                    log.error("dispatch_task_error", task_id=spec.id, agent=spec.agent, error=str(outcome))
                else:
                    results[spec.id] = outcome

        # 更新依赖计数，解锁下一波次
        for tid in wave:
            for child_id in dependents[tid]:
                if child_id in results or child_id in errors:
                    continue
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    ready.append(child_id)

    return results


async def _run_one(spec: TaskSpec, prior_results: dict[str, str], log=None) -> str:
    """执行单个任务，把前置任务的结果注入 context。"""
    if log is None:
        log = logger
    agent = _get_agent(spec.agent)
    if agent is None:
        raise ValueError(f"未知子 Agent：'{spec.agent}'。已注册：{list(_get_sub_agents().keys())}")

    # 把前置任务的结果作为 context 传入
    context = {dep: prior_results[dep] for dep in spec.depends_on if dep in prior_results}

    log.info("dispatch_task_start", agent=spec.agent, task_id=spec.id)
    result = await agent.run(task=spec.input, context=context or None)
    log.info("dispatch_task_done", agent=spec.agent, task_id=spec.id, result_chars=len(result))
    return result