"""量表计分引擎基类与数据结构。

设计原则：
- 计分纯规则化，绝不进 LLM，确保确定性与可审计
- 统一输出 ScoreResult，含总分 / 等级 / 危机 / 因子
- 危机升级（如自杀意念）由量表自带的单项规则触发，不依赖总分
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    """严重度等级（5 级，各量表按需取用）。"""
    NONE = "none"                            # 无
    MILD = "mild"                            # 轻度
    MODERATE = "moderate"                    # 中度
    MODERATELY_SEVERE = "moderately_severe"  # 中重度
    SEVERE = "severe"                        # 重度


class CrisisLevel(str, Enum):
    """危机等级。危机升级须硬编码、零 LLM，前置关键词扫描。"""
    SAFE = "safe"
    ELEVATED = "elevated"  # 触发单项升级 → 直达转介链（学校心理老师/家长/12355）


@dataclass
class ScoreResult:
    scale_id: str
    scale_name: str
    total_score: int
    severity: Severity
    crisis_level: CrisisLevel = CrisisLevel.SAFE
    crisis_triggers: list = field(default_factory=list)  # 触发危机的题项描述
    factor_scores: dict = field(default_factory=dict)     # 因子分
    interpretation: str = ""

    @property
    def needs_crisis_escalation(self) -> bool:
        return self.crisis_level == CrisisLevel.ELEVATED

    def to_dict(self) -> dict:
        """转 JSON 安全 dict：枚举取 .value，供 API 直接返回。"""
        return {
            "scale_id": self.scale_id,
            "scale_name": self.scale_name,
            "total_score": self.total_score,
            "severity": self.severity.value,
            "crisis_level": self.crisis_level.value,
            "crisis_triggers": self.crisis_triggers,
            "factor_scores": self.factor_scores,
            "interpretation": self.interpretation,
            "needs_crisis_escalation": self.needs_crisis_escalation,
        }


class Scale(ABC):
    """量表基类。子类定义题项与计分规则。"""
    scale_id: str = ""
    scale_name: str = ""
    description: str = ""
    items: list = []       # 子类覆盖为题项列表
    options: dict = {}     # 子类覆盖为 {分值: 文本}

    def meta(self) -> dict:
        """自描述元数据，供前端渲染量表表单。"""
        return {
            "scale_id": self.scale_id,
            "scale_name": self.scale_name,
            "description": self.description,
            "items": self.items,
            "options": self.options,
        }

    @abstractmethod
    def score(self, answers: dict) -> ScoreResult:
        """按 题号 -> 答案值 计分。

        缺题、题号越界、分值非法均须抛 ValueError，绝不静默补 0，
        避免掩盖漏答带来的低估风险。
        """
