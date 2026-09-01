"""PHQ-A（PHQ-9 青少年版）抑郁筛查量表。

适用：11-17 岁青少年，过去 2 周。
计分（与 PHQ-9 一致）：
  - 9 题，每题 0-3（0 完全没有 / 1 有几天 / 2 一半以上时间 / 3 几乎每天）
  - 总分 0-27
  - 等级：0-4 无 / 5-9 轻度 / 10-14 中度 / 15-19 中重度 / 20-27 重度
安全规则：第 9 题（自杀意念/自伤想法）任一 >0 即触发危机升级，不看总分。
"""
from app.scales.base import Scale, ScoreResult, Severity, CrisisLevel

PHQ_A_OPTIONS = {
    0: "完全没有",
    1: "有几天",
    2: "一半以上时间",
    3: "几乎每天",
}

# 青少年版标准 9 题（中文）
PHQ_A_ITEMS = [
    {"id": 1, "text": "做事时提不起劲或没有兴趣"},
    {"id": 2, "text": "感到心情低落、沮丧或绝望"},
    {"id": 3, "text": "入睡困难、睡不安稳，或睡眠过多"},
    {"id": 4, "text": "感到疲倦或没有活力"},
    {"id": 5, "text": "食欲不振或吃太多"},
    {"id": 6, "text": "觉得自己很糟糕——或觉得自己很失败，或让自己/家人失望"},
    {"id": 7, "text": "对事物专注有困难，如做作业或看电视时"},
    {"id": 8, "text": "动作或说话速度缓慢到别人已察觉；或正好相反——烦躁或坐立不安地动来动去"},
    {"id": 9, "text": "有不如死掉或用某种方式伤害自己的想法", "suicide": True},
]

SUICIDE_ITEM_ID = 9
MIN_SCORE = 0
MAX_SCORE = 3

# (上界, 等级)
SEVERITY_BANDS = [
    (4, Severity.NONE),
    (9, Severity.MILD),
    (14, Severity.MODERATE),
    (19, Severity.MODERATELY_SEVERE),
    (27, Severity.SEVERE),
]

SEVERITY_INTERPRETATION = {
    Severity.NONE: "目前无明显抑郁症状。",
    Severity.MILD: "存在轻度抑郁症状，建议关注与随访。",
    Severity.MODERATE: "存在中度抑郁症状，建议进一步评估。",
    Severity.MODERATELY_SEVERE: "存在中重度抑郁症状，建议尽快由学校心理老师/专业人员评估。",
    Severity.SEVERE: "存在重度抑郁症状，建议立即转介专业评估。",
}


def severity_for(total: int) -> Severity:
    for upper, sev in SEVERITY_BANDS:
        if total <= upper:
            return sev
    return Severity.SEVERE


class PHQA(Scale):
    scale_id = "phq_a"
    scale_name = "PHQ-A 青少年抑郁筛查量表"
    description = "11-17 岁青少年过去 2 周抑郁筛查"
    items = PHQ_A_ITEMS
    options = PHQ_A_OPTIONS

    def score(self, answers: dict) -> ScoreResult:
        expected = {it["id"] for it in PHQ_A_ITEMS}

        missing = expected - answers.keys()
        if missing:
            raise ValueError(f"PHQ-A 缺失答题: {sorted(missing)}")

        for qid, val in answers.items():
            if qid not in expected:
                raise ValueError(f"PHQ-A 非法题号: {qid}")
            if not (MIN_SCORE <= val <= MAX_SCORE):
                raise ValueError(f"PHQ-A 题{qid} 非法分值: {val}（应为 0-3）")

        total = sum(answers[qid] for qid in expected)
        severity = severity_for(total)

        # 危机升级：第 9 题单项 > 0，不看总分
        triggers = []
        if answers[SUICIDE_ITEM_ID] > 0:
            triggers.append(
                f"第{SUICIDE_ITEM_ID}题 自杀意念/自伤想法 得分 {answers[SUICIDE_ITEM_ID]}"
            )
        crisis = CrisisLevel.ELEVATED if triggers else CrisisLevel.SAFE

        return ScoreResult(
            scale_id=self.scale_id,
            scale_name=self.scale_name,
            total_score=total,
            severity=severity,
            crisis_level=crisis,
            crisis_triggers=triggers,
            interpretation=SEVERITY_INTERPRETATION[severity],
        )


# 默认实例，便于注册
phq_a = PHQA()
