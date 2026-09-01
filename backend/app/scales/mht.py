"""MHT（心理健康诊断测验）中学版。

适用：小学四年级至高中三年级学生自评（本系统面向中学生）。
来源：周步成等 1991 年据日本铃木清"不安倾向诊断测验"修订（华东师范大学）。
条目与计分键依据公开施测资料交叉核对（卫生部精神卫生调查方案、
多地教育平台施测版本一致）。

计分：
  - 100 题，二选一：是=1 / 不是=0
  - 8 个内容量表（因子分 0-15 或 0-10）：
      学习焦虑 1-15 | 对人焦虑 16-25 | 孤独倾向 26-35 | 自责倾向 36-45
      过敏倾向 46-55 | 身体症状 56-70 | 恐怖倾向 71-80 | 冲动倾向 81,83,...,99
  - 效度量表（测谎）：82,84,...,100 共 10 题；≥7 提示作答真实性存疑，
    建议适当时机重新施测（结果解释须谨慎）
  - 全量表分（0-90）= 8 个内容量表分之和（不含效度量表）
  - 界值：全量表分 ≥65 提示可能存在心理障碍，需制定特别指导计划；
    内容量表分 ≥8 为高分（针对性特别指导）；4-7 中等；0-3 较低

危机升级：第 85 题（"还是死了好"）或第 97 题（"想从高的地方跳下来"）
任一答"是"即触发危机升级，不看总分（与 PHQ-A 第 9 题同规则）。
"""
from app.scales.base import Scale, ScoreResult, Severity, CrisisLevel

MHT_OPTIONS = {
    1: "是",
    0: "不是",
}

