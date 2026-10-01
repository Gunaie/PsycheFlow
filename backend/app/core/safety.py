"""危机安全模块：硬编码、零 LLM、前置关键词扫描。

对话与报告均先用本模块扫描用户输入，命中即走兜底转介链，不进 LLM，
呼应 scales/base.py「危机升级须硬编码、前置关键词扫描」的设计原则。
"""
import re

from app.core.config import settings

# 自杀/自伤/轻生相关关键词（小写子串匹配）
CRISIS_KEYWORDS = [
    "自杀", "自残", "自伤", "不想活", "想死", "结束生命",
    "割腕", "跳楼", "了结自己", "活不下去", "轻生",
    "活够了", "没意义了", "不想存在",
]

# 简单否定前缀检测（1-2 个字符范围内），用于排除「我不想死」「他没有自杀」等误报
_NEGATION_PREFIX = re.compile(r"(不|没|无|别|勿|没有|不想|不会|不能|不要)[\s]{0,2}")

# 否定词与关键词之间允许插入的修饰字（「一点都不想死」「根本没有轻生」）
_NEGATION_FILLERS = {"一", "点", "都", "也", "很", "太", "真", "完全", "根本", "丝毫"}


def _is_negated(text: str, hit_start: int, hit_end: int) -> bool:
    """检查关键词命中位置前是否紧跟否定词（最多回退 6 个字符，允许常见修饰字插入）。"""
    window_start = max(0, hit_start - 6)
    before = text[window_start:hit_start]
    # 先尝试直接命中否定前缀（考虑窗口边界截断，从最长否定词开始匹配）
    for neg in sorted(["没有", "不想", "不会", "不能", "不要", "不", "没", "无", "别", "勿"], key=len, reverse=True):
        idx = before.rfind(neg)
        if idx != -1:
            # 否定词与关键词之间的内容
            between = before[idx + len(neg):]
            # 否定词必须紧贴关键词（或仅含修饰字/空白），否则不构成否定修饰
            if between and any(c not in _NEGATION_FILLERS and not c.isspace() for c in between):
                continue
            return True
    return False


def detect_crisis_with_words(text: str) -> tuple[bool, list[str]]:
    """扫描文本是否命中危机关键词，返回 (是否命中, 被命中的触发词列表)。

    空串返回 (False, [])。命中的关键词按出现顺序去重收集。
    否定前缀（不/没/无/别/没有/不想 等）包围的关键词不计入命中。
    """
    trigger_words: list[str] = []
    if not text:
        return (False, trigger_words)
    lower = text.lower()
    for kw in CRISIS_KEYWORDS:
        start = 0
        while True:
            idx = lower.find(kw, start)
            if idx == -1:
                break
            if not _is_negated(lower, idx, idx + len(kw)):
                if kw not in trigger_words:
                    trigger_words.append(kw)
            start = idx + len(kw)
    return (len(trigger_words) > 0, trigger_words)


def detect_crisis(text: str) -> bool:
    """扫描文本是否命中危机关键词。空串返回 False。

    向后兼容：内部调用 detect_crisis_with_words，只返回 bool 结果。
    原调用点 `if detect_crisis(...)` 语义不变。
    """
    return detect_crisis_with_words(text)[0]


def crisis_message() -> str:
    """温暖但坚定的兜底话术 + 援助热线，供命中危机时直接返回。"""
    hotline = settings.crisis_hotline_12355
    return (
        "我听到你正在承受很大的痛苦，你的感受很重要，你不是一个人。"
        "作为校园心理陪伴助手，我不能替代专业帮助——"
        "请立即联系你信任的老师、家长，或拨打青少年心理援助热线 "
        f"{hotline}，专业人员会陪着你一起面对。"
    )
