# -*- coding: utf-8 -*-
"""RAG 检索评测脚本（18.1.5 质量护栏）。

对 scripts/eval/rag_eval_dataset.json 中的标注 query 逐条调用真实
rag_service.search(top_k=3)，统计：
  - hit@1 / recall@3：期望文件是否出现在第 1 / 前 3 条检索结果
  - MRR：期望文件首次命中位置的倒数平均
  - 文件覆盖：每个期望文件至少被命中一次
每次扩充知识库后跑一遍，recall@3 下降即说明新内容引入了检索退化。

用法（容器内，需先 POST /api/rag/build 完成最新索引）：
  docker exec psycheflow-backend uv run python scripts/eval_rag.py           # 全量
  docker exec psycheflow-backend uv run python scripts/eval_rag.py --limit 5 # 冒烟
  docker exec psycheflow-backend uv run python scripts/eval_rag.py --verbose # 打印每条结果

结果写入 scripts/eval/results/rag_eval_<时间戳>.json 并同步覆盖 rag_eval_latest.json。
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from app.rag.service import rag_service  # noqa: E402

DATASET_PATH = "/app/scripts/eval/rag_eval_dataset.json"
RESULTS_DIR = "/app/scripts/eval/results"
TOP_K = 3


def _expect_list(case: dict) -> list[str]:
    exp = case["expect"]
    return exp if isinstance(exp, list) else [exp]


async def run(dataset_path: str, limit: int | None, verbose: bool) -> dict:
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["cases"]
    if limit:
        cases = cases[:limit]

    total = len(cases)
    hit1 = 0
    hit3 = 0
    mrr_sum = 0.0
    per_file: dict[str, dict] = {}
    misses: list[dict] = []
    t0 = time.time()

    for i, case in enumerate(cases):
        query = case["query"]
        expects = _expect_list(case)
        # caller="eval"：评测流量打标，18.1.7 周度弱命中聚类须排除
        results = await rag_service.search(query, top_k=TOP_K, caller="eval")
        got_sources = [r.get("source", "") for r in results]

        rank = next((idx + 1 for idx, src in enumerate(got_sources) if src in expects), None)
        ok1 = rank == 1
        ok3 = rank is not None
        hit1 += ok1
        hit3 += ok3
        mrr_sum += (1.0 / rank) if rank else 0.0

        for f in expects:
            slot = per_file.setdefault(f, {"total": 0, "hit": 0})
            slot["total"] += 1
            if ok3:
                slot["hit"] += 1

        if verbose:
            mark = f"hit@{rank}" if rank else "MISS"
            print(f"[{i+1}/{total}] {mark} | {query[:30]} | got={got_sources}")

        if not ok3:
            misses.append({
                "query": query,
                "note": case.get("note", ""),
                "expect": expects,
                "got": [{"source": r.get("source"), "distance": round(r.get("distance", 0), 3)}
                        for r in results],
            })

    elapsed = time.time() - t0
    covered = {f for f, s in per_file.items() if s["hit"] > 0}
    all_expected = {f for case in cases for f in _expect_list(case)}
    uncovered = sorted(all_expected - covered)

    summary = {
        "total": total,
        "hit@1": round(hit1 / total, 4),
        "recall@3": round(hit3 / total, 4),
        "mrr": round(mrr_sum / total, 4),
        "expected_files": len(all_expected),
        "covered_files": len(covered),
        "uncovered_files": uncovered,
        "miss_count": len(misses),
        "elapsed_sec": round(elapsed, 1),
    }
    return {"summary": summary, "misses": misses, "per_file": per_file}


def save_results(report: dict) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS_DIR, f"rag_eval_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    latest = os.path.join(RESULTS_DIR, "rag_eval_latest.json")
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


async def main():
    parser = argparse.ArgumentParser(description="RAG 检索评测")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（冒烟）")
    parser.add_argument("--verbose", action="store_true", help="打印每条检索结果")
    args = parser.parse_args()

    report = await run(DATASET_PATH, args.limit, args.verbose)
    s = report["summary"]
    print("\n===== RAG 检索评测结果 =====")
    print(f"样本数: {s['total']}  耗时: {s['elapsed_sec']}s")
    print(f"hit@1:    {s['hit@1']*100:.1f}%")
    print(f"recall@3: {s['recall@3']*100:.1f}%")
    print(f"MRR:      {s['mrr']}")
    print(f"文件覆盖: {s['covered_files']}/{s['expected_files']}")
    if s["uncovered_files"]:
        print(f"未覆盖文件: {s['uncovered_files']}")
    if report["misses"]:
        print(f"\n未命中 {s['miss_count']} 条：")
        for m in report["misses"]:
            print(f"  - [{m['note']}] {m['query'][:34]}")
            print(f"    expect={m['expect']}")
            print(f"    got={[(g['source'], g['distance']) for g in m['got']]}")
    path = save_results(report)
    print(f"\n结果已写入: {path}")


if __name__ == "__main__":
    asyncio.run(main())
