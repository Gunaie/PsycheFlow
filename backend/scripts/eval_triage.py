# -*- coding: utf-8 -*-
"""Triage 意图分类评测脚本（P2 LLM 输出评估体系）。

对 scripts/eval/triage_dataset.json 中的标注样本逐条调用真实 triage_node，
统计 4 类意图（求助/倾诉/咨询/危机）的总体与分类别准确率。

用法（容器内，需 .env 有真实 DASHSCOPE_API_KEY）：
  docker exec psycheflow-backend uv run python scripts/eval_triage.py           # 全量
  docker exec psycheflow-backend uv run python scripts/eval_triage.py --limit 8 # 冒烟
  docker exec psycheflow-backend uv run python scripts/eval_triage.py --verbose # 打印每条对错

说明：
  - 危机类样本命中 detect_crisis_with_words 硬编码词表（零 LLM），是安全回归——
    期望其准确率恒为 100%，任何下降即阻断发布
  - LLM 类样本（求助/倾诉/咨询）受模型与温度影响，准确率波动属正常，重点看相对基线的变化
  - 结果写入 scripts/eval/results/triage_eval_<时间戳>.json 并同步覆盖 triage_eval_latest.json
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from app.agents.nodes.triage import triage_node  # noqa: E402
from app.core.config import settings  # noqa: E402

DATASET_PATH = "/app/scripts/eval/triage_dataset.json"
RESULTS_DIR = "/app/scripts/eval/results"


async def run(dataset_path: str, limit: int | None, verbose: bool) -> dict:
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]
    if limit:
        cases = cases[:limit]

    per_intent: dict[str, dict] = {}
    failures: list[dict] = []
    t0 = time.time()

    for i, case in enumerate(cases):
        expected = case["intent"]
        state = {"user_message": case["message"], "agent_trace": []}
        try:
            result = await triage_node(state)
        except Exception as e:
            got = f"EXCEPTION:{type(e).__name__}"
        else:
            got = result.get("triage_intent", "")

        ok = got == expected
        slot = per_intent.setdefault(expected, {"total": 0, "correct": 0})
        slot["total"] += 1
        if ok:
            slot["correct"] += 1
        else:
            failures.append(
                {"index": i, "note": case.get("note", ""), "expected": expected, "got": got, "message": case["message"]}
            )
        if verbose:
            mark = "OK  " if ok else "MISS"
            print(f"[{mark}] #{i:02d} 期望={expected} 实际={got} | {case['message'][:30]}")

    total = len(cases)
    correct = total - len(failures)
    is_local = str(settings.llm_mode).strip().lower() == "local"
    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": settings.llm_mode,
        "model": settings.local_model if is_local else settings.model_triage,
        "dataset": os.path.basename(dataset_path),
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0,
        "per_intent": {
            k: {"total": v["total"], "correct": v["correct"], "accuracy": round(v["correct"] / v["total"], 4)}
            for k, v in per_intent.items()
        },
        "failures": failures,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Triage 意图分类评测")
    parser.add_argument("--limit", type=int, default=None, help="只评测前 N 条（冒烟用）")
    parser.add_argument("--verbose", action="store_true", help="打印每条判定结果")
    parser.add_argument("--dataset", default=DATASET_PATH, help="数据集路径")
    args = parser.parse_args()

    if not settings.dashscope_api_key:
        print("[中止] 未配置 DASHSCOPE_API_KEY：本评测调用真实 LLM，请在有凭据的环境（容器）运行", file=sys.stderr)
        return 2

    summary = asyncio.run(run(args.dataset, args.limit, args.verbose))

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    detail_path = os.path.join(RESULTS_DIR, f"triage_eval_{stamp}.json")
    latest_path = os.path.join(RESULTS_DIR, "triage_eval_latest.json")
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n===== Triage 评测结果（{summary['mode']} 模式，模型 {summary['model']}）=====")
    print(f"总体: {summary['correct']}/{summary['total']} = {summary['accuracy']:.1%}（耗时 {summary['elapsed_sec']}s）")
    for intent, v in summary["per_intent"].items():
        print(f"  {intent}: {v['correct']}/{v['total']} = {v['accuracy']:.1%}")
    if summary["failures"]:
        print("误判明细：")
        for f in summary["failures"]:
            print(f"  #{f['index']:02d} [{f['note']}] 期望 {f['expected']} → 实际 {f['got']} | {f['message'][:36]}")
    print(f"结果已写入: {latest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
