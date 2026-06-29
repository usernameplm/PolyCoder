# providers/router.py
"""
根据 .env 配置自动选择并返回 Provider 实例。
全局单例：第一次调用时初始化，之后复用同一个实例。
"""
from functools import lru_cache
from .base import BaseProvider
from core.config import settings


@lru_cache(maxsize=1)
def get_provider() -> BaseProvider:
    """
    获取 Provider 单例。

    lru_cache 确保这个函数只执行一次（第一次调用时创建实例，
    之后直接返回缓存的实例，不重复创建客户端）。
    """
    name = settings.llm_provider.lower()

    if name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
        )

    elif name in ("openai", "ollama", "deepseek", "azure"):
        from .openai import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url or None,
        )

    elif name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )

    else:
        raise ValueError(
            f"不支持的 Provider: '{name}'。"
            f"可选值：anthropic / openai / gemini。"
            f"检查 .env 里的 LLM_PROVIDER 配置。"
        )


def clear_provider_cache():
    """清除缓存（测试时切换 Provider 用）。"""
    get_provider.cache_clear()