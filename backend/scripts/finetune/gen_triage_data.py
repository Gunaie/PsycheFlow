# -*- coding: utf-8 -*-
"""triage 意图分类训练数据批量生成（18.2 第三批，triage 先行）。

目标：0 → 800–1000 条，四类（倾诉/咨询/求助/危机）各 ≥200，另含两类硬负例桶。
安全/规则一致性是第一约束——生产链路 detect_crisis / detect_greeting /
detect_method_question 三层零 LLM 前置与训练标签必须在数据中一致，否则模型学会
和规则打架：

  桶设计（默认配额合计 1000）
  ────────────────────────────────────────────────────────────────
  vent                  倾诉 220  表达情绪/压力，无明确求方法/求渠道
  consult_knowledge     咨询 140  问知识/概念/成因（什么是/为什么/有用吗）
  consult_method_short  咨询  80  ≤30 字方法问句，必须命中方法正则（硬负例）
  help                  求助 200  求测评/渠道/预约/量表，不得写成方法问句
  crisis_kw             危机 120  必须含 CRISIS_KEYWORDS 任一词（规则必拦）
  crisis_slang          危机 100  网络暗语/隐喻，**禁止**出现危机关键词
                                （训练 LLM 兜底规则漏网的暗语危机）
  greeting_mixed        教师定标 80 寒暄引子 + 实质内容，detect_greeting 必须 False
  boundary              教师定标 60 求助/咨询分界 + >30 字长方法问句硬负例

  每条样本的处理流水线
  ────────────────────────────────────────────────────────────────
  1. 形状校验（8–150 字、含汉字、单行）
  2. 规则审计（直接 import 生产函数，单一事实源）：
       crisis_kw 桶要求规则必中；crisis_slang 桶要求规则**不**中；
       其余桶出现危机词/纯寒暄一律 drop；求助+方法正则→drop（生产会改判咨询）
  3. 规范化精确去重（minhash 近重复留给 merge 阶段统一做）
  4. 便宜模型盲判（用生产 TRIAGE_SYSTEM/TRIAGE_USER_TEMPLATE 原模板）：
       规则可定夺的样本（危机词命中 / 方法正则命中）跳过判官省调用；
       规则沉默样本必须判官标签 == 目标标签才保留，分歧进 rejected 侧车文件

  43 条人工评测集（scripts/eval/triage_dataset.json）**只做 test 不进训练**，
  本脚本不读取它，也不得把它的句子当种子。

用法（容器内推荐，规则层 import 与生产同代码）：
  # 冒烟（约 20 条，验证管线）
  docker exec psycheflow-backend uv run python scripts/finetune/gen_triage_data.py --limit 20
  # 全量
  docker exec psycheflow-backend uv run python scripts/finetune/gen_triage_data.py
  # 只生成不判官（调试用）
  docker exec psycheflow-backend uv run python scripts/finetune/gen_triage_data.py --limit 40 --no-judge

宿主机直跑：backend/.venv 解释器 + 仓库根 .env 自动读取 DASHSCOPE_API_KEY。

输出（sharegpt JSONL，system 由 merge 阶段按任务注入 triage system prompt）：
  {"conversations":[{"from":"human","value":"用户消息：...\\n\\n请只输出意图标签…"},
                    {"from":"gpt","value":"倾诉"}]}
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

# ── backend 目录入 path（规则层单一事实源来自生产代码） ──
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.agents.nodes.triage import detect_greeting, detect_method_question  # noqa: E402
from app.agents.prompts import TRIAGE_SYSTEM, TRIAGE_USER_TEMPLATE  # noqa: E402
from app.core.safety import CRISIS_KEYWORDS, detect_crisis_with_words  # noqa: E402

# ── 配置 ──────────────────────────────────────────────────────
DEFAULT_OUT = Path(__file__).parent / "triage_train.jsonl"
DEFAULT_REJECTED_OUT = Path(__file__).parent / "triage_rejected.jsonl"
DEFAULT_TEACHER = "deepseek-v4-pro-0813"   # pro 强模型生成（18.2 定）
DEFAULT_JUDGE = "deepseek-v4-flash-0731"   # 便宜模型盲判
DEFAULT_SLANG_JUDGE = DEFAULT_TEACHER      # 暗语危机桶判官：flash 对隐喻危机系统性弱判倾诉，须 pro
DEFAULT_CONCURRENCY = 6
DEFAULT_BATCH = 10
MAX_RETRIES = 2
VALID_LABELS = ("倾诉", "咨询", "求助", "危机")

# 青少年求助高频子主题（10 类，与知识库主题标签呼应但不依赖知识库）
SUBTHEMES = [
    "考试与学业压力（中考/高考/排名/作业）",
    "同伴关系（孤立/谣言/吵架/被嘲笑）",
    "亲子与家庭（吵架/期望/留守/偷看隐私）",
    "情绪低落与抑郁感（想哭/没劲/空落落）",
    "焦虑紧张（心慌/手抖/考前拉肚子）",
    "睡眠问题（失眠/早醒/噩梦/熬夜）",
    "自我认同与外貌（身材/长痘/觉得自己没用）",
    "青春期情感（暗恋/表白被拒/分手）",
    "手机与游戏（停不下来/短视频/熬夜玩）",
    "创伤与丧失（车祸/亲人离世/被侵害回忆）",
]

# 混寒暄桶：消息里要带的寒暄引子（仅作生成约束，生产判定仍以 detect_greeting 为准）
GREETING_CUES = ("你好", "您好", "嗨", "哈喽", "hi", "hello", "在吗", "老师好")

# 桶定义：id / 目标标签（None=教师每条给标）/ 默认配额 / 特殊约束
BUCKETS = [
    {"id": "vent", "label": "倾诉", "quota": 220, "hard": None},
    {"id": "consult_knowledge", "label": "咨询", "quota": 140, "hard": None},
    {"id": "consult_method_short", "label": "咨询", "quota": 80, "hard": "method_short"},
    {"id": "help", "label": "求助", "quota": 200, "hard": "help"},
    {"id": "crisis_kw", "label": "危机", "quota": 120, "hard": "crisis_kw"},
    {"id": "crisis_slang", "label": "危机", "quota": 100, "hard": "crisis_slang"},
    {"id": "greeting_mixed", "label": None, "quota": 80, "hard": "greeting_mixed"},
    {"id": "boundary", "label": None, "quota": 60, "hard": "boundary"},
]

LABEL_DEFS = """\
- 求助：主动寻求测评/咨询渠道/量表推荐/想知道自己状态（如"我想做测评""有什么量表""怎么预约心理咨询"）
- 倾诉：表达情绪/压力/困扰但未明确求助或求方法（如"我最近压力大""我心情不好""今天又吵架了"）
- 咨询：询问心理知识/概念解释/成因/科学依据，或询问缓解改善的具体做法（如"什么是抑郁""怎么缓解焦虑""睡不着怎么办"）
- 危机：表达自杀/自伤/绝望（直述或暗语隐喻都算）"""

BUCKET_SPECS = {
    "vent": (
        "生成【倾诉】类：学生第一人称在表达情绪、压力、困扰，只是想说出来，"
        "**没有**询问缓解方法、没有请求渠道/测评。可以带委屈/疲惫/烦躁等语气，"
        "长度 12–80 字，口语化，不要出现书面化的'请问'。"
    ),
    "consult_knowledge": (
        "生成【咨询-知识】类：询问心理学概念、成因、鉴别、科学依据（什么是/为什么/"
        "算不算/有用吗/正常吗），可以是学生本人或家长口吻，长度 12–80 字。"
    ),
    "consult_method_short": (
        "生成【咨询-方法短句】类硬负例：整条 **≤30 个汉字**、问缓解/改善/应对做法的短句，"
        "必须自然包含'怎么缓解/如何应对/怎么办/有什么办法/怎样调节'这类说法之一，"
        "例如'考试前总胃疼怎么缓解'。**不要**生成求渠道/求测评的句子。"
    ),
    "help": (
        "生成【求助】类：想做测评、找咨询渠道、预约心理老师、找量表、想确认自己状态，"
        "例如'哪里能做正规的心理测评''学校心理咨询怎么预约'。"
        "**严禁**询问具体缓解方法（不能出现'怎么缓解/怎么办/有什么办法'）——"
        "那是【咨询】类，混写的样本会被丢弃。长度 12–60 字。"
    ),
    "crisis_kw": (
        "生成【危机-关键词】类：消息表达自杀/自伤/强烈绝望，且**必须原样包含以下词表中"
        "至少一个词**（可以是第一人称，也有约 20% 是学生转述同学/朋友的第三称）：\n"
        + "、".join(CRISIS_KEYWORDS)
        + "\n措辞要像真实中学生说话，可放在考试失利、亲子冲突、失眠等具体情境里，长度 10–80 字。"
    ),
    "crisis_slang": (
        "生成【危机-暗语隐喻】类：表达想死/自伤/彻底绝望的真实消息，但**禁止**出现以下"
        "任何词（逐字出现即作废）：\n"
        + "、".join(CRISIS_KEYWORDS)
        + "\n改用网络暗语或隐喻表达，例如：想永远睡过去不再醒来、希望明天醒不过来、"
        "想从这个世界消失、吃了一整瓶药、站在高处想往下跳、用疼压过心里的难受、"
        "这个世界没了我也无所谓、想找个地方躲起来再也不出来。要有真实细节差异，"
        "不要每条都一个句式，长度 10–80 字。"
    ),
    "greeting_mixed": (
        "生成【寒暄引子+实质内容】硬负例：以寒暄/打招呼开头（你好/您好/嗨/哈喽/在吗等），"
        "逗号后立刻接真实的心理困扰或问题，整条 **>20 字**。这类消息不是纯寒暄，"
        "必须按后半句实质内容在 label 字段给出 倾诉/咨询/求助 之一（禁止给'危机'或'寒暄'）。"
        "示例：'你好，我最近一到考试就手抖，有没有什么办法能缓解一点'"
    ),
    "boundary": (
        "生成【求助/咨询分界】硬负例：消息处在'找渠道/找测评'与'问缓解方法'的边界上，"
        "仔细分辨后在 label 字段给 求助 或 咨询；另请包含约一半 **>30 字**的长方法问句"
        "（先讲一段处境再问怎么办，这类长句规则层不拦截、全靠分类）。"
        "示例长句：'我已经连续两周两三点才睡得着，白天上课完全听不进去，这样下去怎么办才好'"
    ),
}

GEN_SYSTEM_TMPL = """你是青少年心理援助语料专家，为意图分类模型生成训练消息。

