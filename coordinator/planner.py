# coordinator/planner.py
"""
任务规划器：调用 LLM，把用户请求拆分成结构化任务计划（JSON）。
"""
import json
import re
from providers.router import get_provider
from providers.types import Message, TextBlock
from dataclasses import dataclass, field


@dataclass
class TaskSpec:
    """单个任务的规格。"""
    id: str
    agent: str           # 对应哪个子 Agent 的 name
    input: str           # 任务描述（发给子 Agent 的 prompt）
    depends_on: list[str] = field(default_factory=list)


# Coordinator 的系统提示词（★ 替换点：修改可用子 Agent 列表）
_COORDINATOR_SYSTEM = """
你是一个 Coding Agent 的任务协调者。接收用户的代码相关请求，拆分为子任务并以 JSON 格式返回计划。

可用的子 Agent：
- code_writer：代码生成专家，根据需求编写新代码，生成后会自动验证
- code_reviewer：代码审查专家，检查安全漏洞、逻辑错误、代码质量
- debugger：调试专家，复现并修复 Bug，会运行代码验证修复效果
- test_writer：测试专家，生成 pytest 单元测试，覆盖正常/边界/异常场景

任务拆分原则：
- 代码审查 + 修复 + 写测试 → 三个串行任务（review → debug → test_writer）
- 多个独立文件的审查 → 并行任务
- 简单的代码生成请求 → 单个 code_writer 任务

输出格式（严格遵守，不要有其他文字）：
{
  "tasks": [
    {
      "id": "t1",
      "agent": "agent名称（四选一）",
      "input": "给该 agent 的具体任务描述（需包含文件名、函数名等上下文）",
      "depends_on": []
    }
  ]
}

规则：
- depends_on 为 [] 表示可与其他无依赖任务并行执行
- depends_on 为 ["t1"] 表示需要 t1 完成后才能执行（t1 的结果会自动注入）
- 只输出 JSON，不要有任何其他文字
- 如果请求不需要任何子 Agent（如只是聊天），返回 {"tasks": [], "reply": "直接回复的内容"}
"""


async def make_plan(user_request: str) -> tuple[list[TaskSpec], str | None]:
    """
    调用 LLM 生成任务计划。

    返回：
        (TaskSpec 列表, 直接回复文字 or None)
        - 如果有任务：(tasks, None)
        - 如果不需要子 Agent：([], 直接回复文字)
    """
    provider = get_provider()

    response = await provider.chat(
        messages=[Message(role="user", content=[TextBlock(text=user_request)])],
        system=_COORDINATOR_SYSTEM,
        max_tokens=2048,
    )

    raw_text = ""
    for block in response.content:
        if isinstance(block, TextBlock):
            raw_text += block.text

    return _parse_plan(raw_text)


def _parse_plan(text: str) -> tuple[list[TaskSpec], str | None]:
    """解析 LLM 输出的 JSON 任务计划。"""
    # 去掉可能的 Markdown 代码块标记
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()

    # 找到 JSON 边界
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        # 找不到 JSON，把整段文字当作直接回复
        return [], text.strip()

    try:
        plan = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"[Planner] JSON 解析失败：{e}\n原始文本：{text[:300]}")
        return [], text.strip()

    tasks_raw = plan.get("tasks", [])
    direct_reply = plan.get("reply")

    if not tasks_raw:
        return [], direct_reply or text.strip()

    specs = [
        TaskSpec(
            id=t.get("id", f"t{i}"),
            agent=t.get("agent", ""),
            input=t.get("input", ""),
            depends_on=t.get("depends_on", []),
        )
        for i, t in enumerate(tasks_raw)
    ]

    return specs, None