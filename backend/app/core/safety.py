"""危机安全模块：硬编码、零 LLM、前置关键词扫描。

对话与报告均先用本模块扫描用户输入，命中即走兜底转介链，不进 LLM，
呼应 scales/base.py「危机升级须硬编码、前置关键词扫描」的设计原则。
"""
from app.core.config import settings

# 自杀/自伤/轻生相关关键词（小写子串匹配）
CRISIS_KEYWORDS = [
    "自杀", "自残", "自伤", "不想活", "想死", "结束生命",
    "割腕", "跳楼", "了结自己", "活不下去", "轻生",
    "活够了", "没意义了", "不想存在",
]


def detect_crisis(text: str) -> bool:
    """扫描文本是否命中危机关键词。空串返回 False。"""
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in CRISIS_KEYWORDS)


def crisis_message() -> str:
    """温暖但坚定的兜底话术 + 援助热线，供命中危机时直接返回。"""
    hotline = settings.crisis_hotline_12355
    return (
        "我听到你正在承受很大的痛苦，你的感受很重要，你不是一个人。"
        "作为校园心理陪伴助手，我不能替代专业帮助——"
        "请立即联系你信任的老师、家长，或拨打青少年心理援助热线 "
        f"{hotline}，专业人员会陪着你一起面对。"
    )
