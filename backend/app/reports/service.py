"""报告生成：参考《中小学心理健康诊断测验(MHT)》报告结构，完整复刻 7 大章节。

章节结构与参考 MHT 对齐：
  页眉 → 个人基本信息条 → 1.测评工具介绍 → 2.测评结果解读注意事项 →
  3.测评人员信息表 → 4.测评结果(综合等级渐变横幅 + 因子雷达图 + 尺度总览表 + 因子剖析表) →
  5.测评结果剖析(逐因子详解卡片) → 6.发展建议(LLM)

危机兜底：
  危机升级情形下，在"综合等级横幅"与"人员信息表"之间硬编码红色危机框
 （safety.crisis_message + 12355 热线），零 LLM——即便 LLM 建议软化，硬框不可绕过。
"""
import math
import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown import markdown
from weasyprint import HTML

from app.core.config import settings
from app.core.llm import provider
from app.core.safety import crisis_message
from app.scales.registry import get_scale

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"])
)

# —— 严重度 5 级映射 ——
SEVERITY_LABEL = {
    "none": "无明显症状",
    "mild": "轻度",
    "moderate": "中度",
    "moderately_severe": "中重度",
    "severe": "重度",
}
SEVERITY_COLOR = {
    "none": "#16a34a",
    "mild": "#ca8a04",
    "moderate": "#ea580c",
    "moderately_severe": "#dc2626",
    "severe": "#b91c1c",
}
# 严重度排序 → 综合等级取最差
SEVERITY_RANK = {
    "none": 0, "mild": 1, "moderate": 2, "moderately_severe": 3, "severe": 4,
}

# —— 综合等级（用于 4 号章节的渐变横幅，对应 MHT 的"良好"等大字号） ——
OVERALL_LEVEL = {
    "none": {
        "label": "心理健康",
        "en": "Mentally Healthy",
        "gradient": "linear-gradient(90deg,#10b981 0%,#14b8a6 100%)",
        "bar_color": "#10b981",
    },
    "mild": {
        "label": "轻度关注",
        "en": "Mild Concern",
        "gradient": "linear-gradient(90deg,#facc15 0%,#fb923c 100%)",
        "bar_color": "#f59e0b",
    },
    "moderate": {
        "label": "需要关注",
        "en": "Needs Attention",
        "gradient": "linear-gradient(90deg,#fb923c 0%,#ef4444 100%)",
        "bar_color": "#ea580c",
    },
    "moderately_severe": {
        "label": "需要重视",
        "en": "Significant Concern",
        "gradient": "linear-gradient(90deg,#ef4444 0%,#b91c1c 100%)",
        "bar_color": "#dc2626",
    },
    "severe": {
        "label": "需立即干预",
        "en": "Immediate Intervention",
        "gradient": "linear-gradient(90deg,#b91c1c 0%,#7f1d1d 100%)",
        "bar_color": "#b91c1c",
    },
}

# —— 量表工具介绍 ——
SCALE_INTRO = {
    "phq_a": (
        "PHQ-A 青少年抑郁筛查量表（Patient Health Questionnaire for Adolescents）"
        "基于国际通用的 PHQ-9 抑郁筛查九项量表，专为 11-17 岁青少年编制。"
        "量表采用李克特 4 级评分（0=完全没有至 3=几乎每天），评估过去两周内的抑郁症状"
        "频率与强度，总分 0-27，界值 ≥ 11 提示存在抑郁筛查阳性，建议由学校心理老师或"
        "专业人员进一步评估。PHQ-A 在全球校园普测中广泛使用，具有良好的信效度与青少年"
        "适配性，被纳入多个国家中小学心理健康筛查推荐工具清单。"
    ),
    "scared": (
        "SCARED 儿童焦虑相关情绪筛查量表（Screen for Child Anxiety Related Emotional "
        "Disorders）由美国匹兹堡大学 Birmaher 教授团队编制、中文版经国内学者修订，"
        "适用于 8-18 岁儿童青少年。量表包含 41 个条目，采用 0-2 三级评分，总分 0-82，"
        "官方推荐界值 ≥ 25 为阳性筛查，提示可能存在广泛性焦虑、社交恐惧、学校恐惧、"
        "分离焦虑或躯体化惊恐等焦虑相关症状。SCARED 是我国学校心理普测常用工具，"
        "具备良好的信度、效度及性别/年龄测量等值性。"
    ),
    "sdq": (
        "SDQ 长处和困难问卷（Strengths and Difficulties Questionnaire，学生自评版）"
        "由英国 Goodman 教授编制、国内杜亚松等修订，适用于 11-17 岁青少年自评，"
        "评估过去 6 个月的情绪、品行、多动注意、同伴交往与亲社会五个维度。"
        "量表共 25 题，每题 0-2 三级评分（含 5 道反向计分题），总困难分 0-40，"
        "界值 16-19 为边缘、≥20 为异常。SDQ 兼顾困难与长处（亲社会为优势维度），"
        "被广泛用于校园心理筛查与流行病学调查，信效度良好。"
    ),
    "mht": (
        "MHT 心理健康诊断测验（中学版）由华东师范大学周步成等 1991 年据日本铃木清"
        "「不安倾向诊断测验」修订，适用于小学四年级至高中三年级学生。量表共 100 题，"
        "二选一计分（是=1/不是=0），涵盖学习焦虑、对人焦虑、孤独倾向、自责倾向、"
        "过敏倾向、身体症状、恐怖倾向与冲动倾向 8 个内容量表，并设效度量表评估"
        "作答真实性。全量表分 ≥65 提示可能存在心理障碍，需制定特别指导计划；"
        "分量表分 ≥8 为高分，需针对性指导。MHT 是我国中小学心理健康普测的经典工具。"
    ),
}

