# agent/context.py
"""
上下文管理：Token 预算检查 + 历史压缩。
"""
from providers.types import Message, TextBlock

# Claude 的上下文窗口（不同模型不同，claude-sonnet-4 是 200k）
MAX_CONTEXT_TOKENS = 180_000   # 留 20k 余量
COMPRESS_THRESHOLD = 0.8       # 达到 80% 时触发压缩（180k * 0.8 = 144k）


def estimate_tokens(messages: list[Message]) -> int:
    """
    估算消息列表的 Token 数。

    精确计算需要 tokenizer，这里用简单的字符数估算：
    - 中文：约 1 字 = 1.5 Token
    - 英文：约 4 字符 = 1 Token

    这是粗估，误差约 20%，足够用于触发压缩的判断。
    """
    total_chars = 0
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextBlock):
                total_chars += len(block.text)
            else:
                total_chars += 100  # 工具调用块的估计大小

    # 简单估算：平均每字符约 0.8 Token
    return int(total_chars * 0.8)


def should_compress(messages: list[Message]) -> bool:
    """判断是否需要压缩历史。"""
    estimated = estimate_tokens(messages)
    threshold = MAX_CONTEXT_TOKENS * COMPRESS_THRESHOLD
    if estimated > threshold:
        print(f"[Context] 估算 {estimated} tokens，超过阈值 {threshold}，触发压缩")
        return True
    return False


async def compress_messages(
    messages: list[Message],
    provider,
    keep_recent_turns: int = 8,
) -> list[Message]:
    """
    压缩对话历史，保留最近 N 轮 + 对早期历史的摘要。

    压缩策略：
    1. 把最近 keep_recent_turns 轮（每轮 = 1 user + 1 assistant）保留完整内容
    2. 对更早的历史，调用 LLM 生成摘要（一段简洁的总结）
    3. 返回：[摘要消息] + [最近 N 轮]

    为什么不直接截断？
    截断会丢失对话的前因后果，Agent 会忘记之前的重要决定。
    摘要保留了关键信息，即使不是一字不差的原文。
    """
    # 每轮 = 2 条消息（user + assistant），保留最近 N 轮
    keep_count = keep_recent_turns * 2

    if len(messages) <= keep_count:
        return messages   # 历史不够长，不需要压缩

    old_messages = messages[:-keep_count]
    recent_messages = messages[-keep_count:]

    # 构建摘要提示词
    history_text = ""
    for msg in old_messages:
        role_label = "用户" if msg.role == "user" else "助手"
        for block in msg.content:
            if isinstance(block, TextBlock):
                history_text += f"[{role_label}]: {block.text[:500]}\n"

    summary_prompt = f"""
请对以下历史对话进行简洁的摘要，保留所有重要决策、数据、用户需求和问题。
摘要应当让读者理解对话的主要脉络，不需要完整重现每一句话。

历史对话：
{history_text}

请输出摘要：
"""

    summary_response = await provider.chat(
        messages=[Message(role="user", content=[TextBlock(text=summary_prompt)])],
        system="你是专业的对话摘要助手，请提炼对话要点。",
        max_tokens=800,
    )

    summary_text = ""
    for block in summary_response.content:
        if isinstance(block, TextBlock):
            summary_text += block.text

    # 把摘要作为第一条"用户消息"注入（对话必须以 user 开头）
    summary_message = Message(
        role="user",
        content=[TextBlock(text=f"【对话历史摘要】\n{summary_text}")]
    )

    print(f"[Context] 压缩完成：{len(messages)} 条 → 1条摘要 + {len(recent_messages)} 条")
    return [summary_message] + recent_messages