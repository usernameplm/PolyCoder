# providers/router.py
"""
根据 .env 配置选择并返回 Provider 实例。

对话用的 Provider 和图片理解用的 Provider 是分开路由的：
  get_provider()        → 对话/工具调用，看 LLM_PROVIDER
  get_vision_provider()  → 图片描述，看 VISION_PROVIDER（留空则回退到 LLM_PROVIDER）
两者可以指向完全不同的后端，互不影响。
"""
from functools import lru_cache
from .base import BaseProvider
from core.config import settings


def _default_model(name: str) -> str:
    """某个 Provider 名字在没有单独指定模型时，用哪个默认模型。"""
    return {
        "anthropic": settings.llm_model,
        "openai": settings.openai_model,
        "ollama": settings.openai_model,
        "deepseek": settings.openai_model,
        "azure": settings.openai_model,
        "gemini": settings.gemini_model,
    }.get(name, settings.llm_model)


@lru_cache(maxsize=8)
def _build_provider(name: str, model: str) -> BaseProvider:
    """
    按 (Provider 名, 模型名) 创建并缓存一个 Provider 实例。

    用 (name, model) 联合做缓存 key，是因为对话 Provider 和视觉 Provider
    可能是同一个适配器（如都用 openai）但配了不同模型，不能共用一个实例。
    """
    if name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=model,
            base_url=settings.anthropic_base_url or None,
        )

    elif name in ("openai", "ollama", "deepseek", "azure"):
        from .openai import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=model,
            base_url=settings.openai_base_url or None,
            supports_vision=settings.vision_capable or None,
        )

    elif name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(api_key=settings.gemini_api_key, model=model)

    else:
        raise ValueError(
            f"不支持的 Provider: '{name}'。"
            f"可选值：anthropic / openai / gemini。"
            f"检查 .env 里的 LLM_PROVIDER 配置。"
        )


def get_provider() -> BaseProvider:
    """获取对话用的 Provider（看 LLM_PROVIDER）。"""
    name = settings.llm_provider.lower()
    return _build_provider(name, _default_model(name))


def get_vision_provider() -> BaseProvider:
    """
    获取图片理解用的 Provider。

    - 配置了 VISION_PROVIDER：用它（可以和对话 Provider 完全不同）。
    - 没配置：回退到对话 Provider（get_provider()）。
    - 选中的 Provider 必须 supports_vision=True，否则直接报错——
      宁可在调用前就失败，也不要让图片被适配器悄悄丢弃却拿到一段无意义的描述。
    """
    name = (settings.vision_provider or settings.llm_provider).lower()
    model = settings.vision_model or _default_model(name)
    provider = _build_provider(name, model)

    if not provider.supports_vision:
        raise ValueError(
            f"用于视觉调用的 Provider '{name}'（模型 '{model}'）不支持图片输入。\n"
            f"请设置 VISION_PROVIDER / VISION_MODEL 指向一个支持视觉的模型"
            f"（如 anthropic + claude-sonnet-4-6，或 openai + gpt-4o）；"
            f"如果确认该模型其实支持视觉，设置 VISION_CAPABLE=true。"
        )
    return provider


def clear_provider_cache() -> None:
    """测试/切换配置后清空缓存，让下次调用按新配置重新创建实例。"""
    _build_provider.cache_clear()