# —— 2 号章节：测评结果解读注意事项 ——
INTERPRETATION_NOTES = (
    "为了更好地理解和使用本测评结果，请相关工作人员认真阅读以下注意事项："
    "心理测验只在一定程度上反映了受测者在所测内容上的现状和特点，不能机械地将结果认定为最终结论，"
    "而要结合受测者的日常学习、家庭情况、同伴关系、健康状况、近期突发事件等多重因素、"
    "具体情况具体分析，绝不能只根据测验结果给被试贴标签或做定论。"
    "如果需要做定论的情况，由具备相关专业资质的人员来慎重裁定。"
)

# —— PHQ-A 4 子维度（按 PHQ-9 国际通用分法） ——
# 题号 → {name, description, level_interpretation}
PHQ_A_SUBDIMS = [
    {
        "key": "cog_affect",
        "name": "认知/情感症状",
        "desc": "核心抑郁体验：做事提不起劲、情绪低落/沮丧",
        "items": [1, 2],
        "interps": {
            "low": "在兴趣与情绪层面状态良好，能感受到日常活动的愉悦与投入。",
            "mid": "对活动的兴趣有所下降，情绪偶尔陷入低落，建议关注与调节。",
            "high": "兴趣感显著缺失，情绪持续沮丧，可能影响学习与社交功能，建议专业评估。",
        },
    },
    {
        "key": "somatic",
        "name": "睡眠/精力/食欲",
        "desc": "躯体与植物神经症状：入睡困难、疲劳无力、食欲变化",
        "items": [3, 4, 5],
        "interps": {
            "low": "睡眠规律、精力饱满、饮食稳定，躯体层面健康。",
            "mid": "睡眠或食欲出现波动，日常精力稍感不足，可尝试作息调整与运动。",
            "high": "睡眠/食欲紊乱明显，精力耗竭感强烈，可能影响日常功能，建议尽快就医评估。",
        },
    },
    {
        "key": "self_concept",
        "name": "自我价值/注意力",
        "desc": "认知功能与自我评价：自我失败感、注意力难集中",
        "items": [6, 7],
        "interps": {
            "low": "对自我有较稳定的正向认知，学习与活动中注意力集中。",
            "mid": "偶有自我否定想法或学习时注意力分散，可结合减压与正念练习调整。",
            "high": "强烈的自我否定/挫败感，注意力难以维持，严重影响学习表现，需专业支持。",
        },
    },
    {
        "key": "psychomotor",
        "name": "精神运动/风险意念",
        "desc": "运动迟滞/激越与安全风险：语速动作慢或烦躁、自杀/自伤念头",
        "items": [8, 9],
        "interps": {
            "low": "行为节奏正常，无任何自伤或结束生命相关的念头。",
            "mid": "偶有烦躁或迟滞感，若涉及危险念头须立即告知信任的成年人。",
            "high": "出现明显的精神运动异常或自我伤害/结束生命的想法，请立即联系学校心理老师、"
                    "家长并拨打 12355 青少年心理援助热线，必要时拨打 120 或 110。",
        },
    },
]
PHQ_A_MAX_SINGLE = 3  # 每题 0-3