MHT_ITEMS = [
    {"id": 1, "text": "你夜里睡觉时，是否总想着明天的功课？"},
    {"id": 2, "text": "老师在向全班提问时，你是否会觉得是在提问自己而感到不安？"},
    {"id": 3, "text": "你是否一听说\u201c要考试\u201d心里就紧张？"},
    {"id": 4, "text": "你考试成绩不好时，心里是否感到不快？"},
    {"id": 5, "text": "你学习成绩不好时，是否总是提心吊胆？"},
    {"id": 6, "text": "考试时，当你想不起来原先掌握的知识时，你是否会感到焦虑？"},
    {"id": 7, "text": "你考试后，在没有知道成绩之前，是否总是放心不下？"},
    {"id": 8, "text": "你是否一遇到考试，就担心会考坏？"},
    {"id": 9, "text": "你是否希望考试能顺利通过？"},
    {"id": 10, "text": "你在没有完成任务之前，是否总担心完不成任务？"},
    {"id": 11, "text": "你当着大家的面朗读课文时，是否总是怕读错？"},
    {"id": 12, "text": "你是否认为学校里得到的学习成绩总是不大可靠？"},
    {"id": 13, "text": "你是否认为你比别人更担心学习？"},
    {"id": 14, "text": "你是否做过考试考坏了的梦？"},
    {"id": 15, "text": "你是否做过学习成绩不好时，受到爸爸妈妈或老师训斥的梦？"},
    {"id": 16, "text": "你是否经常觉得有同学在背后说你的坏话？"},
    {"id": 17, "text": "你受到父母批评后，是否总是想不开，放在心上？"},
    {"id": 18, "text": "你在游戏或与别人的竞争中输给了对方，是否就不想再干了？"},
    {"id": 19, "text": "人家在背后议论你，你是否感到讨厌？"},
    {"id": 20, "text": "你在大家面前或被老师提问时，是否会脸红？"},
    {"id": 21, "text": "你是否很担心叫你担任班干部？"},
    {"id": 22, "text": "你是否总是觉得好像有人在注意你？"},
    {"id": 23, "text": "在工作或学习时，如果有人注意你，你心里是否紧张？"},
    {"id": 24, "text": "你受到批评时，心情是否不愉快？"},
    {"id": 25, "text": "你受到老师批评时，心里是否总是不安？"},
    {"id": 26, "text": "同学们在笑时，你是否也不会笑？"},
    {"id": 27, "text": "你是否觉得到同学家里去玩不如在自己家里玩？"},
    {"id": 28, "text": "你和大家在一起时，是否也觉得自己是孤单的一个人？"},
    {"id": 29, "text": "你是否觉得和同学一起玩，不如自己一个人玩？"},
    {"id": 30, "text": "同学们在交谈时，你是否不想加入？"},
    {"id": 31, "text": "当你和大家在一起时，是否觉得自己是多余的人？"},
    {"id": 32, "text": "你是否讨厌参加运动会和文艺演出会？"},
    {"id": 33, "text": "你的朋友是否很少？"},
    {"id": 34, "text": "你是否不喜欢同别人谈话？"},
    {"id": 35, "text": "在人多的地方，你是否觉得很怕？"},
    {"id": 36, "text": "你在排球、篮球、足球、拔河、广播操等体育比赛输了时，心里是否一直认为自己不好？"},
    {"id": 37, "text": "你受到批评后，是否总认为是自己不好？"},
    {"id": 38, "text": "别人笑你的时候，你是否会认为是自己做错了什么事？"},
    {"id": 39, "text": "你学习成绩不好时，是否总是认为是自己不用功的缘故？"},
    {"id": 40, "text": "你失败的时候，是否总是认为是自己的责任？"},
    {"id": 41, "text": "大家受到责备时，你是否认为主要是自己的过错？"},
    {"id": 42, "text": "你在乒乓球、羽毛球、篮球、足球、拔河、广播操等体育比赛时，是否一出错就特别留神？"},
    {"id": 43, "text": "碰到为难的事情时，你是否认为自己难以应付？"},
    {"id": 44, "text": "你是否有时会后悔，那件事不做就好了？"},
    {"id": 45, "text": "你和同学吵架以后，是否总是认为是自己的错？"},
    {"id": 46, "text": "你心里是否总想为班级做点好事？"},
    {"id": 47, "text": "你学习的时候，思想是否经常开小差？"},
    {"id": 48, "text": "你把东西借给别人时，是否担心别人会把东西弄坏？"},
    {"id": 49, "text": "碰到不顺利的事情时，你心里是否很烦躁？"},
    {"id": 50, "text": "你是否非常担心家里有人生病或死去？"},
    {"id": 51, "text": "你是否在梦里见到过死去的人？"},
    {"id": 52, "text": "你对收音机和汽车的声音是否特别敏感？"},
    {"id": 53, "text": "你心里是否总觉得好像有什么事没有做好？"},
    {"id": 54, "text": "你是否担心会发生什么意外的事？"},
    {"id": 55, "text": "你在决定要做什么事时，是否总是犹豫不决？"},
    {"id": 56, "text": "你手上是否经常出汗？"},
    {"id": 57, "text": "你害羞时是否会脸红？"},
    {"id": 58, "text": "你是否经常头痛？"},
    {"id": 59, "text": "你被老师提问时，心里是否总是很紧张？"},
    {"id": 60, "text": "你没有参加运动，心脏是否经常噗通噗通地跳？"},
    {"id": 61, "text": "你是否很容易疲劳？"},
    {"id": 62, "text": "你是否很不愿吃药？"},
    {"id": 63, "text": "夜里你是否很难入睡？"},
    {"id": 64, "text": "你是否总觉得身体好像有什么毛病？"},
    {"id": 65, "text": "你是否经常认为自己的体型和面孔比别人难看？"},
    {"id": 66, "text": "你是否经常觉得肠胃不好？"},
    {"id": 67, "text": "你是否经常咬指甲？"},
    {"id": 68, "text": "你是否舔手指头？"},
    {"id": 69, "text": "你是否经常感到呼吸困难？"},
    {"id": 70, "text": "你去厕所的次数是否比别人多？"},
    {"id": 71, "text": "你是否很怕到高的地方去？"},
    {"id": 72, "text": "你是否害怕很多东西？"},
    {"id": 73, "text": "你是否经常做噩梦？"},
    {"id": 74, "text": "你胆子是否很小？"},
    {"id": 75, "text": "夜里，你是否很怕一个人在房间里睡觉？"},
    {"id": 76, "text": "你乘车穿过隧道或路过高桥时，是否很怕？"},
    {"id": 77, "text": "你是否喜欢整夜开着灯睡觉？"},
    {"id": 78, "text": "你听到打雷声是否非常害怕？"},
    {"id": 79, "text": "你是否非常害怕黑暗？"},
    {"id": 80, "text": "你是否经常感到后面有人跟着你？"},
    {"id": 81, "text": "你是否经常生气？"},
    {"id": 82, "text": "你是否不想得到好的成绩？", "validity": True},
    {"id": 83, "text": "你是否经常会突然想哭？"},
    {"id": 84, "text": "你以前是否说过谎话？", "validity": True},
    {"id": 85, "text": "你有时是否会觉得，还是死了好？", "suicide": True},
    {"id": 86, "text": "你是否一次也没有失约过？", "validity": True},
    {"id": 87, "text": "你是否经常想大声喊叫？"},
    {"id": 88, "text": "你是否不愿说出别人不让说的事？", "validity": True},
    {"id": 89, "text": "你有时是否想过自己一个人到遥远的地方去？"},
    {"id": 90, "text": "你是否总是很有礼貌？", "validity": True},
    {"id": 91, "text": "你被人说了坏话，是否想立即采取报复行动？"},
    {"id": 92, "text": "老师或父母说的话，你是否都照办？", "validity": True},
    {"id": 93, "text": "你心里不开心，是否会乱丢、乱砸东西？"},
    {"id": 94, "text": "你是否发过怒？", "validity": True},
    {"id": 95, "text": "你想要的东西，是否就一定要拿到手？"},
    {"id": 96, "text": "你不喜欢的课，老师提前下课，你是否会感到特别高兴？", "validity": True},
    {"id": 97, "text": "你是否经常想从高的地方跳下来？", "suicide": True},
    {"id": 98, "text": "你是否无论对谁都很亲热？", "validity": True},
    {"id": 99, "text": "你是否会经常急躁得坐立不安？"},
    {"id": 100, "text": "对不认识的人，你是否会都喜欢？", "validity": True},
]

