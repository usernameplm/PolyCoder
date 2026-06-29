# agent/state.py
"""
Agentic Loop 的状态对象。

设计原则：不可变（frozen=True）。
每次迭代不修改已有状态，而是创建一个包含更新后数据的新对象。
这样可以保留完整的状态历史，便于调试和审计。
"""
from dataclasses import dataclass, field, replace
from providers.types import Message, Usage


@dataclass(frozen=True)   # frozen=True：创建后不允许修改任何字段
class LoopState:
    """
    Loop 的完整状态快照。

    每次迭代开始和结束时，Loop 都有一个对应的 LoopState。
    调试时可以打印每一轮的状态，观察 Loop 的行为。
    """
    messages: tuple          # 消息历史（用 tuple 而非 list 保证不可变）
    turn_count: int = 0      # 已完成的轮次数
    total_usage: Usage = field(default_factory=Usage)  # 累计 Token 用量
    last_transition: str = "initial"   # 上次是为何继续（调试用）

    def with_messages(self, new_messages: list[Message]) -> "LoopState":
        """返回一个 messages 更新了的新状态（其他字段不变）。"""
        return replace(self, messages=tuple(new_messages))

    def next_turn(self, transition: str, additional_usage: Usage | None = None) -> "LoopState":
        """返回一个轮次 +1 的新状态。"""
        new_usage = self.total_usage
        if additional_usage:
            new_usage = Usage(
                input_tokens=self.total_usage.input_tokens + additional_usage.input_tokens,
                output_tokens=self.total_usage.output_tokens + additional_usage.output_tokens,
                cache_read_tokens=self.total_usage.cache_read_tokens + additional_usage.cache_read_tokens,
            )
        return replace(
            self,
            turn_count=self.turn_count + 1,
            total_usage=new_usage,
            last_transition=transition,
        )