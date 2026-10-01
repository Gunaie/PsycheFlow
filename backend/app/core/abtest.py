"""A/B 测试模块：prompt 版本分流 + 对话质量评分收集。

设计目标：
- 按用户 ID 哈希分流到不同 prompt 版本（保证同一用户始终看到同一版本）
- 收集用户对回复的评分（1-5 分），按版本聚合
- 与 Prometheus 集成，按版本对比对话质量指标
"""
import hashlib
import time
from typing import Optional

from prometheus_client import Counter, Histogram

# A/B 测试配置
AB_TEST_ENABLED = True
AB_TEST_NAME = "intervention_prompt_v2"  # 当前实验名称
AB_TEST_VARIANTS = ["control", "treatment"]  # 对照组 / 实验组

# Prometheus 指标
AB_FEEDBACK = Counter(
    "ab_feedback_total",
    "A/B 测试用户反馈总数",
    ["experiment", "variant", "score"],  # score: 1-5
)
AB_FEEDBACK_AVG = Histogram(
    "ab_feedback_avg",
    "A/B 测试平均评分分布",
    ["experiment", "variant"],
    buckets=[1.0, 2.0, 3.0, 4.0, 5.0],
)
AB_RESPONSE_TIME = Histogram(
    "ab_response_time_seconds",
    "A/B 测试响应时间分布",
    ["experiment", "variant"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)


def get_variant(user_id: str, experiment: str = AB_TEST_NAME) -> str:
    """按用户 ID 哈希分流到实验组/对照组。

    使用 MD5 哈希保证同一用户始终分配到同一组。
    分流比例 50/50（可扩展为任意比例）。
    """
    if not AB_TEST_ENABLED or not user_id:
        return "control"
    key = f"{experiment}:{user_id}"
    hash_val = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return "treatment" if hash_val % 2 == 0 else "control"


def get_prompt_version(variant: str) -> str:
    """返回当前变体对应的 prompt 版本标识。

    与 prompts.py 中的模板版本对应，用于决策追踪和效果对比。
    """
    return f"{AB_TEST_NAME}:{variant}"


def track_feedback(experiment: str, variant: str, score: int) -> None:
    """埋点：用户反馈评分（1-5 分）。"""
    AB_FEEDBACK.labels(experiment=experiment, variant=variant, score=str(score)).inc()
    AB_FEEDBACK_AVG.labels(experiment=experiment, variant=variant).observe(float(score))


def track_response_time(experiment: str, variant: str, latency: float) -> None:
    """埋点：A/B 测试响应时间。"""
    AB_RESPONSE_TIME.labels(experiment=experiment, variant=variant).observe(latency)


class ABTestContext:
    """A/B 测试上下文：携带实验信息和分流结果。"""

    def __init__(self, user_id: str, experiment: str = AB_TEST_NAME):
        self.experiment = experiment
        self.variant = get_variant(user_id, experiment)
        self.prompt_version = get_prompt_version(self.variant)
        self.start_time = time.monotonic()

    def elapsed(self) -> float:
        """返回实验开始以来的时间（秒）。"""
        return time.monotonic() - self.start_time

    def to_dict(self) -> dict:
        """返回可序列化的上下文信息（用于 decision 记录和 API 响应）。"""
        return {
            "experiment": self.experiment,
            "variant": self.variant,
            "prompt_version": self.prompt_version,
        }
