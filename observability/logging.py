# observability/logging.py
"""
结构化日志配置。

为什么用 structlog 而不是普通 print？
- print：人读的，机器处理难（日志聚合系统无法解析）
- structlog：输出 JSON 格式，每个字段有固定 key，
  可以用 Grafana Loki、ELK、腾讯云日志服务等工具
  精确过滤：session_id=xxx 的所有日志、provider=openai 且 status=error 的日志
"""
import structlog
import logging
from opentelemetry import trace


def _inject_trace_id(_, __, event_dict):
    """从当前 OpenTelemetry Span 提取 trace_id 和 span_id，注入每条日志。"""
    span = trace.get_current_span()
    if span is not None:
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def setup_logging(log_level: str = "INFO"):
    """初始化结构化日志，在服务启动时调用一次。"""

    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            _inject_trace_id,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),   # 输出 JSON
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


# 全局 logger（各模块 from observability.logging import logger）
logger = structlog.get_logger()
