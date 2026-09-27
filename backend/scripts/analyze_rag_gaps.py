#!/usr/bin/env python3
"""RAG 弱命中周度聚类（docs/数据与模型详解.md 18.1.7 第 1 项待办落地）。

数据源与三类信号：
  ① 被过滤零命中：logs/rag_search_YYYYMMDD.jsonl 中 caller∈{intervention,case}
     且 top1_passed=false（top1 距离超阈值被过滤）；
  ② 放行但不可用：同上 caller 范围，top1_passed=true 但 result_count=0
     （dedup/危机标签清空）或 top3 无「做法型」标签片段；
  ③ eval miss：scripts/eval/results/rag_eval_latest.json 的 misses 列表
     （dialog_smoke 仅打 stdout 无结构化产物，不参与，失败样本人工归档）。

隐私纪律与埋点一致：只统计 query 文本与检索指标；eval/api/unknown 流量一律排除。

立项门槛：同一主题簇单周 ≥5 次弱命中 → 排期补文档（18.1.7 第二批缺口主题从这来）。

用法（零第三方依赖，容器内或宿主机均可跑）：
  docker exec psycheflow-backend uv run python scripts/analyze_rag_gaps.py           # 最近 7 天
  docker exec psycheflow-backend uv run python scripts/analyze_rag_gaps.py --days 30
  docker exec psycheflow-backend uv run python scripts/analyze_rag_gaps.py --all    # 全量历史
  docker exec psycheflow-backend uv run python scripts/analyze_rag_gaps.py --out report.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# caller 白名单：只统计真实用户流量（eval=评测 / api=调试 / unknown=单测 均排除）
REAL_TRAFFIC_CALLERS = {"intervention", "case"}

# 「做法型」标签 = 受控词表中表示可操作方法的子集（其余标签多为科普/诊断/评估类）
# 宽松近似：top3 命中里只要有一个做法型标签即认为「召了可用」，人工周度评审做最终判断
METHOD_TAGS = {"放松", "CBT", "DBT", "沟通", "共情", "自助", "时间管理",
               "情绪", "睡眠", "考试", "亲子", "学习"}

# 主题关键词表：顺序敏感（特异主题在前，泛化在后），零 LLM 硬编码
# 危机类优先用埋点 is_crisis 字段（与 app.core.safety.CRISIS_KEYWORDS 同源），不在此表
TOPIC_RULES: list[tuple[str, str]] = [
    ("自伤", r"自残|自伤|割手|划手|伤害自己|伤害不了"),
    ("躯体化", r"头疼|头痛|肚子疼|胃疼|恶心|躯体|身体不舒服|查不出|体检|心悸|胸闷"),
    ("社交恐惧", r"社交|当众|发言|上台|演讲|尴尬|不敢说话|同学面前"),
    ("家庭变故", r"离婚|离异|分手|留守|二胎|单亲|隔代|重组家庭|搬去"),
    ("亲子", r"爸妈|父母|家长|妈妈|爸爸|唠叨|管我|逼我|家里人"),
    ("外貌焦虑", r"外貌|长相|变丑|太胖|太瘦|身材|体重|痘痘|被笑"),
    ("厌学", r"厌学|不想上学|不想去学校|逃学|逃课|请假不去"),
    ("考试", r"考试|考砸|考后|成绩|排名|中考|高考|失利|复习|挂科"),
    ("睡眠", r"睡不着|失眠|噩梦|早醒|熬夜|作息|多梦|赖床"),
    ("网瘾/物质", r"游戏|手机|短视频|网瘾|上瘾|停不下来|电子烟|抽烟|喝酒"),
    ("愤怒", r"发火|愤怒|发脾气|摔东西|打人|打架|控制不住|暴躁"),
    ("人际", r"同学|朋友|孤立|排挤|霸凌|欺负|绝交|被嘲笑|没朋友"),
    ("创伤", r"创伤|事故|阴影|闪回|吓到|惊魂"),
    ("哀伤", r"去世|走了|失去|亲人死|哀伤|怀念|葬礼"),
    ("情绪低落", r"难过|伤心|低落|想哭|哭|抑郁|没意思|空虚|emo|委屈"),
    ("焦虑", r"焦虑|紧张|担心|害怕|心慌|压力|恐慌|恐惧"),
]
_TOPIC_COMPILED = [(topic, re.compile(pat)) for topic, pat in TOPIC_RULES]

WEEK_THRESHOLD = 5  # 立项门槛：同主题簇单周弱命中次数


def resolve_logs_dir(explicit: str | None) -> str:
    """日志目录：--logs-dir > 环境变量 RAG_LOGS_DIR > 按候选路径自动探测。

    优先 settings.logs_dir 对应位置（<data>/logs，容器内 /app/data/logs），都能命中。
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("RAG_LOGS_DIR"):
        candidates.append(os.environ["RAG_LOGS_DIR"])
    # settings.logs_dir = <sqlite 所在目录>/logs，容器内即 /app/data/logs（默认 sqlite 在 data/）
    candidates.append(os.path.join(SCRIPT_DIR, "..", "data", "logs"))        # 容器内 /app/data/logs
    candidates.append(os.path.join(SCRIPT_DIR, "..", "..", "data", "logs"))  # 宿主机仓库根 data/logs
    candidates.append(os.path.join(SCRIPT_DIR, "..", "logs"))                # 兼容审计日志挂载
    candidates.append(os.path.join(SCRIPT_DIR, "..", "..", "logs"))          # 宿主机仓库根 logs/
    for cand in candidates:
        cand = os.path.abspath(cand)
        if glob.glob(os.path.join(cand, "rag_search_*.jsonl")):
            return cand
    # 都没有埋点文件时返回最后一个候选，让主流程报「无数据」而非路径不存在
    return os.path.abspath(candidates[-1])


