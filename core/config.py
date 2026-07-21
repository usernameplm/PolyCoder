# core/config.py
# 统一读取所有配置——整个项目只从这里拿配置，修改 .env 立刻生效。

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # LLM Provider 选择
    llm_provider: str = "anthropic"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""

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

    # 飞书机器人（第 12 章）
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    # 通用
    llm_model: str = "claude-sonnet-4-6"
    app_port: int = 8002

    # 工作目录：所有工具（读/写/搜索/执行）和子 Agent 的文件操作都限定在此目录内，
    # 且禁止路径穿越到目录外。启动时用环境变量 WORKSPACE 覆盖，默认当前目录。
    workspace: str = "."

    # 可观测性：OpenTelemetry 链路追踪导出地址（Jaeger/Tempo 等），不填则不导出
    otel_exporter_otlp_endpoint: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    # 视觉调用专用的 Provider/模型（可选）。
    # 留空时回退到对话用的 llm_provider/对应模型，见 providers/router.py 的 get_vision_provider()。
    vision_provider: str = ""
    vision_model: str = ""
    # 当模型名不在 OpenAIProvider 的启发式列表里，但实际支持视觉时，用这个显式声明。
    vision_capable: bool = False

# 全局单例——所有模块都 from core.config import settings 来使用
settings = Settings()