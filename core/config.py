# core/config.py
# 统一读取所有配置——整个项目只从这里拿配置，修改 .env 立刻生效。

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # LLM Provider 选择
    llm_provider: str = "anthropic"

    # Anthropic
    anthropic_api_key: str = ""

    # OpenAI / 兼容服务
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # 通用
    llm_model: str = "claude-sonnet-4-6"
    app_port: int = 8002

    # 可观测性：OpenTelemetry 链路追踪导出地址（Jaeger/Tempo 等），不填则不导出
    otel_exporter_otlp_endpoint: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# 全局单例——所有模块都 from core.config import settings 来使用
settings = Settings()