def load_trace_events(logs_dir: str, since: dt.datetime | None) -> tuple[list[dict], int]:
    """读取全部 rag_search_*.jsonl，过滤时间与 caller 白名单。返回 (events, 解析失败行数)。"""
    events: list[dict] = []
    bad = 0
    for path in sorted(glob.glob(os.path.join(logs_dir, "rag_search_*.jsonl"))):
        with open(path, encoding="utf-8-sig") as f:  # utf-8-sig 容忍手工编辑可能引入的 BOM
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                if ev.get("caller") not in REAL_TRAFFIC_CALLERS:
                    continue
                if since is not None:
                    try:
                        ts = dt.datetime.fromisoformat(ev["ts"])
                    except (KeyError, ValueError):
                        bad += 1
                        continue
                    if ts < since:
                        continue
                events.append(ev)
    return events, bad


def classify_topic(ev: dict) -> str:
    """主题归类：危机用 is_crisis 字段，其余按关键词表顺序匹配，兜底「未分类」。"""
    if ev.get("is_crisis"):
        return "危机"
    query = ev.get("query") or ""
    for topic, pat in _TOPIC_COMPILED:
        if pat.search(query):
            return topic
    return "未分类"


def is_signal1(ev: dict) -> bool:
    """信号①：top1 被阈值过滤（零命中）。"""
    return ev.get("top1_passed") is False


def is_signal2(ev: dict) -> bool:
    """信号②：已放行但不可用——全部被 dedup/危机过滤清空，或 top3 无做法型标签。"""
    if ev.get("top1_passed") is not True:
        return False
    if ev.get("result_count", 0) == 0:
        return True
    for r in ev.get("results") or []:
        tags = r.get("tags") or []
        if isinstance(tags, str):  # 防御：tags 异常序列化成裸字符串时按单标签处理
            tags = [tags]
        if set(tags) & METHOD_TAGS:
            return False  # top3 里有做法型片段 → 召了可用
    return True  # 全部为科普/诊断类 → 弱命中


