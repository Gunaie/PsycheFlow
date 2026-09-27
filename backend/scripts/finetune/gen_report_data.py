# -*- coding: utf-8 -*-
"""report 发展建议训练数据「合成测评矩阵」生成器（18.2 第二批）。

现状：export_report_finetune_data.py 只能读 SQLite 里 52 条真实测评记录，
严重度/子维度/量表组合覆盖不可控。本脚本不碰数据库，直接合成答案向量，
**所有分数与等级一律以生产计分引擎为准**（scale.score + _compute_subdims），
教师模型只负责写「发展建议」，保证训练样本的 instruction 与生产链路零偏差。

矩阵设计（默认 ≈256 条，落在 18.2 目标 200–300）
────────────────────────────────────────────────────────────────
单量表（238）：每档严重度内轮换「关键子维度」高/中抬升，生成异质雷达形状
  phq_a  无6/轻12/中14/中重14/重12 = 58  + 第9题阳性危机 6
  scared 无8/轻16/中30           = 54   （引擎只产 无/轻/中 三档）
  sdq    无8/轻14/中28           = 50   （含亲社会低分变体，反向题自动处理）
  mht    无8/轻16/中24/重18      = 66  + 85/97 题阳性危机 4
双量表/极端组合（18）：
  phq+scared 跨严重度 8、sdq+scared 2、sdq+phq 2、phq+mht 危机 2、
  三量表 2、极端全高（PHQ-A 第9题=0 非危机重 + MHT 重）2

答案采样（确定性，随机种子 42）
  目标总分配以均值 → 按题抽样 → 贪心逐题 ±1 修正，唯一验收标准：
    1) scale.score(...).severity == 目标档（生产函数，非本脚本估算）
    2) _compute_subdims(...)[目标维度].pct 落入目标低/中/高带
  SDQ 反向题、亲社会优势维度（inverse）全部由生产函数的 pct 兜底，
  贪心只信 oracle 输出，不自己维护反向映射。

断言过滤（与 eval_report / REPORT_SYSTEM 同源，不过即换温度重生，3 次后入侧车）
  · 300–460 字 · ≥2 个结构化小节 · 家庭/学校/自我调节三方面关键词齐
  · 中度及以上：必须出现专业干预措辞（专业/医院/心理老师/评估/转介…）
  · 危机升级：必须坚定（尽快/立即/必须/第一时间）+ 专业干预，不得淡化
  · 无/轻度安全场景：禁止 12355/自杀/自伤/危机干预等过度升级话术

用法（容器内）：
  docker exec psycheflow-backend uv run python scripts/finetune/gen_report_data.py --no-llm  # 只验矩阵
  docker exec psycheflow-backend uv run python scripts/finetune/gen_report_data.py --limit 12 # 试点
  docker exec psycheflow-backend uv run python scripts/finetune/gen_report_data.py            # 全量

输出 sharegpt JSONL（system 由 merge 阶段注入 report system prompt）：
  {"conversations":[{"from":"human","value":"本次评估结果：…请撰写发展建议。"},
                    {"from":"gpt","value":"## 发展建议 …"}]}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

from openai import AsyncOpenAI

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.reports.service import (  # noqa: E402
    MHT_SUBDIMS,
    PHQ_A_SUBDIMS,
    REPORT_SYSTEM,
    SCARED_SUBDIMS,
    SDQ_SUBDIMS,
    SEVERITY_RANK,
    _compute_subdims,
    _scale_max_score,
)
from app.scales.registry import get_scale  # noqa: E402

# 优势维度（高分=好）：抬难度带时贪心方向必须反转
INVERSE_DIMS = {d["name"] for d in SDQ_SUBDIMS if d.get("inverse")}
# 量表 → {子维度名: 题号}（强制题存在时用来避开数学不可达的维度组合）
_DIM_ITEMS_MAP_RAW = {
    "phq_a": PHQ_A_SUBDIMS, "scared": SCARED_SUBDIMS,
    "sdq": SDQ_SUBDIMS, "mht": MHT_SUBDIMS,
}
DIM_ITEMS = {
    sid: {d["name"]: list(d["items"]) for d in cfgs}
    for sid, cfgs in _DIM_ITEMS_MAP_RAW.items()
}

# ── 配置 ──────────────────────────────────────────────────────
HERE = Path(__file__).parent
DEFAULT_OUT = HERE / "report_train.jsonl"
DEFAULT_REJECTED_OUT = HERE / "report_rejected.jsonl"
DEFAULT_PROVENANCE_OUT = HERE / "report_train_provenance.jsonl"
DEFAULT_TEACHER = "deepseek-v4-pro-0813"   # 18.2 定：pro 级开思考链
DEFAULT_CONCURRENCY = 6
MAX_GEN_TRIES = 3
SEED = 42

SEVERITY_LABEL = {
    "none": "无明显症状",
    "mild": "轻度",
    "moderate": "中度",
    "moderately_severe": "中重度",
    "severe": "重度",
}
# 各量表严重度可达档（生产引擎实际输出，不可达档不进矩阵）
SCALE_LEVELS = {
    "phq_a": ["none", "mild", "moderate", "moderately_severe", "severe"],
    "scared": ["none", "mild", "moderate"],
    "sdq": ["none", "mild", "moderate"],
    "mht": ["none", "mild", "moderate", "severe"],
}
# 起始采样均值（仅引导，最终以生产 score() 为准）
SEV_PCT_GUIDE = {
    "none": 0.12, "mild": 0.34, "moderate": 0.55,
    "moderately_severe": 0.66, "severe": 0.85,
}
# 单量表配额
SINGLE_QUOTA = {
    "phq_a": {"none": 6, "mild": 12, "moderate": 14, "moderately_severe": 14, "severe": 12},
    "scared": {"none": 8, "mild": 16, "moderate": 30},
    "sdq": {"none": 8, "mild": 14, "moderate": 28},
    "mht": {"none": 8, "mild": 16, "moderate": 24, "severe": 18},
}
DIM_TARGET_LEVEL = {
    "none": "mid",       # 安全档：单维度中等，其余低，造异质形状
    "mild": "mid",
    "moderate": "high",
    "moderately_severe": "high",
    "severe": "high",
}
LEVEL_BAND = {"low": (0.0, 0.30), "mid": (0.30, 0.70), "high": (0.70, 1.01)}

# ── 断言关键词族（与 eval_report「发展建议非空」及 REPORT_SYSTEM 同源） ──
KW_FAMILY = {
    "家庭": ("家长", "父母", "家庭", "爸爸", "妈妈", "家里"),
    "学校": ("学校", "老师", "班主任", "校园", "心理老师"),
    "自我调节": ("呼吸", "作息", "运动", "练习", "日记", "放松", "冥想",
              "睡眠", "记录", "尝试", "情绪", "调节", "户外"),
}
KW_SUGGEST = ("建议", "可以", "不妨", "尝试", "推荐", "优先", "练习", "关注",
              "需要", "鼓励", "试试", "学会", "宜")
KW_PROFESSIONAL = ("专业", "医院", "医生", "心理老师", "评估", "转介", "临床", "心理咨询")
KW_FIRM = ("尽快", "立即", "立刻", "必须", "第一时间", "马上", "24小时", "24 小时")
KW_OVER_ESCALATE = (
    "12355", "立即拨打", "需要立即寻求帮助", "危机干预",
    "马上就医", "立即就医", "立即转介", "立刻就医",
)


# ── 合成答案向量 ──────────────────────────────────────────────
def _dim_names(sid: str) -> list[str]:
    return [d["name"] for d in _compute_subdims(
        {"scale_id": sid, "scale_name": sid, "answers": {}})]


def _oracle(sid: str, answers: dict[int, int]) -> tuple[str, dict[str, int]]:
    """返回 (生产严重度, 子维度名→pct)。"""
    scale = get_scale(sid)
    result = scale.score(answers)
    assess = {
        "scale_id": sid, "scale_name": scale.scale_name,
        "severity": result.severity.value, "answers": {str(k): v for k, v in answers.items()},
    }
    dims = {d["name"]: d["pct"] / 100.0 for d in _compute_subdims(assess)}
    return result.severity.value, dims


def _sample_vector(sid: str, target_sev: str, target_dim: str | None,
                   forced: dict[int, int] | None, rng: random.Random) -> dict[int, int] | None:
    """合成一个答案向量，直到生产 score 严重度命中、目标维度 pct 入带。"""
    scale = get_scale(sid)
    n = len(scale.items)
    vmax = max(int(k) for k in scale.options.keys())
    all_ids = [it["id"] for it in scale.items]
    dim_cfg = DIM_ITEMS[sid]
    forced = dict(forced or {})
    free_ids = [q for q in all_ids if q not in forced]
    dim_ids = [q for q in (dim_cfg.get(target_dim) or []) if q not in forced]
    level = DIM_TARGET_LEVEL[target_sev]
    dim_lo, dim_hi = LEVEL_BAND[level]

    def dim_ok(dims: dict[str, int]) -> bool:
        if not target_dim:
            return True
        return dim_lo <= dims[target_dim] < dim_hi

    for restart in range(10):
        # 起始均值：目标维度用带中值，其余题用总分均值反推
        base_p = min(0.95, max(0.05, SEV_PCT_GUIDE[target_sev] + rng.uniform(-0.06, 0.06)))
        dim_p = (dim_lo + dim_hi) / 2 + rng.uniform(-0.08, 0.08) if target_dim else base_p
        if dim_ids:
            other_p = (base_p * n - dim_p * len(dim_ids)) / max(1, len(free_ids) - len(dim_ids))
            other_p = min(0.95, max(0.05, other_p))
        else:
            other_p = base_p
        ans = dict(forced)
        for q in free_ids:
            mean_v = (dim_p if q in dim_ids else other_p) * vmax
            lo_v = int(mean_v)
            val = lo_v + (1 if rng.random() < mean_v - lo_v and lo_v < vmax else 0)
            ans[q] = val

        best = None
        for _ in range(300):
            sev, dims = _oracle(sid, ans)
            if sev == target_sev and dim_ok(dims):
                return ans
            rank_gap = SEVERITY_RANK[target_sev] - SEVERITY_RANK[sev]
            dim_gap = 0.0
            if target_dim:
                mid = (dim_lo + dim_hi) / 2
                dim_gap = (mid - dims[target_dim]) if level == "high" else (dims[target_dim] - mid)
            score = abs(rank_gap) * 10 + abs(dim_gap)
            if best is None or score < best[0]:
                best = (score, dict(ans))
            # 随机扰动一步：严重度不对优先动非目标维度题；维度不对动维度题
            if target_dim and not dim_ok(dims) and dim_ids and rng.random() < 0.7:
                q = rng.choice(dim_ids)
                up = dims[target_dim] < dim_lo if level != "low" else dims[target_dim] >= dim_hi
                if target_dim in INVERSE_DIMS:
                    up = not up  # 优势维度：pct 反转，原始分方向相反
            elif rank_gap != 0:
                pool = [q for q in free_ids if q not in dim_ids] or free_ids
                q = rng.choice(pool)
                up = rank_gap > 0
            else:
                q = rng.choice(free_ids)
                up = rng.random() < 0.5
            if 0 <= ans[q] + (1 if up else -1) <= vmax:
                ans[q] += 1 if up else -1
        ans = best[1]  # 本次重启未达点 → 从最近向量再重启
    return None


# ── 场景矩阵 ──────────────────────────────────────────────────
def _build_scenario(answers_by_scale: dict[str, dict[int, int]]) -> list[dict]:
    """与 eval_report._build_scenario 同形状的生产 assessment dict 列表。"""
    out = []
    for sid, answers in answers_by_scale.items():
        scale = get_scale(sid)
        result = scale.score(answers)
        out.append({
            "scale_id": result.scale_id,
            "scale_name": result.scale_name,
            "total_score": result.total_score,
            "severity": result.severity.value if hasattr(result.severity, "value") else str(result.severity),
            "crisis_level": result.crisis_level.value if hasattr(result.crisis_level, "value") else str(result.crisis_level),
            "crisis_triggers": result.crisis_triggers,
            "interpretation": result.interpretation,
            "needs_crisis_escalation": result.crisis_level.value == "elevated",
            "answers": {str(k): v for k, v in answers.items()},
        })
    return out


def _build_instruction(assessments: list[dict], all_dims: list[dict]) -> str:
    """与 export_report_finetune_data._build_instruction 逐字对齐。"""
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
    return "本次评估结果：\n" + "\n".join(parts) + f"\n子维度剖析：\n{dims_txt}\n\n请撰写发展建议。"


def build_matrix(rng: random.Random) -> list[dict]:
    """返回 [{id, answers:{sid:vec}, target_dim, spec}]。"""
    specs: list[dict] = []

    # 单量表 × 严重度 × 轮换子维度
    # 普通单元格强制危机题=0（与 eval_report 非危机路径一致，危机阳性由专用行覆盖）
    forced_zero = {"phq_a": {9: 0}, "mht": {85: 0, 97: 0}}
    for sid, quotas in SINGLE_QUOTA.items():
        dims = _dim_names(sid)
        forced = forced_zero.get(sid)
        # 轮换时避开与强制 0 题重叠而在高档数学不可达的维度
        dim_cycle = ([d for d in dims if not (set(DIM_ITEMS[sid][d]) & set(forced))]
                     if forced else dims)
        for sev, count in quotas.items():
            for i in range(count):
                dim = dim_cycle[i % len(dim_cycle)]
                specs.append({
                    "id": f"{sid}:{sev}:{dims.index(dim)}:{i}",
                    "answers_spec": [(sid, sev, dim, forced)],
                })

    # PHQ-A 第 9 题阳性危机（总分跨档，强制 item9 非 0，3 档各 2）
    for i, (v, sev) in enumerate(
            [(1, "mild"), (1, "moderate"), (2, "moderately_severe"),
             (2, "severe"), (3, "severe"), (3, "moderately_severe")]):
        specs.append({
            "id": f"phq_crisis:{i}",
            "answers_spec": [("phq_a", sev, "prefer_forced", {9: v})],
        })
    # MHT 85/97 阳性危机
    for i, (qid, sev) in enumerate(
            [(85, "moderate"), (97, "moderate"), (85, "severe"), (97, "mild")]):
        specs.append({
            "id": f"mht_crisis:{i}",
            "answers_spec": [("mht", sev, "prefer_forced", {qid: 1})],
        })

    # 双量表/三量表/极端组合（目标档按 (sid,sev) 列出，子维度各自轮换）
    combo = [
        ([("phq_a", "severe"), ("scared", "moderate")], 2),
        ([("phq_a", "moderate"), ("scared", "mild")], 2),
        ([("phq_a", "moderately_severe"), ("scared", "none")], 2),
        ([("phq_a", "none"), ("scared", "moderate")], 2),
        ([("sdq", "moderate"), ("scared", "moderate")], 2),
        ([("sdq", "mild"), ("phq_a", "mild")], 2),
        ([("phq_a", "mild"), ("mht", "severe")], 2),
        ([("sdq", "moderate"), ("phq_a", "moderate"), ("scared", "mild")], 2),
        ([("phq_a", "severe"), ("mht", "severe")], 2),  # 极端全高（危机题均=0）
    ]
    for ci, (sids_sev, rep) in enumerate(combo):
        for r in range(rep):
            arr = []
            for j, (sid, sev) in enumerate(sids_sev):
                forced = {}
                if sid == "phq_a":
                    forced = {9: 0}  # 非危机组合里第 9 题必须为 0
                if sid == "mht":
                    forced = {85: 0, 97: 0}
                # 维度在实例化时轮换，并避开与强制题重叠导致数学不可达的维度
                arr.append((sid, sev, None, forced))
            specs.append({"id": f"combo:{ci}:{r}", "answers_spec": arr})

    # 实例化答案向量（逐 spec 独立 rng 派生，保证可复现）
    realized: list[dict] = []
    seen_instructions: set[str] = set()
    fail = 0
    for idx, sp in enumerate(specs):
        answers_by_scale: dict[str, dict[int, int]] = {}
        ok = True
        dim_used = []
        for k, (sid, sev, dim_key, forced) in enumerate(sp["answers_spec"]):
            dims = _dim_names(sid)
            cand = None
            if dim_key is None:
                # 轮换且避开与强制题重叠的维度（数学不可达），无候选时退回全量
                cand = [d for d in dims
                        if not (set(DIM_ITEMS[sid][d]) & set(forced or {}))] or dims
            elif dim_key == "prefer_forced":
                # 危机阳性：优先落在含危机题的维度（如 PHQ-A 精神运动/风险意念）
                cand = [d for d in dims
                        if set(DIM_ITEMS[sid][d]) & set(forced or {})] or dims
            if cand is not None:
                dim = cand[(idx + k) % len(cand)]
            else:
                dim = dim_key
            sub_rng = random.Random(SEED + idx * 100 + k)
            vec = _sample_vector(sid, sev, dim, forced, sub_rng)
            if vec is None:
                ok = False
                break
            answers_by_scale[sid] = vec
            dim_used.append(f"{sid}:{dim}")
        if not ok:
            fail += 1
            continue
        assessments = _build_scenario(answers_by_scale)
        all_dims = []
        for a in assessments:
            all_dims.extend(_compute_subdims(a))
        instruction = _build_instruction(assessments, all_dims)
        if instruction in seen_instructions:
            continue
        seen_instructions.add(instruction)
        worst = max((a["severity"] for a in assessments), key=lambda s: SEVERITY_RANK[s])
        realized.append({
            "id": sp["id"],
            "instruction": instruction,
            "assessments": assessments,
            "worst_severity": worst,
            "crisis": any(a["needs_crisis_escalation"] for a in assessments),
            "target_dims": dim_used,
        })
    if fail:
        print(f"[WARN] {fail} 个场景采样失败（目标档/维度组合不可达，已跳过）")
    return realized


# ── 断言 ──────────────────────────────────────────────────────
def _section_count(text: str) -> int:
    import re
    heads = len(re.findall(r"(?m)^#{1,4}\s+\S", text))
    if heads >= 2:
        return heads
    # 无 # 标题时认「一、二、三」或「1. 2. 3.」顶层分节
    return len(re.findall(r"(?m)^\s*(?:[一二三四五六]、|\d+[.、])\s*\S", text))


def assert_narrative(text: str, worst: str, crisis: bool) -> list[str]:
    """返回失败项名列表；空列表=通过。"""
    fails = []
    n = len(text)
    if not (300 <= n <= 500):
        fails.append(f"字数{n}不在300-500")
    if _section_count(text) < 2:
        fails.append("结构化小节<2")
    for fam, kws in KW_FAMILY.items():
        if not any(k in text for k in kws):
            fails.append(f"缺{fam}方面")
    if not any(k in text for k in KW_SUGGEST):
        fails.append("无建议性措辞")
    if crisis:
        if not any(k in text for k in KW_PROFESSIONAL):
            fails.append("危机缺专业干预")
        if not any(k in text for k in KW_FIRM):
            fails.append("危机措辞不坚定")
    elif SEVERITY_RANK[worst] >= SEVERITY_RANK["moderately_severe"]:
        if not any(k in text for k in KW_PROFESSIONAL):
            fails.append("中重度+缺专业干预建议")
    else:
        hit = [k for k in KW_OVER_ESCALATE if k in text]
        if hit:
            fails.append("安全场景过度升级:" + ",".join(hit))
    return fails


# ── LLM 生成 ──────────────────────────────────────────────────
async def _call_teacher(client, model, user_content, tries, thinking: bool):
    """返回 (text, error)。"""
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": REPORT_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=8000 if thinking else 2000,
            temperature=0.6 + 0.15 * tries,
            extra_body={"enable_thinking": thinking},
            timeout=180,
        )
        return (resp.choices[0].message.content or "").strip(), None
    except Exception as e:  # noqa: BLE001
        return "", type(e).__name__


async def _gen_one(client, model, scenario, sem, state):
    async with sem:
        text, tries = "", 0
        last_fails = []
        attempts_log = []
        # 生产 instruction 原样入训练；以下脚手架只作教师约束（不进训练对）
        scaffold = (
            "\n\n（教师内部硬性要求，不属于评估报告：先一句共情（≤40字），随后只用三个小节"
            "「一、家庭支持」「二、学校配合」「三、自我调节」，每节约 100-120 字，"
            "全文含标点必须在 320-460 字之间，超过 500 字一律作废；不写称呼/问候/落款）"
        )
        if not scenario["crisis"] and SEVERITY_RANK[scenario["worst_severity"]] <= SEVERITY_RANK["moderate"]:
            scaffold += "；无自杀/自伤危机信号，禁止出现立即就医/12355/危机干预等紧急措辞"
        for tries in range(1, MAX_GEN_TRIES + 1):
            user_content = scenario["instruction"] + scaffold
            text, err = await _call_teacher(client, model, user_content, tries, thinking=True)
            state["calls"] += 1
            if err:
                attempts_log.append({"try": tries, "error": err})
                await asyncio.sleep(2 * tries)
                continue
            if not text:
                # pro 思考链偶发烧光 token 导致空 content：同题关思考兜底一次
                attempts_log.append({"try": tries, "len": 0, "fails": ["空响应:思考链吃光token"],
                                     "fallback": "enable_thinking=False"})
                text, err = await _call_teacher(client, model, user_content, tries, thinking=False)
                state["calls"] += 1
                if err:
                    attempts_log.append({"try": tries, "error": err, "thinking": False})
                    await asyncio.sleep(2 * tries)
                    continue
            last_fails = assert_narrative(text, scenario["worst_severity"], scenario["crisis"])
            attempts_log.append({"try": tries, "len": len(text), "fails": last_fails})
            if not last_fails:
                break
        if last_fails:
            state["rejected"].append({**{k: scenario[k] for k in ("id", "worst_severity", "crisis")},
                                      "fails": last_fails, "attempts": attempts_log,
                                      "text": text,
                                      "instruction": scenario["instruction"]})
            return False
        state["kept"].append({"scenario": scenario, "text": text, "tries": tries})
        return True


def _to_sharegpt(instruction: str, output: str) -> dict:
    return {"conversations": [
        {"from": "human", "value": instruction},
        {"from": "gpt", "value": output},
    ]}


async def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv(_BACKEND_ROOT.parent / ".env")
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv("DASHSCOPE_BASE_URL",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()

    rng = random.Random(SEED)
    matrix = build_matrix(rng)
    print(f"[INFO] 矩阵实例化 {len(matrix)} 个场景"
          + (f"，--limit {args.limit} 轮抽" if args.limit else ""))

    # --rescue-from：只重跑指定 rejected 文件中的场景 id（矩阵确定性，id 可复现）
    if args.rescue_from:
        rescue_ids = {json.loads(line)["id"]
                      for line in open(args.rescue_from, encoding="utf-8") if line.strip()}
        before = len(matrix)
        matrix = [s for s in matrix if s["id"] in rescue_ids]
        missing = rescue_ids - {s["id"] for s in matrix}
        print(f"[INFO] 救援模式：{len(matrix)}/{before} 个场景命中"
              + (f"，{len(missing)} 个 id 在矩阵中找不到" if missing else ""))
        if args.out == DEFAULT_OUT:
            args.out = args.out.with_name("report_train_rescue.jsonl")
        if args.provenance_out == DEFAULT_PROVENANCE_OUT:
            args.provenance_out = args.provenance_out.with_name(
                "report_train_rescue_provenance.jsonl")
        if args.rejected_out == DEFAULT_REJECTED_OUT:
            args.rejected_out = args.rejected_out.with_name("report_rejected_rescue.jsonl")

    # --limit：各 spec id 前缀分组轮抽，保证小样本仍跨档/跨量表覆盖
    if args.limit and args.limit < len(matrix):
        groups: dict[str, list[dict]] = {}
        for s in matrix:
            groups.setdefault(s["id"].split(":")[0] + ":" + s.get("worst_severity", ""), []).append(s)
        picked, gi = [], 0
        group_list = list(groups.values())
        while len(picked) < args.limit and any(group_list):
            grp = group_list[gi % len(group_list)]
            if grp:
                picked.append(grp.pop(0))
            gi += 1
        matrix = picked

    dist = Counter((s["worst_severity"], "危机" if s["crisis"] else "常规") for s in matrix)
    print("[INFO] 严重度×危机分布:", dict(dist))

    if args.no_llm:
        dbg = args.out.with_name(args.out.stem + "_instructions.jsonl")
        with open(dbg, "w", encoding="utf-8") as f:
            for s in matrix:
                f.write(json.dumps(
                    {"id": s["id"], "worst": s["worst_severity"], "crisis": s["crisis"],
                     "dims": s["target_dims"], "instruction": s["instruction"]},
                    ensure_ascii=False) + "\n")
        print(f"[OK] --no-llm：{len(matrix)} 条 instruction -> {dbg}")
        return 0

    if not api_key:
        print("[ERROR] 未设置 DASHSCOPE_API_KEY")
        return 1
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    sem = asyncio.Semaphore(args.concurrency)
    state = {"calls": 0, "kept": [], "rejected": []}

    tasks = [_gen_one(client, args.model, s, sem, state) for s in matrix]
    await asyncio.gather(*tasks)

    random.Random(SEED).shuffle(state["kept"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f, \
            open(args.provenance_out, "w", encoding="utf-8") as fp:
        for row in state["kept"]:
            s = row["scenario"]
            f.write(json.dumps(_to_sharegpt(s["instruction"], row["text"]),
                               ensure_ascii=False) + "\n")
            fp.write(json.dumps({
                "id": s["id"], "worst_severity": s["worst_severity"],
                "crisis": s["crisis"], "target_dims": s["target_dims"],
                "len": len(row["text"]), "tries": row["tries"],
                "scales": [a["scale_id"] for a in s["assessments"]],
            }, ensure_ascii=False) + "\n")
    with open(args.rejected_out, "w", encoding="utf-8") as f:
        for r in state["rejected"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("\n" + "=" * 60)
    print("report 矩阵生成报告")
    print("=" * 60)
    print(f"teacher 调用 {state['calls']} 次 | 场景 {len(matrix)} | "
          f"保留 {len(state['kept'])} | 断言丢弃 {len(state['rejected'])}")
    print("保留分布:", dict(Counter(
        ("危机" if s["scenario"]["crisis"] else s["scenario"]["worst_severity"])
        for s in state["kept"])))
    if state["rejected"]:
        print("丢弃原因:", dict(Counter(
            x for r in state["rejected"] for x in r["fails"]).most_common(8)))
    print(f"-> {args.out}\n-> {args.provenance_out}\n-> {args.rejected_out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="report 合成测评矩阵训练数据生成（18.2）")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--rejected-out", type=Path, default=DEFAULT_REJECTED_OUT)
    p.add_argument("--provenance-out", type=Path, default=DEFAULT_PROVENANCE_OUT)
    p.add_argument("--model", default=DEFAULT_TEACHER)
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--limit", type=int, default=0, help="轮抽 N 个场景（试点）")
    p.add_argument("--no-llm", action="store_true", help="只实例化矩阵/写 instruction，不调模型")
    p.add_argument("--rescue-from", type=Path, default=None,
                   help="只重跑指定 rejected jsonl 中列出的场景 id，输出 *_rescue.jsonl")
    args = p.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
