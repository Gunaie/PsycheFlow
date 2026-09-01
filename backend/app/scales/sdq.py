"""SDQ（Strengths and Difficulties Questionnaire）长处和困难问卷 · 学生自评版。

适用：11-17 岁青少年自评，评估过去 6 个月。
来源：Goodman 1997 编制；条目采用杜亚松修订中文版；计分规则与界值
依据 sdqinfo.org 官方计分表（4-17 岁自评）与卫生部《精神卫生工作指标
调查评估方案》（卫办疾控发〔2010〕，自评版反向条目 7/11/14/21/25）。

计分：
  - 25 题，每题 0-2（0 不符合 / 1 有点符合 / 2 完全符合）
  - 反向计分条目：第 7、11、14、21、25 题（2-原始分）
  - 5 因子（各 0-10）：
      情绪症状 3,8,13,16,24 | 品行问题 5,7,12,18,22
      多动/注意缺陷 2,10,15,21,25 | 同伴交往问题 6,11,14,19,23
      亲社会行为 1,4,9,17,20
  - 总困难分（0-40）= 情绪+品行+多动+同伴（不含亲社会）
  - 内化分 = 情绪+同伴；外化分 = 品行+多动（各 0-20）

等级（自评版官方三段）：
  - 总困难：0-15 正常 / 16-19 边缘 / 20-40 异常
  - 因子界值（正常/边缘/异常）：
      情绪 0-5/6/7-10 | 品行 0-3/4/5-10 | 多动 0-5/6/7-10
      同伴 0-3/4-5/6-10 | 亲社会 6-10/5/0-4（越高越好）

危机：SDQ 无自杀意念题项，不作单项危机触发（量表端危机恒 SAFE；
对话端另有前置关键词扫描兜底）。
"""
from app.scales.base import Scale, ScoreResult, Severity, CrisisLevel

SDQ_OPTIONS = {
    0: "不符合",
    1: "有点符合",
    2: "完全符合",
}

# 学生自评版 25 题（杜亚松修订中文版）
SDQ_ITEMS = [
    {"id": 1, "text": "我尝试对别人友善，我关心别人的感受"},
    {"id": 2, "text": "我不能安定，不能长时间保持安静"},
    {"id": 3, "text": "我经常头痛、肚子痛或身体不舒服"},
    {"id": 4, "text": "我常与他人分享东西（食物、玩具、笔）"},
    {"id": 5, "text": "我觉得非常愤怒及常发脾气"},
    {"id": 6, "text": "我经常独处，我通常独自玩耍"},
    {"id": 7, "text": "我通常依照吩咐做事", "reverse": True},
    {"id": 8, "text": "我经常担忧，心事重重"},
    {"id": 9, "text": "如果有人受伤、难过或不适，我都乐意帮忙"},
    {"id": 10, "text": "我经常坐立不安或感到不耐烦"},
    {"id": 11, "text": "我有一个或几个好朋友", "reverse": True},
    {"id": 12, "text": "我经常与别人争执，我能使别人依我的想法行事"},
    {"id": 13, "text": "我经常不快乐、心情沉重或流泪"},
    {"id": 14, "text": "一般来说，其他与我年龄相近的人都喜欢我", "reverse": True},
    {"id": 15, "text": "我容易分心，我觉得难以集中精神"},
    {"id": 16, "text": "我在新的环境中会感到紧张，我很容易失去自信"},
    {"id": 17, "text": "我会友善地对待比我年少的孩子"},
    {"id": 18, "text": "我常被指责撒谎或不老实"},
    {"id": 19, "text": "其他小孩或青少年常捉弄或欺负我"},
    {"id": 20, "text": "我常自愿帮助别人（父母、老师、同学）"},
    {"id": 21, "text": "我做事前会先想清楚", "reverse": True},
    {"id": 22, "text": "我会从家里、学校或别处拿取不属于我的东西"},
    {"id": 23, "text": "我与大人相处比与同辈相处融洽"},
    {"id": 24, "text": "我心中有许多恐惧，我很容易受惊吓"},
    {"id": 25, "text": "我总能把手头上的事情办妥，我的注意力良好", "reverse": True},
]