CONTENT_SCALES = {
    "学习焦虑": list(range(1, 16)),
    "对人焦虑": list(range(16, 26)),
    "孤独倾向": list(range(26, 36)),
    "自责倾向": list(range(36, 46)),
    "过敏倾向": list(range(46, 56)),
    "身体症状": list(range(56, 71)),
    "恐怖倾向": list(range(71, 81)),
    "冲动倾向": [81, 83, 85, 87, 89, 91, 93, 95, 97, 99],
}
VALIDITY_IDS = [82, 84, 86, 88, 90, 92, 94, 96, 98, 100]
SUICIDE_IDS = [85, 97]

TOTAL_THRESHOLD = 65       # 全量表分 ≥65：提示可能存在心理障碍
FACTOR_HIGH = 8            # 因子分 ≥8：高分，需针对性特别指导
FACTOR_MID = 4             # 因子分 4-7：中等
VALIDITY_WARN = 7          # 效度分 ≥7：作答真实性存疑


class MHT(Scale):
    scale_id = "mht"
    scale_name = "MHT 心理健康诊断测验（中学版）"
    description = "小学四年级至高中学生自评，评估学习焦虑、对人焦虑、孤独、自责、过敏、身体症状、恐怖与冲动倾向"
    items = MHT_ITEMS
    options = MHT_OPTIONS

    def score(self, answers: dict) -> ScoreResult:
        expected = {it["id"] for it in MHT_ITEMS}

        missing = expected - answers.keys()
        if missing:
            raise ValueError(f"MHT 缺失答题: {sorted(missing)}")
        for qid, val in answers.items():
            if qid not in expected:
                raise ValueError(f"MHT 非法题号: {qid}")
            if val not in (0, 1):
                raise ValueError(f"MHT 题{qid} 非法分值: {val}（应为 0-1）")

        # 危机升级：第 85/97 题单项，不看总分
        triggers = []
        for qid in SUICIDE_IDS:
            if answers[qid] == 1:
                item_text = next(it["text"] for it in MHT_ITEMS if it["id"] == qid)
                triggers.append(f"第{qid}题「{item_text}」答\u201c是\u201d")

        factor_scores = {
            name: sum(answers[q] for q in qids) for name, qids in CONTENT_SCALES.items()
        }
        validity = sum(answers[q] for q in VALIDITY_IDS)
        total = sum(factor_scores.values())

        # severity：官方界值取最高档
        if total >= TOTAL_THRESHOLD:
            severity = Severity.SEVERE
        elif any(v >= FACTOR_HIGH for v in factor_scores.values()):
            severity = Severity.MODERATE
        elif any(FACTOR_MID <= v < FACTOR_HIGH for v in factor_scores.values()):
            severity = Severity.MILD
        else:
            severity = Severity.NONE

        notes = []
        if validity >= VALIDITY_WARN:
            notes.append(f"⚠ 效度量表得分 {validity}/10，作答真实性存疑，"
                         "建议在适当时机重新施测后再解释结果。")
        notes.append(
            "MHT 总分（全量表分）反映整体焦虑水平，"
            "因子分反映各维度焦虑强度；本测验为筛查与指导工具，不能替代临床诊断。"
        )
        if severity == Severity.SEVERE:
            notes.append(f"全量表分 {total} ≥{TOTAL_THRESHOLD}，"
                         "提示可能存在明显心理困扰，须由学校心理老师进一步评估并制定特别指导计划。")
        high_factors = [n for n, v in factor_scores.items() if v >= FACTOR_HIGH]
        if high_factors:
            notes.append("高分维度：" + "、".join(f"{n}（{factor_scores[n]}）" for n in high_factors) + "。")
        interpretation = "".join(notes)

        return ScoreResult(
            scale_id=self.scale_id,
            scale_name=self.scale_name,
            total_score=total,
            severity=severity,
            crisis_level=CrisisLevel.ELEVATED if triggers else CrisisLevel.SAFE,
            crisis_triggers=triggers,
            factor_scores={**factor_scores, "效度量表": validity},
            interpretation=interpretation,
        )


# 默认实例，便于注册
mht = MHT()
