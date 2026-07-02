# providers/types.py
"""
统一内部消息格式，以 Anthropic API 格式为基准。

所有 Provider 适配器的职责：
  - 发送前：把统一格式转成自家 API 格式
  - 接收后：把自家 API 格式转成统一格式

这样上层代码只需要和统一格式打交道。
"""
from typing import Literal, Union
from pydantic import BaseModel


# ── 内容块（Content Block）────────────────────────────────────────────────────
# 一条 LLM 消息的内容由一个或多个内容块组成。

class TextBlock(BaseModel):
    """普通文本块——LLM 说的话，或者用户的问题。"""
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """
    工具调用块——LLM 决定要调用某个工具时产生此块。
    包含工具名和调用参数。第 5 章实现工具后会频繁见到。
    """
    type: Literal["tool_use"] = "tool_use"
    id: str          # 工具调用的唯一 ID（用于匹配 tool_result）
    name: str        # 工具名称（和注册时的名字一致）
    input: dict      # 工具调用参数（JSON 对象）


class ToolResultBlock(BaseModel):
    """
    工具结果块——工具执行完后把结果放进这个块，发回给 LLM。
    LLM 看到这个块，才能基于工具返回的数据给出最终回答。
    """
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str    # 对应哪次 tool_use 调用的 id
    content: str        # 工具的返回内容（字符串）
    is_error: bool = False   # 工具是否执行失败


# 内容块的联合类型
ContentBlock = Union[TextBlock, ToolUseBlock, ToolResultBlock]


# ── 消息 ──────────────────────────────────────────────────────────────────────

class Message(BaseModel):
    """一条对话消息：角色（user/assistant）+ 内容块列表。"""
    role: Literal["user", "assistant"]
    content: list[ContentBlock]


# ── 工具定义 ──────────────────────────────────────────────────────────────────

class ToolDefinition(BaseModel):
    """
    向 LLM 描述一个工具。LLM 靠这个信息决定何时调用哪个工具。
    description 写得越清晰，LLM 调用工具越准确。
    """
    name: str
    description: str
    input_schema: dict   # JSON Schema 格式，描述工具接受什么参数


# ── Token 用量 ────────────────────────────────────────────────────────────────

class Usage(BaseModel):
    """LLM 调用的 Token 消耗统计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0    # Anthropic Prompt Cache 命中（第 8 章详讲）
    cache_write_tokens: int = 0   # Anthropic Prompt Cache 写入


# ── 流式 chunk 类型 ───────────────────────────────────────────────────────────

class TextDelta(BaseModel):
    """流式传输中的文本片段。"""
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolInputDelta(BaseModel):
    """流式传输中的工具参数片段（工具参数可能很长，分段传输）。"""
    type: Literal["tool_input_delta"] = "tool_input_delta"
    tool_id: str
    partial_json: str


class MessageStart(BaseModel):
    """流式响应开始事件（包含初始 usage 信息）。"""
    type: Literal["message_start"] = "message_start"
    usage: Usage


class ToolUseStart(BaseModel):
    """流式传输：LLM 开始生成一个工具调用块。包含工具名和调用 ID。"""
    type: Literal["tool_use_start"] = "tool_use_start"
    tool_id: str
    tool_name: str


class MessageStop(BaseModel):
    """
    流式响应结束事件。

    stop_reason 说明 LLM 为何停止生成：
    - "end_turn"：正常完成
    - "tool_use"：需要调用工具（第 5 章后会见到）
    - "max_tokens"：达到最大 Token 限制（回答被截断）
    """
    type: Literal["message_stop"] = "message_stop"
    stop_reason: str
    usage: Usage


# 流式 chunk 的联合类型
StreamChunk = Union[TextDelta, ToolInputDelta, ToolUseStart, MessageStart, MessageStop]


# ── Provider 完整响应 ─────────────────────────────────────────────────────────

class ProviderResponse(BaseModel):
    """一次非流式调用的完整响应。"""
    content: list[ContentBlock]   # 所有内容块（文本 + 工具调用）
    stop_reason: str
    usage: Usage