# —— SCARED-41 5 子维度（MVP 实用分组，总分校 82 = 41×2） ——
# 8+6+12+11+4 = 41 题；max = 16+12+24+22+8 = 82
SCARED_SUBDIMS = [
    {
        "key": "somatic",
        "name": "躯体反应",
        "desc": "焦虑在身体上的体现：出汗、发抖、心跳、晕倒、呕吐、心脏病感",
        "items": [3, 18, 19, 23, 26, 28, 36, 37],
        "interps": {
            "low": "身体状态稳定，焦虑较少通过躯体化方式表达。",
            "mid": "偶有心跳快、出汗、紧张发抖等身体反应，可配合呼吸放松训练缓解。",
            "high": "躯体症状频繁且强烈，可能伴随晕倒或急诊类似发作，建议心内科与心理科联合评估。",
        },
    },
    {
        "key": "separation",
        "name": "分离焦虑",
        "desc": "与依恋对象分离相关的焦虑：父母分开、独处、被独自留下",
        "items": [1, 4, 11, 20, 33, 35],
        "interps": {
            "low": "能够以适龄的方式应对与家人的短暂分离，日常住宿/上学适应良好。",
            "mid": "分离时有一定顾虑或偶尔做相关噩梦，可通过渐进式陪伴与固定仪式感适应。",
            "high": "对分离存在强烈持续的恐惧，影响上学/独立活动，建议家庭和学校联合干预。",
        },
    },
    {
        "key": "general",
        "name": "广泛性担忧",
        "desc": "对健康、家庭、未来、自身表现等多领域的普遍担忧",
        "items": [5, 6, 9, 10, 15, 16, 17, 21, 27, 29, 30, 32],
        "interps": {
            "low": "对未来和自身有较合理的预期，不易陷入反复担忧。",
            "mid": "在多方面偶尔出现过虑，可尝试担忧外化书写与现实检验练习。",
            "high": "担忧广泛且难以控制，可能引发睡眠与专注问题，建议接受 CBT 取向的专业辅导。",
        },
    },
    {
        "key": "social",
        "name": "社交恐惧",
        "desc": "对公众场合与他人评价的焦虑：被注视、被取笑、当众发言",
        "items": [2, 7, 12, 13, 14, 24, 25, 34, 38, 39, 40],
        "interps": {
            "low": "社交中状态自然，能够真实表达自己，不被他人评价过度影响。",
            "mid": "在陌生或公众场合偶尔紧张，可通过社交暴露练习与角色扮演逐步适应。",
            "high": "对社交/当众情境极度回避或痛苦，可能严重影响交友与课堂参与，需专业干预。",
        },
    },
    {
        "key": "school",
        "name": "学校恐惧/校园焦虑",
        "desc": "校园场景特有的焦虑：害怕上学、怕被欺负、怕哭出来失态",
        "items": [8, 22, 31, 41],
        "interps": {
            "low": "对上学持积极或稳定的态度，校园生活无明显回避。",
            "mid": "偶尔因担心被取笑或受欺负而对上学产生紧张感，可与班主任/心理老师沟通了解。",
            "high": "强烈害怕上学或出现校园相关的极端反应，必须尽快排查是否存在校园欺凌等环境风险。",
        },
    },
]
SCARED_MAX_SINGLE = 2  # 每题 0-2

# —— SDQ 5 因子（25 题 0-2；反向题 7/11/14/21/25 = 2-原始分） ——
# 亲社会行为为「优势维度」(inverse=True)：高分=良好，通过反转 pct 让高分映射为低严重度（绿色）
SDQ_SUBDIMS = [
    {
        "key": "emotional",
        "name": "情绪症状",
        "desc": "头痛/腹痛/担忧/不快乐/恐惧等情绪与躯体不适",
        "items": [3, 8, 13, 16, 24],
        "interps": {
            "low": "情绪状态平稳，无明显焦虑或低落反应。",
            "mid": "偶有担忧或不快乐，建议关注情绪变化与压力源。",
            "high": "情绪症状明显，可能影响日常学习与社交，建议专业评估。",
        },
    },
    {
        "key": "conduct",
        "name": "品行问题",
        "desc": "愤怒/争执/说谎/偷拿等外化行为",
        "items": [5, 7, 12, 18, 22],
        "interps": {
            "low": "行为规范，无明显品行问题。",
            "mid": "偶有情绪失控或冲突行为，可结合规则引导与情绪管理训练。",
            "high": "品行问题突出，可能影响他人与自身，建议家校协同干预。",
        },
    },
    {
        "key": "hyperactivity",
        "name": "多动/注意缺陷",
        "desc": "坐不住/易分心/不耐烦/冲动",
        "items": [2, 10, 15, 21, 25],
        "interps": {
            "low": "注意力稳定，能安静持久完成任务。",
            "mid": "偶有分心或多动表现，可尝试结构化任务与短时专注训练。",
            "high": "多动/注意缺陷明显，可能影响学习表现，建议专业评估。",
        },
    },
    {
        "key": "peer",
        "name": "同伴交往问题",
        "desc": "独处/被欺负/与大人相处多于同伴",
        "items": [6, 11, 14, 19, 23],
        "interps": {
            "low": "同伴关系良好，能融入群体。",
            "mid": "偶尔独处或感觉被排斥，可鼓励参与小组活动。",
            "high": "同伴交往困难明显，可能存在被欺凌风险，建议班主任与心理老师介入了解。",
        },
    },
    {
        "key": "prosocial",
        "name": "亲社会行为",
        "desc": "友善/分享/助人（高分=优势资源，低分=需支持）",
        "items": [1, 4, 9, 17, 20],
        "inverse": True,
        "interps": {
            "low": "亲社会行为丰富，是明显的心理优势资源。",
            "mid": "亲社会行为适中，可鼓励更多分享与助人。",
            "high": "亲社会行为不足，建议培养同理心与社交技能。",
        },
    },
]
SDQ_MAX_SINGLE = 2
SDQ_REVERSE_IDS = {7, 11, 14, 21, 25}

