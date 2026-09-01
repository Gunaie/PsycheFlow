"""SCARED（儿童焦虑相关情绪筛查量表，Screen for Child Anxiety Related Emotional Disorders）。

适用：8-18 岁儿童青少年。
计分：
  - 41 题，每题 0-2（0 从不/不是 / 1 有时 / 2 经常/是）
  - 总分 0-82
  - 界值：总分 >= 25 提示可能存在焦虑障碍，建议进一步评估
注：SCARED 官方仅给 >=25 的阳性界值；本引擎的细分等级仅供参考，正式判定以总分为准。
因子分：各版本因子题号划分略有差异，正式落地前须与所采用的权威量表版本逐题核对，
        故 MVP 暂不输出因子分（factor_scores 留空）。
"""
from app.scales.base import Scale, ScoreResult, Severity, CrisisLevel

SCARED_OPTIONS = {
    0: "从不/不是",
    1: "有时",
    2: "经常/是",
}

# SCARED-41 标准题项（中文版）
SCARED_ITEMS = [
    {"id": 1, "text": "当父母和我分开时，我会感到害怕"},
    {"id": 2, "text": "我担心其他同学不喜欢我"},
    {"id": 3, "text": "当我觉得害怕时，会出汗"},
    {"id": 4, "text": "我害怕一个人睡"},
    {"id": 5, "text": "我担心自己会生病"},
    {"id": 6, "text": "我担心自己不像其他孩子那样好"},
    {"id": 7, "text": "我做游戏或其他事情时，害怕别人看着"},
    {"id": 8, "text": "我害怕上学"},
    {"id": 9, "text": "我担心会发生不好的事"},
    {"id": 10, "text": "我害怕自己会不由自主地做不好的事"},
    {"id": 11, "text": "我担心父母会出事"},
    {"id": 12, "text": "我害怕和不熟的人说话"},
    {"id": 13, "text": "别人看着我时，我会紧张"},
    {"id": 14, "text": "我担心在学校会被取笑"},
    {"id": 15, "text": "我害怕自己看上去很傻"},
    {"id": 16, "text": "我害怕自己会做错事"},
    {"id": 17, "text": "我担心别人觉得我长得不好看"},
    {"id": 18, "text": "我感到害怕时会发抖"},
    {"id": 19, "text": "我害怕自己会晕倒"},
    {"id": 20, "text": "我担心父母会离开我"},
    {"id": 21, "text": "我担心自己会死"},
    {"id": 22, "text": "当觉得自己表现不好时，我会很难受"},
    {"id": 23, "text": "我害怕自己会窒息"},
    {"id": 24, "text": "我担心在公众场合会做不好"},
    {"id": 25, "text": "我害怕和陌生人说话"},
    {"id": 26, "text": "我害怕自己会呕吐"},
    {"id": 27, "text": "我担心自己被人批评"},
    {"id": 28, "text": "我害怕自己心脏病发"},
    {"id": 29, "text": "我担心自己不如别人"},
    {"id": 30, "text": "我害怕自己会突然感到害怕"},
    {"id": 31, "text": "我担心自己会被人欺负"},
    {"id": 32, "text": "我害怕自己会失控"},
    {"id": 33, "text": "我担心自己会被单独留下"},
    {"id": 34, "text": "我害怕在同学面前做事"},
    {"id": 35, "text": "我担心自己会做噩梦"},
    {"id": 36, "text": "我感到害怕时会心跳加速"},
    {"id": 37, "text": "我害怕自己会突然晕过去"},
    {"id": 38, "text": "我担心自己会出丑"},
    {"id": 39, "text": "我害怕在很多人面前说话"},
    {"id": 40, "text": "我担心自己会被同学取笑"},
    {"id": 41, "text": "我害怕自己会突然哭出来"},
]

POSITIVE_CUTOFF = 25  # 官方阳性界值
MIN_SCORE = 0
MAX_SCORE = 2


def severity_for(total: int) -> Severity:
    # 官方仅 >=25 阳性；细分仅供参考，便于分级关注
    if total < POSITIVE_CUTOFF:
        return Severity.NONE
    if total <= 50:
        return Severity.MILD  # 轻度阳性
    return Severity.MODERATE  # 明显阳性


class SCARED(Scale):
    scale_id = "scared"
    scale_name = "SCARED 儿童焦虑相关情绪筛查量表"
    description = "8-18 岁儿童青少年焦虑筛查（41 题）"
    items = SCARED_ITEMS
    options = SCARED_OPTIONS

    def score(self, answers: dict) -> ScoreResult:
        expected = {it["id"] for it in SCARED_ITEMS}

        missing = expected - answers.keys()
        if missing:
            raise ValueError(f"SCARED 缺失答题: {sorted(missing)}")

        for qid, val in answers.items():
            if qid not in expected:
                raise ValueError(f"SCARED 非法题号: {qid}")
            if not (MIN_SCORE <= val <= MAX_SCORE):
                raise ValueError(f"SCARED 题{qid} 非法分值: {val}（应为 0-2）")

        total = sum(answers[qid] for qid in expected)
        severity = severity_for(total)

        if total < POSITIVE_CUTOFF:
            interp = f"总分 {total}/82，低于阳性界值 25，暂无明显焦虑倾向。"
        else:
            interp = (
                f"总分 {total}/82，达到阳性界值（>=25），"
                f"提示可能存在焦虑障碍，建议由学校心理老师/专业人员进一步评估。"
            )

        return ScoreResult(
            scale_id=self.scale_id,
            scale_name=self.scale_name,
            total_score=total,
            severity=severity,
            # 焦虑量表无自杀意念单项，永不触发危机升级
            crisis_level=CrisisLevel.SAFE,
            crisis_triggers=[],
            factor_scores={},  # 因子分待权威版本核对后实现
            interpretation=interp,
        )


scared = SCARED()