【四个意图标签的定义】
{label_defs}

【本次任务】
{spec}

【通用要求】
1. 全部生成青少年（或少量家长）真实会发给心理助手的**单条消息**，第一人称口语，不要写成对话
2. 围绕子主题：{subtheme}
3. {n} 条彼此差异要大：情境不同、措辞不同、句式不同、长度不同；禁止只替换一两个词
4. 不要 markdown、不要序号、不要引号包裹、不要解释，每条就是裸消息文本
5. 同一条里禁止出现换行

【输出格式】严格只输出 JSON（不要代码块）：
{schema}"""

SCHEMA_FIXED = '{"samples":[{"message":"..."}, ...]}'
SCHEMA_LABELED = '{"samples":[{"message":"...","label":"倾诉|咨询|求助"}, ...]}'


# ── .env 轻量读取（宿主机直跑时自动补 DASHSCOPE_API_KEY） ──
def load_dotenv_once() -> None:
    if os.getenv("DASHSCOPE_API_KEY"):
        return
    env_path = _BACKEND_ROOT.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# ── 生成 ──────────────────────────────────────────────────────
async def teacher_generate(
    client: AsyncOpenAI,
    model: str,
    bucket: dict,
    subtheme: str,
    n: int,
    sem: asyncio.Semaphore,
) -> list[dict]:
    """单次调用 teacher 生成 n 条，返回 [{message, label?}]。失败返回空列表。"""
    need_label = bucket["label"] is None
    prompt = GEN_SYSTEM_TMPL.format(
        label_defs=LABEL_DEFS,
        spec=BUCKET_SPECS[bucket["id"]],
        subtheme=subtheme,
        n=n,
        schema=SCHEMA_LABELED if need_label else SCHEMA_FIXED,
    )
    async with sem:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.9,
                    top_p=0.95,
                    max_tokens=4000,  # pro 模型有 reasoning_content，给足余量防截断
                    extra_body={"enable_thinking": False},
                    timeout=120,
                )
                raw = resp.choices[0].message.content or ""
                items = _parse_samples(raw)
                if items:
                    return items
                print(f"    [WARN] {bucket['id']}/{subtheme[:8]} 第{attempt}次解析为空")
            except Exception as e:  # noqa: BLE001
                print(f"    [ERROR] {bucket['id']}/{subtheme[:8]} 第{attempt}次失败: {type(e).__name__}: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 * attempt)
    return []


def _parse_samples(raw: str) -> list[dict]:
    """容错解析 teacher 输出的 samples 数组。"""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    first, last = s.find("{"), s.rfind("}")
    if first == -1 or last <= first:
        return []
    try:
        obj = json.loads(s[first:last + 1])
    except json.JSONDecodeError:
        return []
    arr = obj.get("samples") if isinstance(obj, dict) else None
    if not isinstance(arr, list):
        return []
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        msg = (item.get("message") or "").strip().strip("「」\"'“”‘’")
        if not msg or "\n" in msg:
            continue
        label = (item.get("label") or "").strip()
        out.append({"message": msg, "label": label})
    return out


# ── 规则审计（直接调生产函数）────────────────────────────────
def audit(message: str, target_label: str | None, hard: str | None) -> tuple[str | None, str | None, bool, list[str]]:
    """返回 (final_label, rule_decides_label or None, slang_review, reasons)。

    final_label=None 表示丢弃；reasons 记录丢弃/标记原因。
    规则沉默样本 rule_decides_label=None，交判官盲判。
    slang_review=True 表示 kw 桶里没含精确关键词、语义疑似暗语危机，
    须走暗语增强判官（pro）复核而非直接丢弃。
    """
    reasons: list[str] = []
    crisis_hit, words = detect_crisis_with_words(message)
    greeting = detect_greeting(message)
    method_q = detect_method_question(message)

    if hard == "crisis_kw":
        if not crisis_hit:
            # 教师把暗语危机放进了关键词桶：不直接丢，改走暗语 pro 判官救回
            return "危机", None, True, ["crisis_kw_missing_keyword_reroute"]
        return "危机", "危机", False, ["crisis_keyword:" + ",".join(words)]

    if hard == "crisis_slang":
        if crisis_hit:
            return None, None, False, ["slang_bucket_leaked_keyword:" + ",".join(words)]
        return "危机", None, True, ["crisis_slang"]

    # 其余四个非危机桶 + 两个教师定标桶
    if crisis_hit:
        return None, None, False, ["non_crisis_bucket_has_keyword:" + ",".join(words)]
    if greeting:
        return None, None, False, ["pure_greeting_never_reaches_llm"]

    if hard == "greeting_mixed":
        cue = _greeting_cue(message)
        if not cue:
            return None, None, False, ["greeting_mixed_missing_cue"]
        if target_label not in ("倾诉", "咨询", "求助"):
            return None, None, False, ["greeting_mixed_bad_label:" + str(target_label)]
        reasons.append("greeting_cue:" + cue)
        final = target_label
    elif hard == "boundary":
        if target_label not in ("求助", "咨询"):
            return None, None, False, ["boundary_bad_label:" + str(target_label)]
        final = target_label
    else:
        final = target_label  # type: ignore[assignment]
        if final not in VALID_LABELS or final == "危机":
            return None, None, False, ["bad_label:" + str(target_label)]

    if hard == "method_short" and not method_q:
        return None, None, False, ["method_short_not_regex_hit"]
    if hard == "help" and method_q:
        return None, None, False, ["help_bucket_method_question"]

    if method_q:
        # 方法正则只对「求助→咨询」改判；标签已是咨询则与生产终态一致，规则可定夺
        if final == "求助":
            return None, None, False, ["help_label_but_method_regex"]
        reasons.append("method_regex_hit")
        return final, "咨询", False, reasons

    return final, None, False, reasons


def _greeting_cue(message: str) -> str:
    low = message.lower()
    for cue in GREETING_CUES:
        if low.startswith(cue):
            return cue
    return ""


def normalize_key(message: str) -> str:
    """去标点/空白/大小写后的精确去重键。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", message.lower())


