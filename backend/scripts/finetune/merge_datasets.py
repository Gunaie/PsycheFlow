# -*- coding: utf-8 -*-
"""合并三任务 SFT 数据（dialog / report / triage），注入各自生产 system prompt，
并用零依赖 MinHash-LSH 做近重复去重（18.2 定版）。

system prompt 与生产代码同源（三处单一事实源）：
  dialog → app.agents.personas.build_system_prompt(get_persona("default"))
  triage → app.agents.prompts.TRIAGE_SYSTEM
  report → app.reports.service.REPORT_SYSTEM
容器内先导出为 txt（云 GPU 上没有 app 代码，merge 只读 txt，可离线跑）：
  docker exec psycheflow-backend uv run python scripts/finetune/merge_datasets.py --emit-systems

合并（容器内或云 GPU 均可，零三方依赖）：
  python3 merge_datasets.py --task all
  python3 merge_datasets.py --task dialog
  python3 merge_datasets.py --task triage --threshold 0.85

默认输入/输出（均在脚本同目录）：
  dialog : dialog_train.jsonl + synthetic_dialog.jsonl (+deepwell*.jsonl 若存在)
           → dialog_merged.jsonl   （system_dialog.txt）
  report : report_train.jsonl      → report_merged.jsonl （system_report.txt）
  triage : triage_train.jsonl      → triage_merged.jsonl （system_triage.txt）

旧调用方式保持兼容（cloud_train.sh 不用改）：
  python3 merge_datasets.py a.jsonl b.jsonl -o dialog_merged.jsonl
  等价于 --task dialog --inputs a.jsonl b.jsonl；system 优先读
  system_dialog.txt，不存在则回退旧版 system_prompt.txt。

去重口径：每条样本把全部 human/gpt 文本归一化（去空白标点、转小写）后取
4-gram 字符片，MinHash(128 perm) + LSH(32 bands × 4 rows) 召回候选对，
候选对算精确 Jaccard，≥threshold（默认 0.8）丢弃后出现的一条。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent

# ── 任务注册表：默认输入 / 输出 / system 文件 ─────────────────
TASKS = {
    "dialog": {
        "out": "dialog_merged.jsonl",
        "system": "system_dialog.txt",
        "system_legacy": "system_prompt.txt",
        "inputs": ["dialog_train.jsonl", "synthetic_dialog.jsonl"],
        "optional_globs": ["deepwell*.jsonl"],
    },
    "report": {
        "out": "report_merged.jsonl",
        "system": "system_report.txt",
        "inputs": ["report_train.jsonl"],
    },
    "triage": {
        "out": "triage_merged.jsonl",
        "system": "system_triage.txt",
        "inputs": ["triage_train.jsonl"],
    },
}

# ── MinHash 参数 ─────────────────────────────────────────────
NUM_PERM = 128
NUM_BANDS = 32
ROWS_PER_BAND = 4          # 32×4=128；LSH 候选阈值约 (1/32)^(1/4)≈0.42，靠精确 Jaccard 收口
SHINGLE_K = 4
_P = (1 << 61) - 1


def _perm_params(seed: int = 42) -> list[tuple[int, int]]:
    rng = random.Random(seed)
    return [(rng.randrange(1, _P), rng.randrange(0, _P)) for _ in range(NUM_PERM)]


PERMS = _perm_params()


# ── 加载 / 校验 ──────────────────────────────────────────────
def load_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [WARN] {path.name}:{ln} 非法 JSON，跳过: {e}")
                continue
            convs = obj.get("conversations")
            if not isinstance(convs, list) or len(convs) < 2:
                print(f"  [WARN] {path.name}:{ln} conversations 缺失/不足，跳过")
                continue
            if any(c.get("from") not in ("human", "gpt") or not c.get("value")
                   for c in convs):
                print(f"  [WARN] {path.name}:{ln} 角色或空值异常，跳过")
                continue
            out.append({"conversations": convs})
    return out


# ── 近重复去重（MinHash-LSH + 精确 Jaccard）──────────────────
def _normalize(convs: list[dict]) -> str:
    text = "".join(c["value"] for c in convs)
    return "".join(ch for ch in text if ch.isalnum()).lower()


def _shingles(norm: str) -> set[int]:
    if len(norm) < SHINGLE_K:
        # 极短文本（实际数据不会走到）：整条作为一个片，保证可比较
        return {int.from_bytes(hashlib.md5(norm.encode("utf-8")).digest()[:8], "little")}
    return {
        int.from_bytes(hashlib.md5(norm[i:i + SHINGLE_K].encode("utf-8")).digest()[:8],
                       "little")
        for i in range(len(norm) - SHINGLE_K + 1)
    }


def _minhash(shingles: set[int]) -> tuple[int, ...]:
    sig = [1 << 63] * NUM_PERM
    for h in shingles:
        for i, (a, b) in enumerate(PERMS):
            v = (a * h + b) % _P
            if v < sig[i]:
                sig[i] = v
    return tuple(sig)


def dedupe(samples: list[dict], threshold: float,
           verbose: bool = True) -> tuple[list[dict], int]:
    """返回去重后的样本与被丢弃条数。输入顺序即优先级（先出现的保留）。"""
    sigs: list[tuple[int, ...]] = []
    sets: list[set[int]] = []
    for s in samples:
        sh = _shingles(_normalize(s["conversations"]))
        sets.append(sh)
        sigs.append(_minhash(sh))

    # LSH 分桶召回候选对
    candidates: set[tuple[int, int]] = set()
    for band in range(NUM_BANDS):
        lo = band * ROWS_PER_BAND
        buckets: dict[tuple, list[int]] = {}
        for i, sig in enumerate(sigs):
            key = sig[lo:lo + ROWS_PER_BAND]
            buckets.setdefault(key, []).append(i)
        for idxs in buckets.values():
            if len(idxs) > 1:
                for x in range(len(idxs)):
                    for y in range(x + 1, len(idxs)):
                        candidates.add((idxs[x], idxs[y]))

    # 精确 Jaccard 收口；union-find 式传播（A≈B、B≈C 时也去 C）
    dropped: set[int] = set()
    checked = 0
    for i, j in sorted(candidates):
        if j in dropped:
            continue
        checked += 1
        si, sj = sets[i], sets[j]
        inter = len(si & sj)
        jac = inter / len(si | sj) if si or sj else 1.0
        if jac >= threshold:
            dropped.add(j)
            if verbose:
                prev = samples[i]["conversations"][0]["value"][:30].replace("\n", " ")
                cur = samples[j]["conversations"][0]["value"][:30].replace("\n", " ")
                print(f"    近重复 J={jac:.2f} 丢弃「{cur}…」(≈「{prev}…」)")
    kept = [s for idx, s in enumerate(samples) if idx not in dropped]
    if verbose:
        print(f"  LSH 候选对 {len(candidates)}，精确核验 {checked}，"
              f"去重 {len(dropped)} 条")
    return kept, len(dropped)


# ── system prompt 导出（容器内；与生产代码同源）──────────────
def emit_systems() -> int:
    backend_root = HERE.parent.parent
    sys.path.insert(0, str(backend_root))
    try:
        from app.agents.personas import build_system_prompt, get_persona  # noqa: E402
        from app.agents.prompts import TRIAGE_SYSTEM  # noqa: E402
        from app.reports.service import REPORT_SYSTEM  # noqa: E402
    except ImportError as e:
        print(f"[ERROR] 需在后端容器内运行 --emit-systems（缺 app 代码）: {e}")
        return 1
    systems = {
        "system_dialog.txt": build_system_prompt(get_persona("default")),
        "system_triage.txt": TRIAGE_SYSTEM,
        "system_report.txt": REPORT_SYSTEM,
    }
    for name, content in systems.items():
        p = HERE / name
        p.write_text(content.strip() + "\n", encoding="utf-8")
        print(f"[OK] {name}: {len(content.strip())} 字符")
    return 0


# ── 单任务合并 ───────────────────────────────────────────────
def _resolve_inputs(cfg: dict, explicit: list[str] | None) -> list[Path]:
    names = list(explicit) if explicit else list(cfg["inputs"])
    paths = [HERE / n for n in names]
    if explicit is None:
        for pat in cfg.get("optional_globs", []):
            paths.extend(sorted(HERE.glob(pat)))
    return paths


def run_task(task: str, explicit_inputs: list[str] | None, out: Path | None,
             threshold: float, seed: int) -> int:
    cfg = TASKS[task]
    sys_path = HERE / cfg["system"]
    if not sys_path.exists() and cfg.get("system_legacy"):
        sys_path = HERE / cfg["system_legacy"]
    if not sys_path.exists():
        print(f"[ERROR] system 文件不存在：{HERE / cfg['system']}，"
              f"请先在容器内跑 --emit-systems")
        return 1
    system_prompt = sys_path.read_text(encoding="utf-8").strip()
    print(f"[{task}] system={sys_path.name}（{len(system_prompt)} 字符）")

    all_samples: list[dict] = []
    for p in _resolve_inputs(cfg, explicit_inputs):
        if not p.exists():
            print(f"  [WARN] 输入不存在，跳过: {p.name}")
            continue
        rows = load_jsonl(p)
        print(f"  加载 {p.name}: {len(rows)} 条")
        all_samples.extend(rows)
    if not all_samples:
        print(f"[ERROR] {task} 无有效样本")
        return 1

    print(f"  去重前合计 {len(all_samples)} 条（阈值 J≥{threshold}）")
    kept, n_dup = dedupe(all_samples, threshold)

    random.Random(seed).shuffle(kept)
    for s in kept:
        s["system"] = system_prompt   # 强制覆盖，杜绝来源自带旧 system
    out_path = out or (HERE / cfg["out"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for s in kept:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"[完成] {task}: {len(all_samples)} → {len(kept)} 条（近重复去 {n_dup}）"
          f" -> {out_path.name}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="三任务 SFT 数据合并：注入生产 system + MinHash 去重")
    p.add_argument("legacy_inputs", nargs="*",
                   help="兼容旧用法：输入 JSONL（等价 --task dialog --inputs ...）")
    p.add_argument("--task", choices=list(TASKS) + ["all"])
    p.add_argument("--inputs", nargs="+", help="覆盖默认输入文件列表")
    p.add_argument("-o", "--out", type=Path, help="覆盖默认输出路径")
    p.add_argument("--threshold", type=float, default=0.8, help="近重复 Jaccard 阈值")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--emit-systems", action="store_true",
                   help="从生产代码导出三套 system_*.txt（容器内运行）")
    args = p.parse_args()

    if args.emit_systems:
        return emit_systems()

    if args.legacy_inputs:
        if args.task and args.task != "dialog":
            p.error("位置参数输入仅用于 dialog；其他任务请用 --task + --inputs")
        return run_task("dialog", args.legacy_inputs, args.out,
                        args.threshold, args.seed)

    tasks = list(TASKS) if args.task in (None, "all") else [args.task]
    if args.inputs and len(tasks) != 1:
        p.error("--inputs 只能与单个 --task 一起使用")
    rc = 0
    for t in tasks:
        rc = run_task(t, args.inputs if args.inputs else None,
                      args.out if len(tasks) == 1 else None,
                      args.threshold, args.seed) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
