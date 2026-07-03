# swarm/task_types.py
"""
Swarm 模式下所有合法任务类型的唯一定义来源。

之前的问题：task_type 在 API 层（SwarmAskRequest）、白板（Task.type）、
各 SwarmAgent 的 task_types 声明里都是裸字符串，三处互不校验——
DebuggerSwarmAgent 发布了一个 "test_write" 任务，但没有任何 Agent
声明处理这个类型，任务永远卡在 pending，也不会报错。

这里把"合法范围"集中定义一次，API 校验和 Blackboard 运行时校验都引用
同一份定义，新增任务类型时只需要改这一个文件。
"""
from typing import Literal

TASK_TYPE_CODE_REVIEW = "code_review"
TASK_TYPE_DEBUG = "debug"
TASK_TYPE_TEST_WRITE = "test_write"

# 供 Pydantic 请求体做值域校验：传入范围外的字符串会在 API 层直接 422，
# 不会等到进了白板才发现没人处理。
TaskType = Literal["code_review", "debug", "test_write"]

ALL_TASK_TYPES: tuple[str, ...] = (
    TASK_TYPE_CODE_REVIEW,
    TASK_TYPE_DEBUG,
    TASK_TYPE_TEST_WRITE,
)