def shape_ok(message: str) -> bool:
    if not (8 <= len(message) <= 150):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", message))


# ── 便宜模型盲判 ──────────────────────────────────────────────
# 暗语危机桶专用增强判词：flash 实测把「想消失/站楼顶/数安眠药」系统性误判倾诉，
# 故该桶用 pro 模型 + 显式隐喻指引；判词与 TRIAGE 定义保持一致，只补充识别线索。
SLANG_JUDGE_SYSTEM = TRIAGE_SYSTEM + """

【危机类补充判定指引（隐喻/暗语）】以下没有直白关键词也判【危机】：
1. 希望不再醒来/永远睡过去/从世界消失/自己没了别人更好等死亡愿望；
2. 站在高处或水边想跳下、囤积或大量服药、安排告别等行为准备/实施描述；
3. 用身体疼痛压过心理痛苦、自我伤害的隐喻说法。
注意：单纯的睡眠抱怨、对他人危机的科普讨论不算危机。"""


async def judge_label(
    client: AsyncOpenAI, model: str, message: str, sem: asyncio.Semaphore,
    system: str = TRIAGE_SYSTEM,
) -> str | None:
    """用生产原模板让模型独立分类，返回标签或 None。"""
    async with sem:
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": TRIAGE_USER_TEMPLATE.format(message=message)},
                    ],
                    temperature=0.0,
                    max_tokens=20,
                    extra_body={"enable_thinking": False},
                    timeout=60,
                )
                raw = (resp.choices[0].message.content or "").strip()
                for lab in VALID_LABELS:
                    if lab in raw:
                        return lab
                return None
            except Exception as e:  # noqa: BLE001
                if attempt == MAX_RETRIES - 1:
                    print(f"    [JUDGE ERROR] {type(e).__name__}: {e}")
                else:
                    await asyncio.sleep(2)
    return None


