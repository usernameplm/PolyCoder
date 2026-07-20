# agent/loop.py
"""
Agentic Loop 核心实现。

这是整个项目最关键的文件，实现了：
  1. while True 主循环（驱动 Agent 持续工作）
  2. 工具调用检测 → 并行执行 → 结果回填
  3. 多种终止条件（无工具调用 / 超出轮次限制 / 外部中断）
  4. 流式和非流式两种模式

与 claude-agent-sdk 的黑箱不同，这里每一行都是你能读、改、调试的代码。
"""
from dataclasses import dataclass
from typing import Callable
from providers.base import BaseProvider
from providers.types import (
    Message, ToolDefinition, TextBlock, ToolUseBlock,
    ContentBlock, MessageStop, TextDelta, Usage,
)
from .state import LoopState
from .executor import ToolExecutor
from .context import should_compress, compress_messages
from observability.logging import logger
from observability.tracing import tracer


# ── 终止原因常量 ───────────────────────────────────────────────────────────────

STOP_COMPLETED = "completed"   # 正常完成（LLM 不再需要工具）
STOP_MAX_TURNS = "max_turns"   # 达到最大轮次
STOP_ABORTED   = "aborted"     # 被外部中断（未来扩展用）


@dataclass
class LoopResult:
    """Agentic Loop 的最终结果。"""
    text: str           # LLM 的最终回答文字
    total_usage: Usage  # 全部轮次的累计 Token 用量
    turn_count: int     # 实际执行的轮次数
    stop_reason: str    # 终止原因（completed / max_turns / aborted）