# —— MHT 8 内容量表（100 题 0/1；冲动倾向含 85/97 自杀题，因子分仅作维度展示，危机由顶部硬框处理） ——
MHT_SUBDIMS = [
    {
        "key": "study_anxiety",
        "name": "学习焦虑",
        "desc": "对考试、成绩、学习任务的过度紧张与担忧",
        "items": list(range(1, 16)),
        "interps": {
            "low": "对学习持合理紧张度，能正常应考。",
            "mid": "学习焦虑中等，偶有考前紧张，可配合放松训练。",
            "high": "学习焦虑明显，可能影响发挥与学习兴趣，建议学业减压与心理支持。",
        },
    },
    {
        "key": "social_anxiety",
        "name": "对人焦虑",
        "desc": "被关注/被批评/被议论时的不安与脸红",
        "items": list(range(16, 26)),
        "interps": {
            "low": "人际互动自然，无明显社交焦虑。",
            "mid": "偶在公众场合紧张，可循序渐进行社交暴露练习。",
            "high": "对人焦虑明显，可能影响课堂参与与交友，建议心理辅导。",
        },
    },
    {
        "key": "loneliness",
        "name": "孤独倾向",
        "desc": "独处偏好、感觉多余、难以融入群体",
        "items": list(range(26, 36)),
        "interps": {
            "low": "能融入集体，无明显孤独感。",
            "mid": "偶尔感觉孤单，可鼓励同伴活动与兴趣社团。",
            "high": "孤独倾向明显，建议排查社交支持网络与抑郁风险。",
        },
    },
    {
        "key": "self_blame",
        "name": "自责倾向",
        "desc": "将失败/批评归因于自身、过度自责",
        "items": list(range(36, 46)),
        "interps": {
            "low": "归因方式较平衡，不过度自责。",
            "mid": "偶有自责想法，可结合认知重构练习调整。",
            "high": "自责倾向明显，可能影响自我评价，建议认知行为取向辅导。",
        },
    },
    {
        "key": "allergy",
        "name": "过敏倾向",
        "desc": "对声音/意外/决策的过度敏感与犹豫",
        "items": list(range(46, 56)),
        "interps": {
            "low": "感官与决策反应适度。",
            "mid": "偶有敏感或犹豫，可练习正念与决断。",
            "high": "过敏倾向明显，可能影响专注与效率，建议关注焦虑水平。",
        },
    },
    {
        "key": "somatic",
        "name": "身体症状",
        "desc": "出汗/头痛/失眠/肠胃等躯体化表现",
        "items": list(range(56, 71)),
        "interps": {
            "low": "身体状态稳定，无明显躯体化。",
            "mid": "偶有头痛或失眠，可调整作息与运动。",
            "high": "身体症状明显，建议心内科/心理科联合评估排除器质性问题。",
        },
    },
    {
        "key": "phobia",
        "name": "恐怖倾向",
        "desc": "对高处/黑暗/独处/雷声的强烈恐惧",
        "items": list(range(71, 81)),
        "interps": {
            "low": "无明显恐怖反应。",
            "mid": "偶有恐惧，可配合渐进暴露。",
            "high": "恐怖倾向明显，影响日常活动，建议专业干预。",
        },
    },
    {
        "key": "impulse",
        "name": "冲动倾向",
        "desc": "愤怒/急躁/想喊叫/情绪失控",
        "items": [81, 83, 85, 87, 89, 91, 93, 95, 97, 99],
        "interps": {
            "low": "情绪节制良好，无明显冲动。",
            "mid": "偶有急躁，可练习情绪暂停与表达。",
            "high": "冲动倾向明显，建议关注情绪调节与安全，必要时专业支持。",
        },
    },
]
MHT_MAX_SINGLE = 1