def to_sharegpt(message: str, label: str) -> dict:
    return {"conversations": [
        {"from": "human", "value": TRIAGE_USER_TEMPLATE.format(message=message)},
        {"from": "gpt", "value": label},
    ]}


# ── 桶配额循环 ────────────────────────────────────────────────
async def fill_bucket(
    bucket: dict,
    quota: int,
    batch: int,
    teacher: str,
    judge_model: str | None,
    slang_judge_model: str | None,
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    state: dict,
) -> None:
    """反复生成直到该桶配额填满或尝试轮次耗尽。"""
    bucket_idx = next(i for i, b in enumerate(BUCKETS) if b["id"] == bucket["id"])
    rng = random.Random(42 + bucket_idx * 97)  # 稳定种子：str.__hash__ 受 PYTHONHASHSEED 影响不可用
    themes = SUBTHEMES[:]
    rng.shuffle(themes)
    max_rounds = max(4, (quota + batch - 1) // batch * 3)

    for rnd in range(max_rounds):
        if state["kept_by_bucket"][bucket["id"]] >= quota:
            return
        subtheme = themes[rnd % len(themes)]
        items = await teacher_generate(client, teacher, bucket, subtheme, batch, sem)
        state["calls"] += 1
        if not items:
            state["api_empty"] += 1
            continue

        for item in items:
            if state["kept_by_bucket"][bucket["id"]] >= quota:
                break  # 配额已满，本批剩余样本不审计不计费（teacher 已生成，浪费仅限本批）
            msg = item["message"]
            tgt = bucket["label"] or item.get("label") or None
            state["raw"] += 1

            if not shape_ok(msg):
                state["drop"]["shape"] += 1
                _reject(state, bucket, msg, tgt, ["shape_failed"])
                continue
            key = normalize_key(msg)
            if key in state["seen"]:
                state["drop"]["dedup"] += 1
                _reject(state, bucket, msg, tgt, ["dedup"])
                continue

            final, rule_label, slang_review, reasons = audit(msg, tgt, bucket["hard"])
            if final is None:
                state["drop"]["rule"] += 1
                for r in reasons:
                    state["drop_reasons"][r.split(":")[0]] += 1
                _reject(state, bucket, msg, tgt, reasons)
                continue

            state["seen"].add(key)

            if rule_label is not None or judge_model is None:
                # 规则可定夺，或 --no-judge 调试：直接保留
                state["pre_judge_kept"] += 1
                _keep(state, bucket, msg, final, reasons, judge=None)
                continue

            if slang_review:
                # 暗语/kw 桶漏词救回：pro + 危机增强判词，一判定生死
                judged = await judge_label(client, slang_judge_model, msg, sem, SLANG_JUDGE_SYSTEM)
                state["judge_calls"] += 1
                state["slang_judge_calls"] += 1
                if judged == final:
                    state["judge_agree"] += 1
                    _keep(state, bucket, msg, final, reasons, judge=judged)
                else:
                    state["drop"]["judge"] += 1
                    state["judge_disagree"] += 1
                    _reject(state, bucket, msg, tgt,
                            reasons + [f"slang_pro_judge_disagree:{judged}!={final}"])
                continue

            # 普通桶：flash 先盲判，分歧则 pro 二审终裁（flash 对渠道问句/长方法问句系统性误判）
            judged = await judge_label(client, judge_model, msg, sem, TRIAGE_SYSTEM)
            state["judge_calls"] += 1
            state["flash_judge_calls"] += 1
            if judged == final:
                state["judge_agree"] += 1
                _keep(state, bucket, msg, final, reasons, judge=judged)
                continue

            tiebreak = await judge_label(client, slang_judge_model, msg, sem, TRIAGE_SYSTEM)
            state["judge_calls"] += 1
            state["tiebreak_calls"] += 1
            if tiebreak == final:
                state["tiebreak_rescued"] += 1
                state["judge_agree"] += 1
                _keep(state, bucket, msg, final,
                      reasons + [f"flash_disagreed_{judged}_pro_rescued"], judge=tiebreak)
            else:
                state["drop"]["judge"] += 1
                state["judge_disagree"] += 1
                _reject(state, bucket, msg, tgt,
                        reasons + [f"flash:{judged},pro:{tiebreak}!={final}"])

        kept = state["kept_by_bucket"][bucket["id"]]
        print(f"  [{bucket['id']}] 轮次 {rnd + 1}/{max_rounds} 子主题={subtheme[:10]} "
              f"本批 {len(items)} 条，累计保留 {kept}/{quota}")

    if state["kept_by_bucket"][bucket["id"]] < quota:
        print(f"  [WARN] 桶 {bucket['id']} 配额未填满："
              f"{state['kept_by_bucket'][bucket['id']]}/{quota}（可调高 max_rounds 后补跑）")


def _keep(state: dict, bucket: dict, msg: str, label: str, reasons: list, judge: str | None) -> None:
    state["kept"].append({"message": msg, "label": label, "bucket": bucket["id"],
                          "reasons": reasons, "judge": judge})
    state["kept_by_bucket"][bucket["id"]] += 1
    state["label_counts"][label] += 1


def _reject(state: dict, bucket: dict, msg: str, label: str | None, reasons: list) -> None:
    state["rejected"].append({"message": msg, "target_label": label,
                              "bucket": bucket["id"], "reasons": reasons})


# ── 主流程 ────────────────────────────────────────────────────
async def run(args) -> int:
    load_dotenv_once()
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv("DASHSCOPE_BASE_URL",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    if not api_key:
        print("[ERROR] 未设置 DASHSCOPE_API_KEY（容器环境或仓库根 .env）")
        return 1

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    sem = asyncio.Semaphore(args.concurrency)

    if args.provenance_out is None:
        args.provenance_out = args.out.with_name(args.out.stem + "_provenance.jsonl")

    if args.limit:
        # 冒烟：各桶按比例缩到至少 2 条
        total_default = sum(b["quota"] for b in BUCKETS)
        quotas = {b["id"]: max(2, round(b["quota"] * args.limit / total_default)) for b in BUCKETS}
        batch = min(args.batch, 5)
    else:
        quotas = {b["id"]: b["quota"] for b in BUCKETS}
        batch = args.batch
    judge_model = None if args.no_judge else args.judge_model
    slang_judge_model = None if args.no_judge else args.slang_judge_model

    state = {
        "kept": [], "rejected": [], "seen": set(),
        "kept_by_bucket": {b["id"]: 0 for b in BUCKETS},
        "label_counts": Counter(),
        "drop": Counter(), "drop_reasons": Counter(),
        "raw": 0, "calls": 0, "api_empty": 0,
        "judge_calls": 0, "judge_agree": 0, "judge_disagree": 0,
        "slang_judge_calls": 0, "flash_judge_calls": 0,
        "tiebreak_calls": 0, "tiebreak_rescued": 0,
        "pre_judge_kept": 0,
    }

    print(f"[INFO] teacher={args.model} judge={judge_model or '（关闭）'} "
          f"暗语判官={slang_judge_model or '（关闭）'} 并发={args.concurrency} 批大小={batch}")
    print(f"[INFO] 桶配额={quotas}")

    for bucket in BUCKETS:
        print(f"\n=== 桶 {bucket['id']}（目标标签 {bucket['label'] or '教师定标'}）===")
        await fill_bucket(bucket, quotas[bucket["id"]], batch,
                          args.model, judge_model, slang_judge_model, client, sem, state)

    # ── 写出 ──
    random.Random(42).shuffle(state["kept"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f, \
            open(args.provenance_out, "w", encoding="utf-8") as fp:
        for row in state["kept"]:
            f.write(json.dumps(to_sharegpt(row["message"], row["label"]),
                               ensure_ascii=False) + "\n")
            # 溯源侧车：桶/判官/救回标记，供 5% 人工抽检定向复核（不进训练）
            fp.write(json.dumps(
                {"message": row["message"], "label": row["label"], "bucket": row["bucket"],
                 "reasons": row["reasons"], "judge": row["judge"]},
                ensure_ascii=False) + "\n")
    with open(args.rejected_out, "w", encoding="utf-8") as f:
        for row in state["rejected"]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ── 分布报告 ──
    total_kept = len(state["kept"])
    print("\n" + "=" * 64)
    print("triage 数据生成报告")
    print("=" * 64)
    print(f"teacher 调用 {state['calls']} 次（空响应 {state['api_empty']}）| 原始样本 {state['raw']}")
    print(f"保留 {total_kept} 条 -> {args.out}")
    print(f"溯源侧车（桶/判官标记，供人工抽检）-> {args.provenance_out}")
    print(f"侧车（丢弃/分歧） {len(state['rejected'])} 条 -> {args.rejected_out}")
    print(f"  丢弃明细：形状 {state['drop']['shape']} / 规则 {state['drop']['rule']} "
          f"/ 去重 {state['drop']['dedup']} / 判官分歧 {state['drop']['judge']}")
    if state["drop_reasons"]:
        print(f"  规则原因 Top：{dict(state['drop_reasons'].most_common(8))}")
    print(f"判官：总调用 {state['judge_calls']}（flash {state['flash_judge_calls']} + "
          f"暗语 pro {state['slang_judge_calls']} + pro 二审 {state['tiebreak_calls']}），"
          f"最终一致 {state['judge_agree']}，分歧 {state['judge_disagree']}"
          + (f"，一致率 {state['judge_agree'] / max(state['judge_calls'], 1):.1%}"
             if state["judge_calls"] else ""))
    print(f"  pro 二审救回 flash 误判 {state['tiebreak_rescued']} 条；"
          f"规则可定夺跳过判官 {state['pre_judge_kept']} 条；分歧样本见侧车可人工捞回")
    print("最终标签分布：", dict(state["label_counts"]))
    print("桶保留分布：", {b["id"]: state["kept_by_bucket"][b["id"]] for b in BUCKETS})
    short = {k: v for k, v in state["label_counts"].items() if v < 200}
    if not args.limit and short:
        print(f"[WARN] 以下类别不足 200 条：{short}，需补跑")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="triage 意图分类训练数据生成（18.2）")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--rejected-out", type=Path, default=DEFAULT_REJECTED_OUT)
    p.add_argument("--provenance-out", type=Path, default=None,
                   help="保留样本溯源文件（默认与 --out 同目录加 _provenance 后缀）")
    p.add_argument("--model", default=DEFAULT_TEACHER, help="teacher 生成模型")
    p.add_argument("--judge-model", default=DEFAULT_JUDGE, help="普通桶便宜判官模型")
    p.add_argument("--slang-judge-model", default=DEFAULT_SLANG_JUDGE,
                   help="pro 判官：暗语桶定夺 + flash 分歧二审终裁")
    p.add_argument("--no-judge", action="store_true", help="跳过判官（调试用）")
    p.add_argument("--limit", type=int, default=0, help="冒烟模式：总样本上限（约）")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="每次调用生成条数")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = p.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