REVERSE_IDS = {7, 11, 14, 21, 25}
MIN_SCORE = 0
MAX_SCORE = 2

# 因子名 -> 条目
SUBSCALES = {
    "情绪症状": [3, 8, 13, 16, 24],
    "品行问题": [5, 7, 12, 18, 22],
    "多动/注意缺陷": [2, 10, 15, 21, 25],
    "同伴交往问题": [6, 11, 14, 19, 23],
    "亲社会行为": [1, 4, 9, 17, 20],
}

# (上界, 等级)：总困难分 0-40
TOTAL_BANDS = [
    (15, Severity.NONE),
    (19, Severity.MILD),
    (40, Severity.MODERATE),
]

SEVERITY_INTERPRETATION = {
    Severity.NONE: "总体情绪与行为状况正常。",
    Severity.MILD: "总体困难水平处于边缘范围，建议关注与随访。",
    Severity.MODERATE: "总体困难水平处于异常范围（可能存在情绪或行为问题），"
                       "建议由学校心理老师进一步评估。注意：SDQ 为筛查工具，不能替代临床诊断。",
}

# 因子界值（低-正常 / 边缘 / 高-异常）；亲社会为反向（高分好）
FACTOR_BANDS = {
    "情绪症状": (5, 6),
    "品行问题": (3, 4),
    "多动/注意缺陷": (5, 6),
    "同伴交往问题": (3, 4),
}


def severity_for_total(total: int) -> Severity:
    for upper, sev in TOTAL_BANDS:
        if total <= upper:
            return sev
    return Severity.MODERATE


class SDQ(Scale):
    scale_id = "sdq"
    scale_name = "SDQ 长处和困难问卷（学生自评版）"
    description = "11-17 岁青少年自评，评估过去 6 个月的情绪、行为、多动、同伴与亲社会状况"
    items = SDQ_ITEMS
    options = SDQ_OPTIONS

    def score(self, answers: dict) -> ScoreResult:
        expected = {it["id"] for it in SDQ_ITEMS}

        missing = expected - answers.keys()
        if missing:
            raise ValueError(f"SDQ 缺失答题: {sorted(missing)}")
        for qid, val in answers.items():
            if qid not in expected:
                raise ValueError(f"SDQ 非法题号: {qid}")
            if not (MIN_SCORE <= val <= MAX_SCORE):
                raise ValueError(f"SDQ 题{qid} 非法分值: {val}（应为 0-2）")

        # 反向计分：第 7/11/14/21/25 题 = 2 - 原始分
        scored = {
            qid: (2 - answers[qid]) if qid in REVERSE_IDS else answers[qid]
            for qid in expected
        }

        factor_scores = {
            name: sum(scored[q] for q in qids) for name, qids in SUBSCALES.items()
        }
        total_difficulties = (
            factor_scores["情绪症状"] + factor_scores["品行问题"]
            + factor_scores["多动/注意缺陷"] + factor_scores["同伴交往问题"]
        )
        severity = severity_for_total(total_difficulties)

        # interpretation：等级 + 因子高分提示 + 亲社会低分提示
        notes = [SEVERITY_INTERPRETATION[severity]]
        for name, (normal_upper, borderline) in FACTOR_BANDS.items():
            if factor_scores[name] > borderline:
                notes.append(f"{name}得分偏高（{factor_scores[name]}/10，超出正常界值）。")
        if factor_scores["亲社会行为"] <= 4:
            notes.append(f"亲社会行为得分偏低（{factor_scores['亲社会行为']}/10），"
                         "提示同伴互助与社交技能需要支持。")
        interpretation = "".join(notes)

        return ScoreResult(
            scale_id=self.scale_id,
            scale_name=self.scale_name,
            total_score=total_difficulties,  # 总困难分即 SDQ 主分数
            severity=severity,
            crisis_level=CrisisLevel.SAFE,
            crisis_triggers=[],
            factor_scores={
                **factor_scores,
                "内化分(情绪+同伴)": factor_scores["情绪症状"] + factor_scores["同伴交往问题"],
                "外化分(品行+多动)": factor_scores["品行问题"] + factor_scores["多动/注意缺陷"],
            },
            interpretation=interpretation,
        )


# 默认实例，便于注册
sdq = SDQ()
