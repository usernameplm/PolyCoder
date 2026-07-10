# observability/metrics.py
"""
Prometheus 指标定义。

Prometheus 是业界标准的监控系统，Grafana 可以展示 Prometheus 数据。
指标数据通过 /metrics 端点暴露，Prometheus 服务器定期来"拉取"。
"""
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST


# ── 计数器（Counter）：只增不减，记录总量 ─────────────────────────────────────

# 总请求数（按 provider、model、status 分类）
agent_requests_total = Counter(
    "agent_requests_total",
    "Agent 接收并处理的请求总数",
    ["provider", "model", "status"],   # label 维度，可以分类过滤
)

# 工具调用总数
tool_calls_total = Counter(
    "tool_calls_total",
    "工具被调用的总次数",
    ["tool_name", "status"],   # status: success / error
)


# ── 直方图（Histogram）：记录延迟分布 ─────────────────────────────────────────

# Agent 请求延迟（从收到请求到返回结果）
agent_latency_seconds = Histogram(
    "agent_latency_seconds",
    "Agent 完成一次请求的端到端延迟（秒）",
    ["provider"],
    # 桶的边界：0.5s、1s、2s、5s、10s、30s、60s
    # 意思是：有多少请求在 0.5s 内完成，有多少在 1s 内……
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)


# ── 计量器（Gauge）：可增可减，记录当前状态 ───────────────────────────────────

# 当前活跃中的请求数
active_requests = Gauge(
    "active_requests",
    "当前正在处理中的请求数",
)


# ── Token 用量（用 Counter 累计）─────────────────────────────────────────────

token_usage_total = Counter(
    "token_usage_total",
    "累计 Token 用量",
    ["type"],   # type: input / output / cache_read
)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def record_request(provider: str, model: str, status: str, latency: float, usage=None):
    """一次 Agent 请求完成时记录所有相关指标。"""
    agent_requests_total.labels(provider=provider, model=model, status=status).inc()
    agent_latency_seconds.labels(provider=provider).observe(latency)

    if usage:
        token_usage_total.labels(type="input").inc(usage.input_tokens)
        token_usage_total.labels(type="output").inc(usage.output_tokens)
        if usage.cache_read_tokens:
            token_usage_total.labels(type="cache_read").inc(usage.cache_read_tokens)
