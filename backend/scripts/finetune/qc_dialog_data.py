# -*- coding: utf-8 -*-
"""dialog_train.jsonl 全量自动质检 + 分层抽样（18.2 第三批验收辅助）。

检查项：
  1. 文件行数对齐（train == provenance）
  2. 每条 gpt 轮次：长度 25-120、无闭合问句、无危机禁用语
  3. 危机类（crisis/crisis_traj）：gpt 必须含具体求助动作（12355/老师/家长/医院…）
  4. 近重复：gpt 文本归一化后重复率
  5. provenance：judge 分数分布、repair 比例、各 kind 通过率
  6. rejected：按 kind 归因
  7. 分层抽样 ~5% 写到 qc_sample.jsonl 供人工通读

用法（容器内）：
  docker exec psycheflow-backend uv run python scripts/finetune/qc_dialog_data.py
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
TRAIN = HERE / "dialog_train.jsonl"
PROV = HERE / "dialog_train_provenance.jsonl"
REJ = HERE / "dialog_rejected.jsonl"
SAMPLE_OUT = HERE / "qc_sample.jsonl"

CLOSE_Q = re.compile(r"(对吧|对吗|是不是|是吧|好吗|对不对|好不好|吗[？?]|吧[？?])")
CRISIS_HELP = re.compile(
    r"(12355|热线|老师|家长|大人|父母|妈妈|爸爸|信任的人|心理(咨询|老师|科)|医院|急诊|专业)"
)
CRISIS_BANNED = re.compile(
    r"(答应我|不要离开我|别离开我|不要做傻事|别做傻事|想想你的?(爸妈|父母|家人)|"
    r"你还有什么想不开|世界(很|还)美好|一切都会好起来|不许死|不能死)"
)

SAMPLE_FRAC = 0.05
SEED = 42

# 生产危机词表（app.core.safety.CRISIS_KEYWORDS）+ 暗语高信号短语
CRISIS_SIGNAL = re.compile(
    r"(自杀|自残|自伤|不想活|想死|结束生命|割腕|跳楼|了结自己|活不下去|轻生|活够了|没意义了|不想存在|"
    r"怎么死|死不痛苦|想跳(下去|楼|窗)|从天台|天台上|天桥上|"
    r"睡过去.*不用醒|一觉睡过去|不用醒来|再也不用醒|一直睡.*别叫醒|"
    r"刀片.*(动手|准备|藏|搞到)|吃了?.*(药片|药都)|冲到马路|"
    r"(走了|离世|去世|不在了)[^。！？!?]{0,20}去找(他|她|TA|ta)|"
    r"消失.*结束|没有我.*(轻松|好))"
)
CRISIS_RESP = re.compile(r"(12355|热线|老师|家长|大人|急诊|医院)")


def load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        print(f"[ERROR] 缺文件: {p}")
        sys.exit(1)
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--purge-sidecars", action="store_true",
                    help="把侧车行从 dialog_train.jsonl / provenance 中物理剔除（原地重写）")
    args = ap.parse_args()

    train = load_jsonl(TRAIN)
    prov = load_jsonl(PROV)
    rej = load_jsonl(REJ) if REJ.exists() else []
    print(f"[1] 行数：train={len(train)} provenance={len(prov)} rejected={len(rej)}")
    assert len(train) == len(prov), "train 与 provenance 行数不一致！"

    # ── 2. 全量 gpt 轮次规则扫描（按 provenance 的 kind 分组）──
    issues: list[tuple[str, str, str]] = []
    kind_turn_counts: Counter = Counter()
    lens: list[int] = []
    rag_missing = 0
    for row, p in zip(train, prov):
        kind = p["kind"]
        convs = row["conversations"]
        gpts = [c["value"] for c in convs if c["from"] == "gpt"]
        kind_turn_counts[kind] += len(gpts)
        # RAG-grounded 类必须带来源（生成器设计：检索失败即丢场景）
        if kind in ("consult", "vc_traj") and not p.get("rag_sources"):
            rag_missing += 1
        for i, g in enumerate(gpts, 1):
            lens.append(len(g))
            if not (25 <= len(g) <= 120):
                issues.append((p["id"], f"{kind}#轮{i}", f"长度 {len(g)} 越界"))
            if CLOSE_Q.search(g):
                issues.append((p["id"], f"{kind}#轮{i}", "闭合问句"))
            if CRISIS_BANNED.search(g):
                issues.append((p["id"], f"{kind}#轮{i}", "危机禁用语"))
            # 危机求助动作只要求末轮（crisis_traj 首轮是暗语试探，按设计温和点破+开放提问）
            is_crisis_last = kind in ("crisis", "crisis_traj") and i == len(gpts)
            if is_crisis_last:
                if not CRISIS_HELP.search(g):
                    issues.append((p["id"], f"{kind}#轮{i}", "危机末轮无具体求助动作"))
                if not re.search(r"(我听到|我在|陪|你不是一个人|你的感受|在乎|我会认真|痛苦)", g):
                    issues.append((p["id"], f"{kind}#轮{i}", "危机末轮缺温度承接"))
    print(f"    RAG-grounded 类缺来源: {rag_missing}")

    print(f"[2] gpt 轮次总数={sum(kind_turn_counts.values())}；"
          f"长度 min/中位/max = {min(lens)}/{sorted(lens)[len(lens)//2]}/{max(lens)}")
    print(f"    各 kind 轮次: {dict(kind_turn_counts)}")
    if issues:
        c = Counter(x[1].split("#")[0] for x in issues)
        print(f"    ⚠ 规则违规 {len(issues)} 条，按 kind: {dict(c)}")
        for x in issues[:15]:
            print("     ", x)
    else:
        print("    ✓ 无闭合问句 / 无长度越界 / 危机回复均含求助动作 / 无禁用语")

    # ── 3. 近重复（末轮归一化）──
    def norm(t: str) -> str:
        return re.sub(r"[\s，。！？、,.!?~～…—\-—:：;；]", "", t)

    sig: dict[str, str] = {}
    dup = 0
    for row, p in zip(train, prov):
        last_g = [c["value"] for c in row["conversations"] if c["from"] == "gpt"][-1]
        key = norm(last_g)[:60]
        if key in sig:
            dup += 1
        else:
            sig[key] = p["id"]
    print(f"[3] 末轮前60字归一化重复：{dup}/{len(train)}（{dup/len(train)*100:.1f}%）")

    # ── 4. provenance 分布 ──
    judge_scores = [p["judge"].get("score", 0) for p in prov]
    repaired = sum(1 for p in prov if p.get("repaired_turns"))
    best_k = Counter(str(p.get("best_k")) for p in prov)
    print(f"[4] judge 分数分布: {dict(sorted(Counter(judge_scores).items()))}")
    print(f"    repair 救援成功: {repaired}/{len(prov)}（{repaired/len(prov)*100:.1f}%）")
    print(f"    best_k 分布: {dict(best_k)}")

    kept_kind = Counter(p["kind"] for p in prov)
    rej_kind = Counter(r.get("kind", "?") for r in rej)
    print("[5] 各 kind 通过率:")
    for k in sorted(set(kept_kind) | set(rej_kind)):
        tot = kept_kind[k] + rej_kind[k]
        print(f"    {k:12s} 保留 {kept_kind[k]:4d} / {tot:4d} = "
              f"{kept_kind[k]/tot*100:5.1f}%  丢弃 {rej_kind[k]}")
    other_fail = Counter(f for r in rej for f in r.get("fails", [])
                         if f != "无候选同时通过正则+判官≥4")
    if other_fail:
        print("    非判官原因丢弃:", other_fail.most_common(10))

    # ── 5.5 侧车扫描：非危机行的 human 轮次出现危机暗语，gpt 却未走危机响应 ──
    sidecars: list[tuple[str, int, str]] = []
    self_corrected = 0
    for row, p in zip(train, prov):
        if p["kind"] in ("crisis", "crisis_traj"):
            continue
        convs = row["conversations"]
        for ti in range(0, len(convs), 2):
            h = convs[ti]["value"]
            g = convs[ti + 1]["value"]
            if CRISIS_SIGNAL.search(h):
                if CRISIS_RESP.search(g):
                    self_corrected += 1
                else:
                    sidecars.append((p["id"], ti // 2 + 1, h[:60]))
    print(f"[5.5] 非危机轨迹中的危机暗语：侧车 {len(sidecars)} 条（teacher 自行纠正 "
          f"{self_corrected} 条）；侧车率 {len(sidecars)/len(train)*100:.2f}%")
    by_k = Counter(x[0].split(":")[0] for x in sidecars)
    print("      侧车按 kind:", dict(by_k))
    for sid, turn, h in sidecars[:20]:
        print(f"       - {sid} 轮{turn}: {h}")
    sidecar_ids = {x[0] for x in sidecars}
    if sidecar_ids:
        with open(HERE / "dialog_sidecar_ids.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(sidecar_ids)))
        print(f"      -> 侧车 id 清单写至 dialog_sidecar_ids.txt（{len(sidecar_ids)} 行）")

    if args.purge_sidecars and sidecar_ids:
        kept = [(r, p) for r, p in zip(train, prov) if p["id"] not in sidecar_ids]
        with open(TRAIN, "w", encoding="utf-8") as f:
            for r, _ in kept:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(PROV, "w", encoding="utf-8") as f:
            for _, p in kept:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"[PURGE] 已剔除 {len(train)-len(kept)} 条侧车，剩余 {len(kept)} 条 "
              f"（{TRAIN.name} / {PROV.name} 原地重写）")
        train, prov = [r for r, _ in kept], [p for _, p in kept]

    # ── 6. 分层抽样 5%（危机类抽 10%，每 kind 至少 5 条）──
    by_kind: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(prov):
        by_kind[p["kind"]].append(i)
    rng = random.Random(SEED)
    picked: list[int] = []
    for kind, idxs in by_kind.items():
        frac = 0.10 if kind in ("crisis", "crisis_traj", "vc_traj") else SAMPLE_FRAC
        n = max(5, round(len(idxs) * frac))
        picked.extend(rng.sample(idxs, min(n, len(idxs))))
    rng.shuffle(picked)
    with open(SAMPLE_OUT, "w", encoding="utf-8") as f:
        for i in picked:
            f.write(json.dumps({"provenance": prov[i], "row": train[i]},
                               ensure_ascii=False) + "\n")
    # 可读版（人工通读用）
    readable = HERE / "qc_sample_readable.txt"
    with open(readable, "w", encoding="utf-8") as f:
        for i in picked:
            p, row = prov[i], train[i]
            f.write(f"{'='*70}\n[{p['kind']}] {p['id']} | {p['theme']} | "
                    f"{p['grade']} | judge={p['judge'].get('score')} | "
                    f"best_k={p.get('best_k')} | repair={p.get('repaired_turns')}\n")
            if p.get("rag_sources"):
                f.write(f"RAG: {p['rag_sources']}\n")
            for c in row["conversations"]:
                role = "学生" if c["from"] == "human" else "暖暖"
                f.write(f"{role}: {c['value']}\n")
            f.write("\n")
    print(f"[6] 分层抽样 {len(picked)} 条 -> {SAMPLE_OUT.name} / {readable.name}")
    print("    抽样分布:", dict(Counter(prov[i]['kind'] for i in picked)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