def load_eval_misses() -> list[dict]:
    """信号③：最近一次 eval_rag 的 miss 样本（无产物文件则返回空）。"""
    path = os.path.join(SCRIPT_DIR, "eval", "results", "rag_eval_latest.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    misses = report.get("misses") or []
    out = []
    for m in misses:
        query = m.get("query", "")
        topic = "未分类"
        for name, pat in _TOPIC_COMPILED:
            if pat.search(query):
                topic = name
                break
        out.append({**m, "topic": topic})
    return out


def week_key(ev: dict) -> str:
    """ISO 周键，如 2026-W39。ts 解析失败归为 unknown-week。"""
    try:
        ts = dt.datetime.fromisoformat(ev["ts"])
        return f"{ts.isocalendar().year}-W{ts.isocalendar().week:02d}"
    except (KeyError, ValueError):
        return "unknown-week"


def cluster(events: list[dict]) -> dict:
    """按 (周, 主题) 聚类同一信号的弱命中。"""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for ev in events:
        buckets[(week_key(ev), classify_topic(ev))].append(ev)
    clusters = {}
    for (week, topic), evs in sorted(buckets.items()):
        query_counts = Counter((ev.get("query") or "").strip() for ev in evs)
        clusters[f"{week}|{topic}"] = {
            "count": len(evs),
            "needs_action": len(evs) >= WEEK_THRESHOLD,
            "intent": sorted({ev.get("intent", "unknown") for ev in evs}),
            "embed_mode": sorted({ev.get("embed_mode", "?") for ev in evs}),
            "top1_distance": sorted({ev.get("top1_distance") for ev in evs})[:5],
            "queries": dict(query_counts.most_common()),
        }
    return clusters


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 弱命中周度聚类（18.1.7）")
    parser.add_argument("--days", type=int, default=7, help="统计最近 N 天（默认 7）")
    parser.add_argument("--all", action="store_true", help="统计全部历史日志")
    parser.add_argument("--logs-dir", default=None, help="埋点日志目录（默认自动探测）")
    parser.add_argument("--out", default=None, help="同时输出 JSON 报告到指定路径")
    args = parser.parse_args()

    since = None if args.all else dt.datetime.now() - dt.timedelta(days=args.days)
    logs_dir = resolve_logs_dir(args.logs_dir)
    events, bad_lines = load_trace_events(logs_dir, since)
    eval_misses = load_eval_misses()

    sig1 = [ev for ev in events if is_signal1(ev)]
    sig2 = [ev for ev in events if is_signal2(ev)]
    caller_counts = Counter(ev.get("caller") for ev in events)

    sig1_clusters = cluster(sig1)
    sig2_clusters = cluster(sig2)
    eval_clusters: dict[str, dict] = {}
    if eval_misses:
        bucket: dict[str, list[dict]] = defaultdict(list)
        for m in eval_misses:
            bucket[m["topic"]].append(m)
        for topic, ms in sorted(bucket.items()):
            eval_clusters[topic] = {
                "count": len(ms),
                "queries": [m["query"] for m in ms],
                "expect": [m.get("expect") for m in ms],
                "got": [m.get("got") for m in ms],
            }

    action_items: list[str] = []
    for name, clusters in (("①", sig1_clusters), ("②", sig2_clusters)):
        for key, info in clusters.items():
            if info["needs_action"]:
                action_items.append(f"信号{name} {key}：{info['count']} 次 ≥ {WEEK_THRESHOLD}")
    for topic, info in eval_clusters.items():
        if info["count"] >= WEEK_THRESHOLD:
            action_items.append(f"信号③ eval-miss {topic}：{info['count']} 条 ≥ {WEEK_THRESHOLD}")

    print("=" * 60)
    print("RAG 弱命中周度聚类（18.1.7 立项依据）")
    print("=" * 60)
    print(f"日志目录: {logs_dir}")
    span = "全部历史" if args.all else f"最近 {args.days} 天"
    print(f"统计范围: {span} | 真实流量 {len(events)} 条（{dict(caller_counts) or '无'}）| 解析失败行 {bad_lines}")
    print(f"信号① 被过滤零命中: {len(sig1)} 条 | 信号② 放行但不可用: {len(sig2)} 条 | 信号③ eval miss: {len(eval_misses)} 条")

    def print_clusters(title: str, clusters: dict) -> None:
        print(f"\n--- {title} ---")
        if not clusters:
            print("  （无）")
        for key, info in clusters.items():
            flag = " ★立项" if info["needs_action"] else ""
            print(f"  [{key}] {info['count']} 次{flag} | intent={info['intent']} | mode={info['embed_mode']} | top1距离={info['top1_distance']}")
            for q, c in info["queries"].items():
                print(f"      ×{c}  {q}")

    print_clusters("信号① 被过滤零命中（阈值挡掉的 query）", sig1_clusters)
    print_clusters("信号② 放行但 top3 无做法型片段（召了不可用）", sig2_clusters)
    print("\n--- 信号③ eval_rag miss（rag_eval_latest.json）---")
    if not eval_clusters:
        print("  （无，或无评测产物）")
    for topic, info in eval_clusters.items():
        print(f"  [{topic}] {info['count']} 条")
        for q in info["queries"]:
            print(f"      {q}")

    print("\n" + "=" * 60)
    if action_items:
        print(f"★ 立项建议（同主题簇单周 ≥{WEEK_THRESHOLD} 次弱命中 → 排期补文档）:")
        for item in action_items:
            print(f"  - {item}")
    else:
        print(f"本周无主题簇达到 {WEEK_THRESHOLD} 次门槛，无需立项。")

    if args.out:
        report = {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "range": span,
            "logs_dir": logs_dir,
            "real_traffic": dict(caller_counts),
            "signal1_total": len(sig1),
            "signal2_total": len(sig2),
            "signal3_total": len(eval_misses),
            "signal1_clusters": sig1_clusters,
            "signal2_clusters": sig2_clusters,
            "signal3_eval_misses": eval_clusters,
            "action_items": action_items,
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 报告已写入: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
