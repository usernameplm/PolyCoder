# observability/tracing.py
"""
OpenTelemetry 链路追踪。

链路追踪回答的问题：
  "用户的这个请求，经过了哪些 Agent？每个 Agent 耗时多少？哪里最慢？"

每个操作（一次 Agent 调用、一次工具调用）创建一个 Span（跨度）。
多个 Span 串成一条链路（Trace），在 Jaeger 或 Tempo 中可视化展示。
"""
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource


def setup_tracing(service_name: str = "my-agent", otlp_endpoint: str | None = None):
    """
    初始化链路追踪，在服务启动时调用一次。

    otlp_endpoint：OTLP 导出地址（Jaeger、Tempo 等）
    如果不传，链路数据不导出（但代码里的 tracer 调用不报错）。
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            print(f"[Tracing] 链路追踪已启动，导出到：{otlp_endpoint}")
        except ImportError:
            print("[Tracing] 未安装 opentelemetry-exporter-otlp，链路数据不导出")

    trace.set_tracer_provider(provider)


# 全局 tracer（各模块使用）
tracer = trace.get_tracer("my-agent")
