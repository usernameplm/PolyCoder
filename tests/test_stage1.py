"""阶段 1 自动测试"""
import asyncio
import pytest
from agent import ask, AskResult


@pytest.mark.asyncio
async def test_ask_returns_result():
    """基础对话测试：应该返回非空回答"""
    result = await ask("用一句话解释什么是 Python")
    assert isinstance(result, AskResult)
    assert len(result.text) > 10, "回答不应该为空"
    assert result.input_tokens > 0
    assert result.output_tokens > 0


@pytest.mark.asyncio
async def test_ask_chinese_response():
    """应该用中文回答（系统提示词里要求了）"""
    result = await ask("What is Python?")
    # 检查回答包含中文字符
    has_chinese = any('一' <= char <= '鿿' for char in result.text)
    assert has_chinese, "应该用中文回答"