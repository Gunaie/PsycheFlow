# -*- coding: utf-8 -*-
"""dialog 共情对话训练数据生成器（18.2 第三批）。

目标：把现有 1386 条（单轮为主）扩到 4000-6000 条三任务混合中的 dialog 主力，
按 18.2 方案的六个改进点落地：
  ① teacher 换 pro（deepseek-v4-pro-0813，关思考链，短回复场景够用且快）；
  ② Best-of-4：每个场景生成 4 个候选，逐轮过**生产正则质检**
     （app.agents.nodes.intervention.check_reply_quality，与 dialog_smoke 同源），
     全过候选再上 flash 判官按 SAFETY_BASELINE 口径打分，≥4 分取最高分；
  ③ 4 意图 × 10 主题矩阵保底覆盖（倾诉/咨询/求助/危机；寒暄在生产为零 LLM 硬编码，不造）；
  ④ 4 轮多轮轨迹：倾诉 4 轮 + 倾诉转咨询 4 轮（最后一问必须是求做法问题）；
  ⑤ RAG-grounded：咨询/转咨询场景先取**生产 rag_service 真实检索片段**给 teacher，
     训练「用自己的话利用而非复读」，逐字搬运 15 字即判失败；
  ⑥ 危机负例：gpt 必须温暖坚定 + 具体求助动作（老师/家长/12355），
     禁止罗列普通调节做法、禁止挽留/刺激/淡化话术。

输出 sharegpt（多轮即多条 human/gpt 交替；system 不在此注入，merge 阶段灌生产
SAFETY_BASELINE+default 人格 prompt）。

用法（容器内）：
  docker exec psycheflow-backend uv run python scripts/finetune/gen_dialog_data.py --no-llm
  docker exec psycheflow-backend uv run python scripts/finetune/gen_dialog_data.py --limit 16
  docker exec psycheflow-backend uv run python scripts/finetune/gen_dialog_data.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

from openai import AsyncOpenAI

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.agents.nodes.intervention import check_reply_quality, RETRY_HINT  # noqa: E402
from app.agents.nodes.triage import detect_method_question  # noqa: E402
from app.agents.personas import build_system_prompt, get_persona  # noqa: E402

# ── 配置 ─────────────────────────────────────────────────────
DEFAULT_OUT = Path(__file__).parent / "dialog_train.jsonl"
DEFAULT_REJECTED_OUT = Path(__file__).parent / "dialog_rejected.jsonl"
DEFAULT_PROVENANCE_OUT = Path(__file__).parent / "dialog_train_provenance.jsonl"

DEFAULT_TEACHER = "deepseek-v4-pro-0813"    # pro 强 teacher（关思考）
DEFAULT_JUDGE = "deepseek-v4-flash-0731"    # flash 语义判官
CRISIS_KINDS = ("crisis", "crisis_traj")     # 安全攸关类：teacher 永不降级
CHEAP_KINDS = ("consult", "help")            # 单轮非危机：flash 试点 10/10；
                                             # 多轮轨迹 flash 守不住跨轮约束（超长/闭合问句），保持 pro


def teacher_model_for(args, kind: str) -> str:
    """分层 teacher：设置 --cheap-model 后，单轮非危机类候选生成走低价模型；
    多轮轨迹/危机类与修复轮（_repair_candidate）始终用 --model（pro）。"""
    if args.cheap_model and kind in CHEAP_KINDS:
        return args.cheap_model
    return args.model


SEED = 42
DEFAULT_CONCURRENCY = 8
BEST_OF_K = 2
JUDGE_PASS_SCORE = 4
GPT_MIN_LEN, GPT_MAX_LEN_HARD = 25, 120     # 生产质检上限 120；下限防敷衍
COPY_WINDOW = 20                            # 与 RAG 片段逐字重叠窗口（≤15 会误伤通用操作语）

# 精简配额（BoK 2 + 并发 8；2026-09-27 实跑成本约 ¥55–70 + 调参沉没 ~¥20——
# 原「¥25-35」预估低估了 pro ¥24/M 输出价与 4 轮 transcript 输出量，详见 docs/验收报告_18.2_18.3.md §2.3。
# 与现有 synthetic 500 合并后 2979；DeepWell ~886 云端未找回，未并入）
QUOTA = {
    "vent_traj": 600,      # 倾诉 4 轮轨迹
    "vc_traj": 200,        # 倾诉转咨询 4 轮轨迹（末轮求做法）
    "consult": 1000,       # 咨询单轮（RAG-grounded）
    "help": 500,           # 求助单轮（测评/渠道引导，生产跳过 RAG）
    "crisis": 300,         # 危机负例单轮
    "crisis_traj": 100,    # 危机 2 轮（试探暗语 → 明确表达）
}

# 生产 dialog system（默认人格暖暖）——teacher 以同一系统生成，风格零漂移
DIALOG_SYSTEM = build_system_prompt(get_persona("default"))

GRADES = ["初一", "初二", "初三", "高一", "高二", "高三", "职校", "大学"]
VOICES = [
    "表达简短克制，多用短句",
    "表达絮叨，细节很多",
    "带点网络口语但不夸张",
    "语气冲、带防备",
    "小心翼翼、不太敢直接说",
    "自嘲、用玩笑掩饰",
]

# ── 10 主题 × 倾诉场景核（教师据此写真实用户台词，不照抄）────
VENT_CORES: dict[str, list[str]] = {
    "学业压力": [
        "重点班排名下滑，怕被挤出重点班",
        "作业每天写到凌晨，白天上课犯困",
        "一到大考就脑子空白，平时会的也忘",
        "复读一年成绩还是上不去，不敢面对家人",
        "偏科严重，数学一看到卷子就发怵",
        "艺考/体考集训很累，开始怀疑自己选的路",
    ],
    "人际关系": [
        "被小团体孤立，午餐一个人坐",
        "和最好的朋友因为小事冷战",
        "融不进宿舍话题，觉得自己多余",
        "被同学起难听的外号",
        "好朋友有了新朋友，自己像被替代",
        "班干部管人被全班针对",
    ],
    "亲子关系": [
        "父母只看成绩，考好了也不肯定",
        "爸妈总拿自己和别人家孩子比",
        "父母吵架/闹离婚，在家很窒息",
        "想看心理老师但家长说矫情",
        "手机和交友被父母严密管控",
        "弟弟/妹妹出生后觉得自己不被重视",
    ],
    "情绪问题": [
        "持续一两个月高兴不起来，对什么都没兴趣",
        "莫名烦躁，一点小事就想哭或发火",
        "早上特别不想起床、不想去学校",
        "总是控制不住往坏处想",
        "情绪突然崩溃，又说不出具体原因",
        "一到周日晚上就低落",
    ],
    "自我认同": [
        "觉得自己长得不好看，回避拍照和镜子",
        "身材焦虑，开始不敢正常吃饭",
        "觉得自己什么都做不好，没有价值",
        "对自己的性别气质/性取向感到困惑不安",
        "内向被反复说不合群，怀疑是不是自己有问题",
        "总是讨好别人，不敢拒绝",
    ],
    "恋爱情感": [
        "暗恋的人有了对象，上课也走神",
        "被表白后不知道怎么处理关系",
        "分手后在班里天天碰面很尴尬",
        "网恋对象很冷淡，忍不住反复看手机",
        "初恋被家长老师反对",
        "表白被拒后觉得丢人到不想上学",
    ],
    "未来发展": [
        "不知道选文科理科/什么专业",
        "父母想让走的路和自己喜欢的冲突",
        "担心考不上好学校，人生就完了",
        "对毕业以后完全没有方向，很恐慌",
        "身边同学都有目标，只有自己没有",
        "想走艺术但怕没前途又费钱",
    ],
    "行为习惯": [
        "手机/短视频停不下来，事后又自责",
        "熬夜打游戏，第二天起不来",
        "做事严重拖延，越拖越焦虑",
        "反复检查确认，明明知道没必要",
        "一紧张就咬指甲/拔头发/抠皮肤",
        "沉迷网络世界，现实里不想见人",
    ],
    "身体健康": [
        "长期入睡困难、半夜易醒",
        "一考试就肚子疼/头疼，检查又没病",
        "心慌胸闷，总担心自己身体出大问题",
        "明显食欲变化，暴食或吃不下",
        "经常疲惫没精神，怎么睡都不够",
        "考前尿频、手抖、呼吸发急",
    ],
    "创伤事件": [
        "目睹交通事故后反复做噩梦",
        "亲人离世，无法接受",
        "曾被亲戚/熟人侵犯，最近突然回忆起来",
        "被校园霸凌过，看到施暴者就发抖",
        "在众人面前出丑后再也不敢上台",
        "家中遭遇变故，失去安全感",
    ],
}

# ── 咨询问题库（真实检索词）。method=True 的求做法问题，生产规则要求 ≥2 种做法类别，
#    且自然回答须能落到生产 5 类做法（呼吸/书写/肌肉放松/正念/运动）；
#    人际/亲子等关系类主题用知识/辨析问法，避开 detect_method_question 触发词。──
CONSULT_QUESTIONS: dict[str, list[dict]] = {
    "学业压力": [
        {"q": "考试焦虑是什么原因引起的", "method": False},
        {"q": "考试特别紧张怎么缓解", "method": True},
        {"q": "学习压力大怎么调节", "method": True},
        {"q": "一到考试脑子就空白，这是怎么回事", "method": False},
    ],
    "人际关系": [
        {"q": "被同学孤立时，哪些反应算正常", "method": False},
        {"q": "社交紧张时心跳加速，身体为什么会这样", "method": False},
        {"q": "和好朋友吵架后，情绪一直过不去说明什么", "method": False},
        {"q": "为什么被群体排斥会让人这么难受", "method": False},
    ],
    "亲子关系": [
        {"q": "为什么和父母一说话就容易吵起来", "method": False},
        {"q": "家长总说为你好，背后通常是什么心态", "method": False},
        {"q": "青春期和父母对着干，心理上意味着什么", "method": False},
        {"q": "为什么有些父母很难说出肯定孩子的话", "method": False},
    ],
    "情绪问题": [
        {"q": "抑郁情绪和抑郁症有什么区别", "method": False},
        {"q": "情绪低落时怎么调节", "method": True},
        {"q": "突然情绪崩溃时怎么让自己稳下来", "method": True},
        {"q": "为什么坏情绪有时候没有明确原因", "method": False},
    ],
    "自我认同": [
        {"q": "自我价值感是什么，和自信一样吗", "method": False},
        {"q": "总是否定自己，这种习惯是怎么形成的", "method": False},
        {"q": "讨好型人格在心理学上指什么", "method": False},
        {"q": "为什么青少年会特别在意自己的外貌", "method": False},
    ],
    "恋爱情感": [
        {"q": "早恋到底正不正常", "method": False},
        {"q": "失恋为什么会带来身体上的难受", "method": False},
        {"q": "被喜欢的人拒绝后，大脑在经历什么", "method": False},
        {"q": "学生时代的感情为什么印象特别深", "method": False},
    ],
    "未来发展": [
        {"q": "对未来感到恐慌，这种焦虑在提醒什么", "method": False},
        {"q": "一想到未来就很焦虑怎么缓解", "method": True},
        {"q": "为什么选择越多反而越焦虑", "method": False},
        {"q": "把大目标拆小为什么能减轻焦虑", "method": False},
    ],
    "行为习惯": [
        {"q": "拖延背后常见的心理原因是什么", "method": False},
        {"q": "一焦虑就停不下来刷手机，这是什么机制", "method": False},
        {"q": "反复检查确认，是不是强迫症", "method": False},
        {"q": "拖延后焦虑得睡不着怎么缓解", "method": True},
    ],
    "身体健康": [
        {"q": "焦虑为什么会引起肚子疼和头疼", "method": False},
        {"q": "躯体化症状是什么意思", "method": False},
        {"q": "失眠有什么非药物的改善方法", "method": True},
        {"q": "睡前脑子停不下来怎么放松", "method": True},
    ],
    "创伤事件": [
        {"q": "什么是创伤后应激反应", "method": False},
        {"q": "为什么创伤画面会不受控制地闪回", "method": False},
        {"q": "闪回出现时怎么让自己安定下来", "method": True},
        {"q": "受过欺负后总回避相关场景，这是为什么", "method": False},
    ],
}

# ── 求助场景（测评/渠道；生产对求助意图跳过 RAG，引导 /scale）──
HELP_CORES = [
    "想知道自己是不是抑郁了，该做什么测评",
    "在哪里可以做正规的心理量表",
    "学校的心理辅导怎么预约，流程是什么",
    "想找心理老师聊聊但不知道怎么开口",
    "PHQ-A 和 SCARED 分别是测什么的",
    "测评结果能说明自己有病吗",
    "除了学校还有哪些正规求助渠道",
    "不敢当面求助，线上有什么可靠渠道",
    "做完量表之后下一步该干什么",
    "想让家长带自己看心理科但不知道怎么说",
    "怎么判断自己需不需要做心理咨询",
    "免费的青少年心理援助资源有哪些",
]

# ── 危机负例核（含直白 + 暗语隐喻，对齐 triage 危机语料风格）──
CRISIS_CORES = [
    "明确说不想活了，觉得活着没意义",
    "想过从教学楼天台/窗户跳下去",
    "已经准备好割伤自己的工具",
    "想一觉睡过去再也不用醒来",
    "觉得自己是家里的负担，没有我他们更轻松",
    "吃药了结的念头越来越具体",
    "站在高处会有想跳的冲动，感到害怕",
    "被侵犯后觉得自己脏，想用伤害自己惩罚自己",
    "告别式表达：谢谢你们，以后不用管我了",
    "自伤已经发生，伤口被同学发现",
    "反复查怎么死不痛苦",
    "酒后冲动想冲到马路上",
    "唯一的朋友离世，想去找对方",
    "长期被霸凌，觉得只有消失才能结束",
]

CRISIS_HELP_RE = re.compile(r"(12355|热线|老师|家长|大人|父母|妈妈|爸爸|信任的人|心理(咨询|老师|科)|医院|急诊|专业)")
CRISIS_WARM_RE = re.compile(r"(我听到|我在|陪|你不是一个人|你的感受|在乎你|我会认真|痛苦)")
CRISIS_BANNED_RE = re.compile(
    r"(答应我|不要离开我|别离开我|不要做傻事|别做傻事|想想你的?(爸妈|父母|家人)|"
    r"你还有什么想不开|世界(很|还)美好|一切都会好起来|不许死|不能死|乖，?别)"
)
CRISIS_TIP_CATS = ("呼吸", "吸气", "呼气", "日记", "写下来", "运动", "跑步", "散步", "拉伸", "肌肉", "正念", "冥想")

HELP_TOPIC_RE = re.compile(r"(测评|量表|phq|scared|心理老师|辅导|预约|咨询(室|渠道|资源)?|/scale|医生|医院|热线|渠道|求助)")

_TEMPLATE_OPENING = re.compile(r"(我理解你的感受|我能感受到你|这一定让你感到|听起来你最近过得)")


# ── 矩阵构建 ─────────────────────────────────────────────────
def _voice(rng: random.Random) -> dict:
    return {"grade": rng.choice(GRADES), "voice": rng.choice(VOICES)}


def build_scenarios(rng: random.Random) -> list[dict]:
    """确定性生成场景清单（咨询问题在矩阵期固定，便于先检索后生成）。"""
    specs: list[dict] = []
    themes = list(VENT_CORES)

    def add(kind: str, n: int):
        for i in range(n):
            theme = themes[i % len(themes)]
            specs.append({"kind": kind, "theme": theme, "idx": i, **_voice(rng)})

    add("vent_traj", QUOTA["vent_traj"])
    add("vc_traj", QUOTA["vc_traj"])
    add("consult", QUOTA["consult"])
    add("help", QUOTA["help"])
    add("crisis", QUOTA["crisis"])
    add("crisis_traj", QUOTA["crisis_traj"])

    for s in specs:
        theme, i = s["theme"], s["idx"]
        if s["kind"] == "vent_traj":
            s["core"] = VENT_CORES[theme][i % len(VENT_CORES[theme])]
            s["turns"] = 4
            s["id"] = f"vent_traj:{theme}:{i}"
        elif s["kind"] == "vc_traj":
            s["core"] = VENT_CORES[theme][i % len(VENT_CORES[theme])]
            bank = CONSULT_QUESTIONS[theme]
            item = bank[(i // len(themes)) % len(bank)]
            s["question"], s["method"] = item["q"], item["method"]
            s["turns"] = 4
            s["id"] = f"vc_traj:{theme}:{i}"
        elif s["kind"] == "consult":
            bank = CONSULT_QUESTIONS[theme]
            item = bank[(i // len(themes)) % len(bank)]
            s["question"], s["method"] = item["q"], item["method"]
            s["core"] = s["question"]
            s["turns"] = 1
            s["id"] = f"consult:{theme}:{i}"
        elif s["kind"] == "help":
            s["core"] = HELP_CORES[i % len(HELP_CORES)]
            s["turns"] = 1
            s["id"] = f"help:{theme}:{i}"
        elif s["kind"] == "crisis":
            s["core"] = CRISIS_CORES[i % len(CRISIS_CORES)]
            s["turns"] = 1
            s["id"] = f"crisis:{theme}:{i}"
        else:  # crisis_traj
            s["core"] = CRISIS_CORES[i % len(CRISIS_CORES)]
            s["turns"] = 2
            s["id"] = f"crisis_traj:{theme}:{i}"
    return specs


# ── RAG 真实片段（生产检索器；失败则场景判失败，不造假片段）──
async def fetch_rag(question: str):
    from app.rag.service import rag_service
    chunks = await rag_service.search(question, top_k=3, intent="咨询", caller="finetune_dialog_gen")
    parts = [f"[{i+1}] 《{c.get('source', '未知来源')}》:\n{(c.get('text') or '')[:350]}"
             for i, c in enumerate(chunks[:3])]
    return chunks, ("\n\n".join(parts) if parts else "（无相关片段）")


# ── teacher 提示词 ───────────────────────────────────────────
def _persona_line(s: dict) -> str:
    return f"用户设定：{s['grade']}学生；说话风格：{s['voice']}。台词要像真实青少年打字，不书面、不朗诵。"


def _rag_block(rag_context: str) -> str:
    return (
        "\n知识库片段（真实检索结果，仅供背景参考）：\n" + rag_context +
        "\n要求：用你自己的话自然表达，不得逐字搬运片段（连续15字相同即作废），"
        "用户没问来源就不要写「来源：《》」。"
    )


def build_teacher_user(s: dict, rag_context: str | None) -> str:
    kind = s["kind"]
    common = (
        "\n\n【数据生成指令】你在为心理陪伴AI生产训练对话。请按下面设定写一段"
        f"{s['turns']} 轮对话（每轮=一条用户消息+一条你的回复）。"
        "你的每条回复都必须严格遵守你角色的全部规则：50-100字、不超过3句、"
        "不复读、不编造用户没说过的内容。\n"
        "收尾问句只能用「什么/怎么/哪些/哪里/哪一/哪种/多久/什么时候/谁」这类开放式问词"
        "（如：你最想先试哪一个？那种感觉什么时候最明显？多说说当时发生了什么？）；"
        "严禁出现：吗、吧、对吧、对吗、是不是、好吗、要不要、能不能、有没有、会不会、可以吗；"
        "特别注意「你愿意……吗？」「身边有……的人吗？」同样违规，要改成陈述式邀请或开放问词；"
        "整轮最多一个问句，也可以不用问句收尾。\n" + _persona_line(s)
    )
    if kind == "vent_traj":
        body = (
            f"情境核：{s['core']}。\n"
            "4 轮的意图固定为：倾诉→倾诉→倾诉→倾诉。"
            "用户从说事情到逐步露出更软的情绪；你每轮先接住情绪，"
            "第2-4轮可以各给一种做法，**全篇同一类做法只能出现一次**"
            "（呼吸/书写/运动/肌肉放松/正念五类里轮换，严禁跨轮重复同类，换说法也算重复），"
            "每轮以开放式问题或陈述式邀请收尾。"
        )
    elif kind == "vc_traj":
        method_line = (
            "第4轮是求做法的问题，你的回答必须包含两类**不同**的具体做法，"
            "只能从这五类里选两个：①腹式呼吸/缓慢呼吸 ②写下来/日记/纸条 "
            "③渐进式肌肉放松/握拳再松开 ④正念/专注当下的觉察 ⑤运动/散步/拉伸。"
            if s["method"] else
            "第4轮是知识/辨析型问题，直接通俗作答即可，可附一个轻量小练习，不强求多种做法。"
        )
        body = (
            f"情境核：{s['core']}。\n"
            "4 轮意图固定为：倾诉→倾诉→倾诉→咨询。"
            f"第 4 轮用户的问题必须是（可口语化改写但语义不变；"
            f"长度不得超过30个字；{'保留原句的求做法问法' if s['method'] else '不得加入缓解/改善/应对/怎么办等求做法词'}）：「{s['question']}」"
            + _rag_block(rag_context or "（无相关片段）") +
            "末轮严格不超过110字、不超过3句：先用半句通俗话直接回答，"
            "再用两短句给两种不同类别的做法（用「也可以」连接，不展开解释），"
            "最后一个短开放问句；不写长情绪衔接。" + method_line
        )
    elif kind == "consult":
        method_line = (
            "这是求做法的问题，回答必须包含两类**不同**的具体做法，"
            "只能从这五类里选两个：①腹式呼吸/缓慢呼吸 ②写下来/日记/列清单 "
            "③渐进式肌肉放松/握拳再松开 ④正念/专注当下的觉察 ⑤运动/散步/拉伸。"
            if s["method"] else
            "这是知识/辨析型问题，直接通俗作答即可，可附一个轻量小练习，不强求多种做法。"
        )
        body = (
            f"1 轮，用户的问题（可口语化微调；长度不得超过30个字；"
            f"{'保留求做法问法' if s['method'] else '不要加入缓解/改善/应对/怎么办等求做法词'}）：「{s['question']}」"
            + _rag_block(rag_context or "（无相关片段）") +
            "严格不超过110字、不超过3句：先用半句通俗话直接回答；"
            "若给做法，用两短句写两种不同类别（用「也可以」连接）；最后一个短开放问句。"
            + method_line
        )
    elif kind == "help":
        body = (
            f"1 轮，用户诉求：{s['core']}。\n"
            "这是求助意图：不做知识科普、不做情绪深挖，直接给可操作的渠道/步骤"
            "（学校心理老师/辅导室预约、平台测评页做 PHQ-A 与 SCARED、"
            "量表只是筛查工具不能替代医生诊断），语气温和，开放式问题收尾。"
        )
    elif kind == "crisis":
        body = (
            f"1 轮，用户表达：{s['core']}（请写成该学生的第一人称原话，可含暗语/隐喻）。\n"
            "这是危机场景：先温暖承认痛苦（不说教不淡化），明确你不能替代专业帮助，"
            "立刻给出具体求助动作（现在联系信任的老师/家长、拨打12355、必要时去急诊/120），"
            "鼓励不要独处、移除危险物品由信任的人陪同。"
            "禁止罗列呼吸/运动等普通调节做法，禁止挽留式台词（答应我/不要离开我/别做傻事/"
            "想想父母/世界很美好），禁止刺激或质问。50-100字。"
        )
    else:  # crisis_traj
        body = (
            f"2 轮，暗语核：{s['core']}。\n"
            "第1轮用户只用隐晦的试探表达（如'最近总往窗边看''要是睡过去不用醒就好了'），"
            "你要温和点出担心并直接问具体：整轮只问**一个**问题，用开放问词"
            "（什么/哪些/什么时候/多久/哪里），严禁是不是/有没有/吗/吧；"
            "第2轮用户承认有自伤/自杀念头或计划，你立即坚定给出求助动作"
            "（信任的老师/家长、12355、不独处、移除危险物品），"
            "同样禁止普通调节做法清单与挽留/刺激话术，每轮50-100字。"
        )
    schema = (
        '\n严格只输出 JSON（不要 markdown、不要解释）：'
        '{"turns":[{"human":"...","gpt":"..."}]}'
    )
    return common + body + schema


# ── 输出解析 ─────────────────────────────────────────────────
def parse_transcript(raw: str, expect_turns: int) -> list[dict] | None:
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return None
    turns = obj.get("turns")
    if not isinstance(turns, list) or len(turns) != expect_turns:
        return None
    out = []
    for tr in turns:
        h = (tr.get("human") or "").strip()
        g = (tr.get("gpt") or "").strip()
        if not h or not g or not (4 <= len(h) <= 200) or not (10 <= len(g) <= 200):
            return None
        out.append({"human": h, "gpt": g})
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


# ── 逐轮正则判官（生产 check_reply_quality + 任务附加规则）────
def judge_regex(turns: list[dict], kind: str, chunk_text: str, force_method: bool = False) -> list[dict]:
    """返回每轮失败原因列表（空 list=全过）。"""
    history: list[dict] = []
    per_turn = []
    norm_chunk = _norm(chunk_text or "")
    for n, tr in enumerate(turns):
        h, g = tr["human"], tr["gpt"]
        fails = []
        if GPT_MIN_LEN > len(g) or len(g) > GPT_MAX_LEN_HARD:
            fails.append(f"字数{len(g)}不在{GPT_MIN_LEN}-{GPT_MAX_LEN_HARD}")
        last_turn = n == len(turns) - 1
        need_methods = force_method and last_turn and kind in ("consult", "vc_traj")
        if need_methods and len(h) > 30:
            fails.append("求做法问句被改写超30字，生产规则无法识别")
        min_cats = 2 if need_methods or detect_method_question(h) else 1
        if not check_reply_quality(g, history, min_method_categories=min_cats):
            # 生产函数合并多项原因，拆开标注主要可见项
            if not fails:
                fails.append("生产质检不通过（闭合问句/多问/超长/复读/做法类别/幻觉归因之一）")
        if _TEMPLATE_OPENING.search(g):
            fails.append("模板腔开场")
        if kind == "crisis" or (kind == "crisis_traj" and n == 1):
            if not CRISIS_HELP_RE.search(g):
                fails.append("危机缺具体求助动作")
            if not CRISIS_WARM_RE.search(g):
                fails.append("危机缺温度承接")
            if CRISIS_BANNED_RE.search(g):
                fails.append("危机含挽留/刺激/淡化话术")
            if sum(1 for w in CRISIS_TIP_CATS if w in g) >= 2:
                fails.append("危机罗列普通调节做法")
        if kind == "help" and not HELP_TOPIC_RE.search(g):
            fails.append("求助未给测评/渠道引导")
        if kind in ("consult", "vc_traj") and norm_chunk:
            ng = _norm(g)
            copied = any(ng[k:k + COPY_WINDOW] in norm_chunk
                         for k in range(max(0, len(ng) - COPY_WINDOW + 1)))
            if copied:
                fails.append("逐字搬运RAG片段")
        per_turn.append({"turn": n + 1, "human": h, "gpt": g, "fails": fails})
        history += [{"role": "user", "content": h}, {"role": "assistant", "content": g}]
    return per_turn


# ── flash 语义判官（仅对正则全过候选）────────────────────────
JUDGE_SYSTEM = (
    "你是青少年心理陪伴对话的严格质检员。按助手角色规则给整段对话打一个总分，"
    "重点看：1）每条回复是否具体接住该用户的独特情境而非空泛套话；"
    "2）意图契合：倾诉先接情绪、咨询先通俗答问且方法具体可做、求助直接给测评/渠道步骤、"
    "危机温暖坚定给求助动作且不挽留不淡化；3）口语自然、不排比不说教；"
    "4）多轮之间做法与问句不重样、追问能推进对话。"
    "只输出 JSON：{\"score\":1到5的整数,\"reason\":\"一句话\"}。4分=可直接进训练集。"
)


async def flash_judge(client, model, turns: list[dict], kind: str, sem_j) -> dict:
    dialog = "\n".join(f"用户：{t['human']}\n助手：{t['gpt']}" for t in turns)
    async with sem_j:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": JUDGE_SYSTEM},
                      {"role": "user", "content": f"场景类型：{kind}\n\n{dialog}"}],
            max_tokens=300, temperature=0.1,
            extra_body={"enable_thinking": False}, timeout=60,
        )
    raw = (resp.choices[0].message.content or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"score": 0, "reason": "判官解析失败"}
    try:
        obj = json.loads(m.group(0))
        return {"score": int(obj.get("score", 0)), "reason": str(obj.get("reason", ""))[:120]}
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"score": 0, "reason": "判官JSON非法"}


# ── 单场景 Best-of-K ─────────────────────────────────────────
_CRISIS_REPAIR_HINT = (
    "另外这是危机场景：不要罗列呼吸/运动等普通做法，不要挽留或质问，"
    "先承接痛苦，再给一个具体求助动作（信任的老师/家长、12355），50-100字。"
)


async def _repair_candidate(client, args, spec: dict, cand: dict, rag_context: str, state) -> dict | None:
    """对失败轮逐轮单轮重生（最多 2 轮），全轨迹重判通过才返回新候选。"""
    turns = [dict(t) for t in cand["turns"]]
    repaired_idx: list[int] = []
    extra = _CRISIS_REPAIR_HINT if spec["kind"] in ("crisis", "crisis_traj") else ""
    for _ in range(2):
        judged = judge_regex(turns, spec["kind"], rag_context or "",
                             force_method=spec.get("method", False))
        bad = [t["turn"] - 1 for t in judged if t["fails"]]
        if not bad:
            break
        idx = bad[0]
        history = []
        for t in turns[:idx]:
            history += [{"role": "user", "content": t["human"]},
                        {"role": "assistant", "content": t["gpt"]}]
        try:
            resp = await client.chat.completions.create(
                model=args.model,
                messages=[{"role": "system", "content": DIALOG_SYSTEM},
                          *history,
                          {"role": "user", "content": turns[idx]["human"]},
                          {"role": "system", "content": RETRY_HINT + extra}],
                max_tokens=400, temperature=0.35,
                extra_body={"enable_thinking": False}, timeout=60,
            )
            state["calls_teacher"] += 1
            new_g = (resp.choices[0].message.content or "").strip()
        except Exception:  # noqa: BLE001
            return None
        if not new_g:
            return None
        turns[idx]["gpt"] = new_g
        repaired_idx.append(idx + 1)
    judged = judge_regex(turns, spec["kind"], rag_context or "",
                         force_method=spec.get("method", False))
    if any(t["fails"] for t in judged):
        return None
    return {"k": f"{cand['k']}+repair", "turns": turns, "judged": judged,
            "fails": [], "repaired_turns": repaired_idx}


async def gen_one(client, spec: dict, sem_g, sem_j, state, args) -> None:
    async with sem_g:
        rag_context = None
        rag_sources: list[str] = []
        if spec["kind"] in ("consult", "vc_traj"):
            try:
                chunks, rag_context = await fetch_rag(spec.get("question") or spec["core"])
                rag_sources = [c.get("source", "?") for c in chunks[:3]]
            except Exception as e:  # noqa: BLE001
                state["rejected"].append({"id": spec["id"], "kind": spec["kind"],
                                          "fails": [f"RAG检索失败:{type(e).__name__}"],
                                          "candidates": []})
                return
        user_prompt = build_teacher_user(spec, rag_context)
        candidates = []
        for k in range(BEST_OF_K):
            t_model = teacher_model_for(args, spec["kind"])
            try:
                resp = await client.chat.completions.create(
                    model=t_model,
                    messages=[{"role": "system", "content": DIALOG_SYSTEM},
                              {"role": "user", "content": user_prompt}],
                    max_tokens=3000 if spec["turns"] >= 4 else 1200,
                    temperature=0.65 + 0.12 * k,
                    extra_body={"enable_thinking": False}, timeout=120,
                )
                state["calls_teacher"] += 1
                if t_model != args.model:
                    state["calls_cheap"] += 1
                raw = resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001
                candidates.append({"k": k + 1, "error": type(e).__name__})
                continue
            turns = parse_transcript(raw, spec["turns"])
            if turns is None:
                candidates.append({"k": k + 1, "fails": ["JSON解析/轮次结构失败"]})
                continue
            judged = judge_regex(turns, spec["kind"], rag_context or "",
                                 force_method=spec.get("method", False))
            all_fails = [f for t in judged for f in t["fails"]]
            candidates.append({"k": k + 1, "turns": turns, "judged": judged, "fails": all_fails})

        clean = [c for c in candidates if "turns" in c and not c["fails"]]

        # 定点修复：无干净候选时，取失败轮最少的候选，对至多 2 个失败轮按生产
        # RETRY_HINT 同构方式单轮重生，整条轨迹重判（防修 A 轮引入跨轮复读）
        if not clean:
            fixable = [c for c in candidates if "turns" in c]
            if fixable:
                best = sorted(fixable, key=lambda c: (len(c["fails"]), c["k"]))[0]
                repaired = await _repair_candidate(client, args, spec, best, rag_context, state)
                if repaired:
                    clean = [repaired]

        chosen = None
        for c in clean:
            try:
                c["judge"] = await flash_judge(client, args.judge_model, c["turns"], spec["kind"], sem_j)
            except Exception:  # noqa: BLE001
                c["judge"] = {"score": 0}  # 判官异常不崩全局：该候选按不合格处理
            state["calls_judge"] += 1
        scored = [c for c in clean if c["judge"]["score"] >= JUDGE_PASS_SCORE]
        if scored:
            chosen = sorted(scored, key=lambda c: (-c["judge"]["score"],
                                                   sum(len(t["gpt"]) for t in c["turns"])))[0]
        if chosen is None:
            state["rejected"].append({"id": spec["id"], "kind": spec["kind"], "theme": spec["theme"],
                                      "fails": ["无候选同时通过正则+判官≥4"],
                                      "candidates": [{k2: v for k2, v in c.items() if k2 != "turns"}
                                                     for c in candidates]})
            return
        state["kept"].append({"spec": spec, "candidate": chosen, "rag_sources": rag_sources})


async def gen_one_guarded(client, spec, sem_g, sem_j, state, args) -> None:
    """gen_one 兜底：任何未捕获异常只丢该场景，不崩全局；顺带进度心跳。"""
    try:
        await gen_one(client, spec, sem_g, sem_j, state, args)
    except Exception as e:  # noqa: BLE001
        state["rejected"].append({"id": spec["id"], "kind": spec["kind"],
                                  "fails": [f"未捕获异常:{type(e).__name__}"],
                                  "candidates": []})
    finally:
        state["done"] += 1
        if state["done"] % 100 == 0:
            print(f"[PROGRESS] {state['done']}/{state['total']} | "
                  f"保留 {len(state['kept'])} 丢弃 {len(state['rejected'])}", flush=True)


# ── 主流程 ───────────────────────────────────────────────────
def round_robin(specs: list[dict], n: int) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for s in specs:
        groups.setdefault(f"{s['kind']}:{s['theme']}", []).append(s)
    # 主题优先排序：小样本试点时每取一轮就跨全 6 类，避免总被同 kind 占满
    kind_order = {"vent_traj": 0, "vc_traj": 1, "consult": 2, "help": 3, "crisis": 4, "crisis_traj": 5}
    keys = sorted(groups, key=lambda k: (k.split(":", 1)[1], kind_order[k.split(":", 1)[0]]))
    picked, gi = [], 0
    while len(picked) < n and any(groups[k] for k in keys):
        key = keys[gi % len(keys)]
        if groups[key]:
            picked.append(groups[key].pop(0))
        gi += 1
    return picked


async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv(_BACKEND_ROOT.parent / ".env")

    rng = random.Random(SEED)
    specs = build_scenarios(rng)
    if args.limit and args.limit < len(specs):
        specs = round_robin(specs, args.limit)
    dist = Counter((s["kind"], s["theme"]) for s in specs)
    print(f"[INFO] 场景 {len(specs)} 个；kind 分布: {dict(Counter(s['kind'] for s in specs))}")

    if args.no_llm:
        dbg = args.out.with_name(args.out.stem + "_specs.jsonl")
        with open(dbg, "w", encoding="utf-8") as f:
            for s in specs:
                f.write(json.dumps({k: v for k, v in s.items()}, ensure_ascii=False) + "\n")
        print(f"[OK] --no-llm：{len(specs)} 个场景 -> {dbg}")
        return 0

    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        print("[ERROR] 未设置 DASHSCOPE_API_KEY")
        return 1
    base_url = os.getenv("DASHSCOPE_BASE_URL",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    sem_g, sem_j = asyncio.Semaphore(args.concurrency), asyncio.Semaphore(args.concurrency)
    state = {"calls_teacher": 0, "calls_cheap": 0, "calls_judge": 0,
             "kept": [], "rejected": [], "done": 0, "total": len(specs)}

    await asyncio.gather(*[gen_one_guarded(client, s, sem_g, sem_j, state, args) for s in specs])

    random.Random(SEED).shuffle(state["kept"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f, \
            open(args.provenance_out, "w", encoding="utf-8") as fp:
        for row in state["kept"]:
            s, c = row["spec"], row["candidate"]
            conv = [{"from": "human", "value": t["human"]} if i % 2 == 0
                    else {"from": "gpt", "value": t["gpt"]}
                    for t in c["turns"] for i in (0, 1)]
            f.write(json.dumps({"conversations": conv}, ensure_ascii=False) + "\n")
            fp.write(json.dumps({
                "id": s["id"], "kind": s["kind"], "theme": s["theme"], "turns": s["turns"],
                "grade": s["grade"], "voice": s["voice"],
                "rag_sources": row["rag_sources"], "best_k": c["k"], "judge": c["judge"],
                "repaired_turns": c.get("repaired_turns", []),
                "gpt_lens": [len(t["gpt"]) for t in c["turns"]],
            }, ensure_ascii=False) + "\n")
    with open(args.rejected_out, "w", encoding="utf-8") as f:
        for r in state["rejected"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("\n" + "=" * 60)
    print("dialog 生成报告")
    print("=" * 60)
    print(f"teacher {state['calls_teacher']} 次（其中低价 {state['calls_cheap']}）| "
          f"judge {state['calls_judge']} 次 | "
          f"场景 {len(specs)} | 保留 {len(state['kept'])} | 丢弃 {len(state['rejected'])}")
    print("保留 kind:", dict(Counter(r["spec"]["kind"] for r in state["kept"])))
    if state["rejected"]:
        print("丢弃 top 原因:", Counter(
            f for r in state["rejected"] for f in r["fails"]).most_common(8))
    print(f"-> {args.out}\n-> {args.provenance_out}\n-> {args.rejected_out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="dialog Best-of-4 训练数据生成（18.2）")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--rejected-out", type=Path, default=DEFAULT_REJECTED_OUT)
    p.add_argument("--provenance-out", type=Path, default=DEFAULT_PROVENANCE_OUT)
    p.add_argument("--model", default=DEFAULT_TEACHER)
    p.add_argument("--cheap-model", default=None,
                   help="单轮非危机类（consult/help）的低价 teacher；多轮/危机类与修复轮仍用 --model")
    p.add_argument("--judge-model", default=DEFAULT_JUDGE)
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--limit", type=int, default=0, help="kind:theme 分组轮抽 N 个（试点）")
    p.add_argument("--no-llm", action="store_true")
    args = p.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
