"""量表计分引擎：纯规则化、确定性、可审计。"""
from app.scales.base import Scale, ScoreResult, Severity, CrisisLevel
from app.scales.registry import get_scale, list_scales

__all__ = ["Scale", "ScoreResult", "Severity", "CrisisLevel", "get_scale", "list_scales"]