async def run_agent_loop(
    prompt: str,
    provider: BaseProvider,
    system: str = "",
    tools: list[ToolDefinition] | None = None,
    executor: ToolExecutor | None = None,
    max_turns: int = 10,
    max_tokens: int = 4096,
    on_text_delta: Callable[[str], None] | None = None,
    initial_messages: list[Message] | None = None,
    session_id: str | None = None,
    agent_name: str | None = None,
) -> LoopResult:
    """
    Agentic Loop 主函数。

    参数说明：
        prompt         用户的问题或任务描述
        provider       LLM Provider（第 3 章实现的抽象层）
        system         系统提示词（给 LLM 的工作说明）
        tools          LLM 可以使用的工具列表（空列表表示无工具）
        executor       工具执行器（有工具时必须传入）
        max_turns      最多循环几轮（防止无限循环，消耗过多 Token）
        max_tokens     每轮最大输出 Token
        on_text_delta  流式文本回调（传入则启用流式模式）
        initial_messages  已有的历史消息（含本轮新问题），传入则跳过用 prompt
                          单独构造首条消息——用于带会话记忆的多轮对话场景
        session_id    会话标识，传入后所有日志自动带上 session_id 字段
        agent_name    调用方的 Agent 名字（如 code_reviewer/debugger），传入后
                      作为 span 名字，让 Tempo trace 树能区分是哪个子 Agent，
                      不传则退回通用的 "agent_loop"

    返回：
        LoopResult 包含最终文字、用量统计、轮次数、终止原因
    """
    log = logger.bind(session_id=session_id) if session_id else logger
    # 初始化状态：优先用调用方传入的完整历史；没有则退回单条用户消息
    starting_messages = initial_messages if initial_messages is not None else [
        Message(role="user", content=[TextBlock(text=prompt)])
    ]
    state = LoopState(messages=tuple(starting_messages))

    # ── 主循环 ─────────────────────────────────────────────────────────────────
    with tracer.start_as_current_span(agent_name or "agent_loop") as loop_span:
        loop_span.set_attribute("session_id", session_id or "")
        loop_span.set_attribute("provider", provider.model_name)
        loop_span.set_attribute("agent.name", agent_name or "")

        while True:

            # [检查] 上下文压缩：Token 超限时自动压缩早期历史
            current_messages = list(state.messages)
            if should_compress(current_messages):
                current_messages = await compress_messages(current_messages, provider)
                state = LoopState(
                    messages=tuple(current_messages),
                    turn_count=state.turn_count,
                    total_usage=state.total_usage,
                    last_transition="compressed",
                )

            # [检查] 轮次限制
            if state.turn_count >= max_turns:
                log.warning(
                    "agent_loop_max_turns",
                    turn=state.turn_count,
                    max_turns=max_turns,
                    reason=STOP_MAX_TURNS,
                )
                return LoopResult(
                    text=f"（已达最大轮次限制 {max_turns}，任务可能未完成）",
                    total_usage=state.total_usage,
                    turn_count=state.turn_count,
                    stop_reason=STOP_MAX_TURNS,
                )

            # [执行] 调用 LLM，收集本轮输出
            text_chunks: list[str] = []
            tool_calls: list[ToolUseBlock] = []
            turn_usage = Usage()

            with tracer.start_as_current_span(f"turn_{state.turn_count}") as turn_span:
                if on_text_delta:
                    # 流式模式：边生成边通过回调传出文本
                    pending_tool_inputs: dict[str, str] = {}   # tool_id → 累积的 JSON 字符串
                    pending_tool_names: dict[str, str] = {}    # tool_id → tool_name

                    async for chunk in provider.stream(
                        messages=list(state.messages),
                        system=system,
                        tools=tools or None,
                        max_tokens=max_tokens,
                    ):
                        from providers.types import ToolUseStart, ToolInputDelta

                        if isinstance(chunk, TextDelta):
                            text_chunks.append(chunk.text)
                            on_text_delta(chunk.text)

                        elif isinstance(chunk, ToolUseStart):
                            pending_tool_names[chunk.tool_id] = chunk.tool_name

                        elif isinstance(chunk, ToolInputDelta):
                            pending_tool_inputs.setdefault(chunk.tool_id, "")
                            pending_tool_inputs[chunk.tool_id] += chunk.partial_json

                        elif isinstance(chunk, MessageStop):
                            turn_usage = chunk.usage

                    # 流结束后，从累积的 JSON 重建 ToolUseBlock
                    import json
                    for tool_id, json_str in pending_tool_inputs.items():
                        try:
                            tool_calls.append(ToolUseBlock(
                                id=tool_id,
                                name=pending_tool_names.get(tool_id, "unknown"),
                                input=json.loads(json_str),
                            ))
                        except json.JSONDecodeError:
                            pass

                else:
                    # 非流式模式：等待完整响应
                    response = await provider.chat(
                        messages=list(state.messages),
                        system=system,
                        tools=tools or None,
                        max_tokens=max_tokens,
                    )

                    for block in response.content:
                        if isinstance(block, TextBlock):
                            text_chunks.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            tool_calls.append(block)

                    turn_usage = response.usage

                turn_span.set_attribute("input_tokens", turn_usage.input_tokens)
                turn_span.set_attribute("output_tokens", turn_usage.output_tokens)
                turn_span.set_attribute("tool_calls", [tc.name for tc in tool_calls])

            # 累积 Token 用量
            state = state.next_turn("processing", turn_usage)

            # [判断] 是否有工具调用
            if not tool_calls:
                # 没有工具调用 → LLM 给出了最终答案，Loop 结束
                log.info(
                    "agent_loop_complete",
                    turn=state.turn_count,
                    total_input_tokens=state.total_usage.input_tokens,
                    total_output_tokens=state.total_usage.output_tokens,
                    stop_reason=STOP_COMPLETED,
                )
                return LoopResult(
                    text="".join(text_chunks),
                    total_usage=state.total_usage,
                    turn_count=state.turn_count,
                    stop_reason=STOP_COMPLETED,
                )

            # [执行] 并行执行所有工具
            if executor is None:
                log.error("agent_loop_no_executor", turn=state.turn_count)
                return LoopResult(
                    text="（错误：Agent 决定使用工具，但未配置 ToolExecutor）",
                    total_usage=state.total_usage,
                    turn_count=state.turn_count,
                    stop_reason=STOP_ABORTED,
                )

            log.info(
                "agent_loop_turn",
                turn=state.turn_count,
                provider=provider.model_name,
                input_tokens=turn_usage.input_tokens,
                output_tokens=turn_usage.output_tokens,
                tool_calls=[tc.name for tc in tool_calls],
                stop_reason="tool_use",
            )
            # 工具并行执行（ToolExecutor 内部用 asyncio.gather），
            # 每个工具调用的 Span 在 executor._execute_one 里创建，见该文件
            tool_results = await executor.execute_all(tool_calls)

            # [构建] 下一轮的消息历史
            # 协议要求：
            #   1. 把本轮的 assistant 回复（包含 ToolUseBlock）加入历史
            #   2. 把工具结果（ToolResultBlock）作为 user 消息加入历史
            new_messages = list(state.messages)

            # assistant 消息：本轮文字 + 工具调用决策
            assistant_content: list[ContentBlock] = []
            if text_chunks:
                assistant_content.append(TextBlock(text="".join(text_chunks)))
            assistant_content.extend(tool_calls)
            new_messages.append(Message(role="assistant", content=assistant_content))

            # user 消息：工具执行结果
            new_messages.append(Message(role="user", content=tool_results))

            # [更新] 状态，进入下一轮
            state = LoopState(
                messages=tuple(new_messages),
                turn_count=state.turn_count,
                total_usage=state.total_usage,
                last_transition="tool_use",
            )
            # → 继续 while True