# —— LLM 提示词 ——
REPORT_SYSTEM = (
    "你是 PsycheFlow 心理评估报告的「发展建议」撰写助手。"
    "请根据提供的量表结果与子维度剖析，撰写 300-450 字、面向中小学生及家长/心理老师的「发展建议」："
    "先以共情语气肯定受测者当前状态；再分 2-4 小节给出家庭、学校、自我调节三方面的可执行建议"
    "（如固定作息、腹式呼吸练习、定时亲子沟通、主动联系学校心理老师等）；"
    "若中重度及以上或出现危机信号，必须明确、坚定地建议尽快由专业人员评估与干预，不得淡化风险。"
    "使用纯 Markdown，二级标题用 ##，条目用 1. 2. 3.，标题要贴合校园场景。"
)


async def _build_narrative(assessments: list, all_dims: list) -> str:
    """把量表总分与子维度一并传给 LLM 撰写「发展建议」。"""
    parts = []
    for a in assessments:
        parts.append(
            f"- {a['scale_name']}：总分 {a['total_score']}/{_scale_max_score(a['scale_id'])}，"
            f"严重度「{SEVERITY_LABEL.get(a['severity'], a['severity'])}」"
            + ("，触发危机升级（自杀意念/自伤）" if a.get("needs_crisis_escalation") else "")
        )
    dims_txt = "\n".join(
        f"  · {d['scale_name']}/{d['name']}：{d['raw_score']}/{d['max_score']}，"
        f"等级「{d['severity_label']}」"
        for d in all_dims
    )
    user_msg = f"本次评估结果：\n" + "\n".join(parts) + f"\n子维度剖析：\n{dims_txt}\n\n请撰写发展建议。"
    try:
        result = await provider.chat(
            role="report",
            messages=[
                {"role": "system", "content": REPORT_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=4000,  # deepseek-v4 有 reasoning_content 思考链，须留足余量
        )
        # 防止大模型返回空字符串/空白（不抛异常但实际为空），兜底生效
        if not result or not result.strip():
            raise RuntimeError("LLM returned empty narrative")
        return result
    except Exception:
        return (
            "## 发展建议（通用兜底）\n\n"
            "### 一、家庭端\n\n"
            "1. **倾听与陪伴优先**：请家长以非评判性的语气先承接情绪，避免立刻劝慰、"
            "评判或讲道理；每天留出 10-15 分钟「无手机一对一时间」，让孩子感受到被看见。\n"
            "2. **作息节奏护航**：保持基本固定的起床、用餐、就寝时间；限制睡前 1 小时使用电子屏幕，"
            "鼓励每天至少 30 分钟户外活动与运动。\n"
            "3. **家校协同**：与班主任、学校心理老师同步孩子近期状态；若出现自伤/自杀相关信号，"
            "必须立即介入并联系专业机构。\n\n"
            "### 二、学校端\n\n"
            "1. **课堂观察**：请班主任重点关注其课堂参与度、作业质量、同伴互动与出勤情况。\n"
            "2. **心理老师跟进**：建议学校心理老师在 1-3 个工作日内主动约谈，了解其主观感受与压力源，"
            "建立可信赖的求助通道。\n"
            "3. **危机信号处置**：若评估结果为中重度及以上，或出现自杀意念，请立即联系家长并"
            "按学校心理危机预案转介至专业医院/机构。\n\n"
            "### 三、自我调节\n\n"
            "1. **身体先稳**：每天 3-5 分钟腹式呼吸练习（吸气 4 秒 → 屏息 2 秒 → 呼气 6 秒），"
            "早晚各一次有助于降低生理应激水平。\n"
            "2. **书写表达**：情绪激动时可尝试「三行日记」（今天发生了什么？我感受到了什么？"
            "我需要什么支持？）把情绪外化出来。\n"
            "3. **求助不丢人**：若出现自伤或想结束生命的念头，请 **立刻** 拨打 12355 "
            "青少年心理援助热线；紧急危及生命时请拨 120 或 110。"
        )


# —— 工具函数 ——
def _scale_max_score(scale_id: str) -> int | None:
    scale = get_scale(scale_id)
    if not scale or not scale.items or not scale.options:
        return None
    return len(scale.items) * max(int(k) for k in scale.options.keys())


def _severity_for_pct(pct: float) -> str:
    """基于得分百分比给出子维度粗略等级（参考量表界值）。"""
    if pct < 0.30:
        return "none"
    if pct < 0.50:
        return "mild"
    if pct < 0.70:
        return "moderate"
    if pct < 0.85:
        return "moderately_severe"
    return "severe"


def _compute_subdims(assessment: dict) -> list:
    """为单个量表评估计算子维度。返回 [{scale_id, scale_name, name, desc, raw_score, max_score, pct, severity, interp}]"""
    sid = assessment["scale_id"]
    scale_name = assessment["scale_name"]
    answers = assessment.get("answers") or {}
    # answers 从 DB 出来是 {str(qid): int(val)}，强制转 int 键
    ans = {}
    for k, v in answers.items():
        try:
            ans[int(k)] = int(v)
        except (ValueError, TypeError):
            pass

    dims_cfg = []
    max_single = 0
    if sid == "phq_a":
        dims_cfg = PHQ_A_SUBDIMS
        max_single = PHQ_A_MAX_SINGLE
    elif sid == "scared":
        dims_cfg = SCARED_SUBDIMS
        max_single = SCARED_MAX_SINGLE
    elif sid == "sdq":
        dims_cfg = SDQ_SUBDIMS
        max_single = SDQ_MAX_SINGLE
        # SDQ 反向计分：7/11/14/21/25 = (max_single - 原始分)
        ans = {qid: (max_single - v) if qid in SDQ_REVERSE_IDS else v for qid, v in ans.items()}
    elif sid == "mht":
        dims_cfg = MHT_SUBDIMS
        max_single = MHT_MAX_SINGLE

    out = []
    for d in dims_cfg:
        raw = sum(ans.get(q, 0) for q in d["items"])
        mx = len(d["items"]) * max_single
        pct = 0 if mx == 0 else raw / mx
        # 优势维度（如亲社会）：高分=良好，反转 pct 使高分映射为低严重度（绿色）
        if d.get("inverse"):
            pct = 1 - pct
        sev = _severity_for_pct(pct)
        sev_label = SEVERITY_LABEL[sev]
        if sev in ("none",):
            level = "low"
        elif sev in ("mild", "moderate"):
            level = "mid"
        else:
            level = "high"
        out.append(
            {
                "scale_id": sid,
                "scale_name": scale_name,
                "name": d["name"],
                "desc": d["desc"],
                "raw_score": raw,
                "max_score": mx,
                "pct": int(pct * 100),
                "severity": sev,
                "severity_label": sev_label,
                "severity_color": SEVERITY_COLOR[sev],
                "interp": d["interps"][level],
            }
        )
    return out


def _overall_severity(assessments: list) -> str:
    worst = "none"
    for a in assessments:
        if SEVERITY_RANK.get(a["severity"], 0) > SEVERITY_RANK.get(worst, 0):
            worst = a["severity"]
    return worst


def _radar_chart(dims: list, cx: int = 240, cy: int = 170, r: int = 120) -> dict:
    """生成 9 因子 SVG 雷达图的几何数据，供模板直接拼装 <polygon>/<line>/<text>。

    返回：
      width/height: SVG viewBox 尺寸
      grid_rings: [points_str] — 4 层正多边形坐标（25/50/75/100%）
      axes:       [(x1,y1,x2,y2)] — n 条轴心线（中心→外圈）
      data_poly:  points_str — 得分多边形
      data_color: str — 取"最差因子"的严重度配色作为填充色
      data_fill:  str — rgba 半透明填充字符串（基于 data_color）
      labels:     [(name,x,y,anchor)] — 每个因子外围标签坐标 + text-anchor
      percent_texts: [(pct,x,y,color)] — 得分百分位数据点旁标
    """
    n = len(dims)
    if n < 3:
        # 不够 3 条轴就不画雷达图（模板里会判断 data_poly 空值）
        return {}
    angles = [2 * math.pi * i / n - math.pi / 2 for i in range(n)]
    ring_levels = [0.25, 0.50, 0.75, 1.00]
    grid_rings = []
    for k in ring_levels:
        grid_rings.append(
            " ".join(
                f"{cx + r * k * math.cos(a):.1f},{cy + r * k * math.sin(a):.1f}"
                for a in angles
            )
        )
    axes = [(cx, cy, cx + r * math.cos(a), cy + r * math.sin(a)) for a in angles]
    worst_dim = max(dims, key=lambda d: SEVERITY_RANK.get(d["severity"], 0))
    data_color = worst_dim["severity_color"]
    # 严重度配色 → 半透明 fill（按 #RRGGBB 估算 0.28 alpha）
    data_fill = f"{data_color}47"  # hex + '47' ≈ 0.28 opacity

    data_pts = []
    percent_texts = []
    for d, a in zip(dims, angles):
        p = d["pct"] / 100.0
        px = cx + r * p * math.cos(a)
        py = cy + r * p * math.sin(a)
        data_pts.append((px, py))
        # 数据点旁文字：稍微向外推 6px
        tx = cx + (r * p + 10) * math.cos(a)
        ty = cy + (r * p + 10) * math.sin(a)
        percent_texts.append((d["pct"], tx, ty, d["severity_color"]))
    data_poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in data_pts)
    data_dots = [(f"{x:.1f}", f"{y:.1f}") for x, y in data_pts]

    # 标签：外圈 +18 到 34 px，按方向选择 text-anchor
    labels = []
    lr_out = r + 26
    for d, a in zip(dims, angles):
        lx = cx + lr_out * math.cos(a)
        ly = cy + lr_out * math.sin(a)
        dx = math.cos(a)
        if dx < -0.35:
            anchor = "end"
        elif dx > 0.35:
            anchor = "start"
        else:
            anchor = "middle"
        labels.append((d["name"], lx, ly, anchor))

    # 轴刻度百分比文字：在每条轴 25/50/75/100% 位置画小刻度数字（仅 4 个）
    ring_labels = [
        (str(int(k * 100)) + "%", cx + 2, cy - r * k - 2) for k in ring_levels
    ]

    return {
        "width": 480,
        "height": 360,
        "cx": cx,
        "cy": cy,
        "grid_rings": grid_rings,
        "axes": axes,
        "data_poly": data_poly,
        "data_color": data_color,
        "data_fill": data_fill,
        "data_dots": data_dots,
        "labels": labels,
        "percent_texts": percent_texts,
        "ring_labels": ring_labels,
    }


def render_report_html(session, assessments: list, narrative_md: str) -> str:
    """参考 MHT 结构组装完整上下文 + Jinja2 渲染。"""
    has_crisis = any(a.get("needs_crisis_escalation") for a in assessments)

    # 1. 量表级渲染（含 severity 标色 + max）
    rendered_scales = []
    for a in assessments:
        rendered_scales.append(
            {
                **a,
                "severity_label": SEVERITY_LABEL.get(a["severity"], a["severity"]),
                "severity_color": SEVERITY_COLOR.get(a["severity"], "#475569"),
                "max_score": _scale_max_score(a["scale_id"]),
                "pct": 0
                if not _scale_max_score(a["scale_id"])
                else int(a["total_score"] / _scale_max_score(a["scale_id"]) * 100),
            }
        )

    # 2. 子维度（跨所有量表合并，顺序与 MHT 的 8 行因子表对齐）
    all_dims = []
    for a in assessments:
        all_dims.extend(_compute_subdims(a))

    # 3. 综合等级
    overall_sev = _overall_severity(assessments)
    overall = OVERALL_LEVEL[overall_sev]

    # 3b. 因子雷达图（如果有至少 3 个子维度）
    radar = _radar_chart(all_dims)

    # 4. 工具介绍拼接
    intro_parts = []
    for a in assessments:
        text = SCALE_INTRO.get(a["scale_id"])
        if text:
            intro_parts.append(text)
    scale_intro_html = "</p><p>".join(intro_parts) if intro_parts else "—"

    # 5. 量表简介条（页眉下方：姓名/机构/报告日期，对应 MHT 的首行）
    generated_at = datetime.utcnow().strftime("%Y-%m-%d")
    # 从 session.account.profile 读取用户真实信息（未登录/匿名时回退）
    account = getattr(session, "account", None)
    profile = getattr(account, "profile", None) or {}
    subject = profile.get("name") or getattr(account, "label", None) or "匿名"

    # 测评用时：session 创建到最新 assessment 提交的时长（单量表≈该量表做题用时；合并量表=两量表合计用时）
    duration_str = "—"
    assessments_orm = getattr(session, "assessments", None) or []
    if assessments_orm:
        try:
            latest_at = max(a.created_at for a in assessments_orm)
            total_sec = int((latest_at - session.created_at).total_seconds())
            if total_sec >= 0:
                duration_str = f"{total_sec // 60}分{total_sec % 60}秒" if total_sec >= 60 else f"{total_sec}秒"
        except Exception:
            pass

    # 6. 测评人员信息表（MHT 3 号章节的 10 字段 5 列 2 行结构）
    info_fields = [
        ("姓　名", subject),
        ("性　别", profile.get("gender") or "—"),
        ("年　龄", str(profile.get("age")) if profile.get("age") else "—"),
        ("编　号", getattr(session, "id", "")[:8]),
        ("测评用时", duration_str),
        ("学　号", profile.get("student_no") or "—"),
        ("年　级", profile.get("grade") or "—"),
        ("测评工具编号", getattr(session, "id", "")),
        ("部　门", profile.get("school") or "校园心理筛查（PsycheFlow）"),
        ("测评工具", " + ".join(a["scale_name"].split(" ")[0] for a in assessments)),
    ]

    # 7. 其他静态
    interpret_notes = INTERPRETATION_NOTES
    crisis_msg = crisis_message() if has_crisis else ""
    hotline = settings.crisis_hotline_12355

    template = _env.get_template("report.html")
    return template.render(
        # 页眉信息
        report_title="心理健康筛查综合报告",
        report_subtitle="个人评估报告",
        org_label="PsycheFlow 智能心理评估系统",
        generated_at=generated_at,
        generated_at_full=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        subject=subject,
        session_id=getattr(session, "id", ""),
        # 危机
        has_crisis=has_crisis,
        crisis_message=crisis_msg,
        hotline=hotline,
        # 1.工具介绍
        scale_intro_html=scale_intro_html,
        # 2.解读注意事项
        interpret_notes=interpret_notes,
        # 3.人员信息表
        info_fields=info_fields,
        # 4.综合等级
        overall=overall,
        # 4b.因子雷达图
        radar=radar,
        # 4c.尺度总览表
        scales=rendered_scales,
        # 4b.因子剖析表 + 5. 因子详解卡
        dims=all_dims,
        # 6.发展建议
        narrative_html=markdown(narrative_md) if narrative_md else "",
        # 页脚
        model_report=settings.model_report,
    )


async def generate_report_pdf(session, assessments: list) -> bytes:
    """完整链路：计算子维度 → 传给 LLM 写建议 → 渲染 → WeasyPrint 出 PDF。"""
    all_dims = []
    for a in assessments:
        all_dims.extend(_compute_subdims(a))
    narrative_md = await _build_narrative(assessments, all_dims)
    html_str = render_report_html(session, assessments, narrative_md)
    pdf_bytes = HTML(string=html_str).write_pdf()

    # —— 审计日志：报告生成 ——（不阻断返回，try/except 包死）
    try:
        from app.core.audit import write_report_audit

        # 构造 scores_dict: {scale_id: {score, severity, max_score, crisis_level}}
        scores_dict: dict = {}
        for a in assessments:
            sid = a.get("scale_id") if isinstance(a, dict) else getattr(a, "scale_id", None)
            if not sid:
                continue
            total_score = a.get("total_score") if isinstance(a, dict) else getattr(a, "total_score", 0)
            severity = a.get("severity") if isinstance(a, dict) else getattr(a, "severity", "none")
            crisis_level = a.get("crisis_level") if isinstance(a, dict) else getattr(a, "crisis_level", "")
            max_score = _scale_max_score(sid) or 0
            scores_dict[sid] = {
                "score": int(total_score) if total_score is not None else 0,
                "severity": severity,
                "max_score": int(max_score) if max_score is not None else 0,
                "crisis_level": crisis_level,
            }
        # has_crisis: 任一量表 crisis_level == "elevated" 或 needs_crisis_escalation
        def _a_crisis(a) -> bool:
            if isinstance(a, dict):
                return (
                    a.get("crisis_level") == "elevated"
                    or bool(a.get("needs_crisis_escalation"))
                )
            return (
                getattr(a, "crisis_level", "") == "elevated"
                or bool(getattr(a, "needs_crisis_escalation", False))
            )
        has_crisis = any(_a_crisis(a) for a in assessments)

        # narrative_len: 优先取 LLM 返回的发展建议原始长度（Markdown 文本）
        narrative_len = len(narrative_md) if narrative_md else 0

        session_id_val = getattr(session, "id", None)
        account_id_val = getattr(session, "account_id", None)
        write_report_audit(
            str(session_id_val) if session_id_val is not None else None,
            account_id_val if account_id_val else None,
            has_crisis,
            scores_dict,
            len(pdf_bytes),
            narrative_len,
        )
    except Exception as e:
        import logging

        logging.getLogger("psycheflow.audit").warning(
            "write_report_audit 失败: %s", str(e)
        )

    return pdf_bytes
