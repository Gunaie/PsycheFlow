"""Prometheus 指标定义与收集中间件。

暴露 /metrics 端点供 Prometheus 抓取，覆盖：
- HTTP 请求计数/延迟/错误率
- 对话业务指标（轮次/危机率/干预来源）
- RAG 检索质量（hit@1/recall@3/零命中/距离分布）
- LLM 调用（成功/兜底/延迟）
"""
import time

from prometheus_client import Counter, Histogram, Info, generate_latest, CONTENT_TYPE_LATEST

# ========== 系统信息 ==========
APP_INFO = Info("psycheflow_app", "应用信息")

# ========== HTTP 请求（中间件自动收集）==========
REQUEST_COUNT = Counter(
    "http_requests_total",
    "HTTP 请求总数",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP 请求延迟分布",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0],
)

# ========== 对话业务 ==========
CHAT_TURNS = Counter(
    "chat_turns_total",
    "对话轮次总数",
    ["role", "intent", "crisis"],
)
CHAT_TOKEN = Histogram(
    "chat_token_latency_seconds",
    "对话首 token 延迟（SSE 流式）",
    ["model", "role"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# ========== RAG 检索 ==========
RAG_SEARCH = Counter(
    "rag_search_total",
    "RAG 检索总数",
    ["caller", "intent", "result"],
)
RAG_DISTANCE = Histogram(
    "rag_top1_distance",
    "RAG top1 片段距离分布",
    ["caller"],
    buckets=[0.3, 0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 2.0],
)
RAG_ZERO_HITS = Counter(
    "rag_zero_hits_total",
    "RAG 零命中次数（被过滤或检索为空）",
    ["caller", "reason"],
)

# ========== LLM ==========
LLM_CALLS = Counter(
    "llm_calls_total",
    "LLM 调用总数",
    ["role", "model", "status"],  # status: success / fallback / error
)
LLM_LATENCY = Histogram(
    "llm_latency_seconds",
    "LLM 端到端延迟",
    ["role", "model"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

# ========== 危机安全 ==========
CRISIS_HITS = Counter(
    "crisis_hits_total",
    "危机命中总数",
    ["source"],  # source: triage / assessment / intervention
)


def metrics_response() -> tuple[bytes, str]:
    """返回 Prometheus 格式的指标响应。"""
    return generate_latest(), CONTENT_TYPE_LATEST


def init_app_info(version: str = "0.1.0", python_version: str = "") -> None:
    """初始化应用信息指标。"""
    APP_INFO.info({
        "version": version,
        "python_version": python_version,
    })


class PrometheusMiddleware:
    """ASGI 中间件：自动收集 HTTP 请求计数与延迟。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")

        # 跳过 /metrics 自身，避免自引用
        if path == "/metrics":
            await self.app(scope, receive, send)
            return

        start = time.time()
        status_code = 200  # 默认成功，异常时由 send_wrapper 覆盖

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.time() - start
            REQUEST_COUNT.labels(method=method, endpoint=path, status=status_code).inc()
            REQUEST_LATENCY.labels(method=method, endpoint=path).observe(duration)


def track_chat_turn(role: str, intent: str, crisis: bool) -> None:
    """埋点：对话轮次。"""
    CHAT_TURNS.labels(role=role, intent=intent, crisis=str(crisis).lower()).inc()


def track_chat_first_token(model: str, role: str, latency: float) -> None:
    """埋点：流式对话首 token 延迟。"""
    CHAT_TOKEN.labels(model=model, role=role).observe(latency)


def track_rag_search(caller: str, intent: str, result_count: int, top1_distance: float | None = None) -> None:
    """埋点：RAG 检索结果。"""
    result = "hit" if result_count > 0 else "miss"
    RAG_SEARCH.labels(caller=caller, intent=intent, result=result).inc()
    if top1_distance is not None:
        RAG_DISTANCE.labels(caller=caller).observe(top1_distance)
    if result_count == 0:
        RAG_ZERO_HITS.labels(caller=caller, reason="filtered_or_empty").inc()


def track_llm_call(role: str, model: str, status: str, latency: float) -> None:
    """埋点：LLM 调用（成功/兜底/失败）。"""
    LLM_CALLS.labels(role=role, model=model, status=status).inc()
    LLM_LATENCY.labels(role=role, model=model).observe(latency)


def track_crisis(source: str) -> None:
    """埋点：危机命中。"""
    CRISIS_HITS.labels(source=source).inc()
