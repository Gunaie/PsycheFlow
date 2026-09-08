"""合并 deepwell_dialog.jsonl + synthetic_dialog.jsonl，并给每条样本注入 system prompt。

用法（在云 GPU 或本地，数据目录下运行）：
  python3 merge_datasets.py deepwell_dialog.jsonl synthetic_dialog.jsonl dialog_merged.jsonl

  - 第 1、2 参数为输入 JSONL（sharegpt 格式，conversations 字段）
  - 第 3 参数为输出 JSONL
  - system prompt 从同目录 system_prompt.txt 读取；可用 --system-path 覆盖

输出格式（每条样本带顶层 "system" 字段，dataset_info.json 中 columns.system 映射）：
  {"system":"...","conversations":[{"from":"human","value":"..."},{"from":"gpt","value":"..."}]}
"""
import argparse
import json
import random
import sys
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [WARN] 跳过非法 JSON 行: {e}")
                continue
            convs = obj.get("conversations")
            if not isinstance(convs, list) or len(convs) < 2:
                print(f"  [WARN] 跳过结构异常样本: conversations 字段缺失或不足 2 条")
                continue
            out.append(obj)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="合并对话数据集并注入 system prompt")
    parser.add_argument("inputs", nargs="+", help="输入 JSONL 文件路径（至少 1 个）")
    parser.add_argument("-o", "--out", required=True, type=Path, help="输出 JSONL 路径")
    parser.add_argument("--system-path", type=Path, default=None,
                        help="system prompt 文件路径（默认同目录 system_prompt.txt）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（用于 shuffle）")
    args = parser.parse_args()

    # ── 读取 system prompt ──
    sys_path = args.system_path or Path(__file__).parent / "system_prompt.txt"
    if not sys_path.exists():
        print(f"[ERROR] system prompt 文件不存在: {sys_path}")
        return 1
    system_prompt = sys_path.read_text(encoding="utf-8").strip()
    print(f"[INFO] system prompt 长度: {len(system_prompt)} 字符")

    # ── 加载并合并 ──
    all_samples: list[dict] = []
    for inp in args.inputs:
        p = Path(inp)
        if not p.exists():
            print(f"[WARN] 输入文件不存在，跳过: {p}")
            continue
        rows = load_jsonl(p)
        print(f"[INFO] 加载 {p.name}: {len(rows)} 条")
        all_samples.extend(rows)

    if not all_samples:
        print("[ERROR] 无有效样本可合并")
        return 1

    # ── shuffle + 注入 system 字段 ──
    random.seed(args.seed)
    random.shuffle(all_samples)
    for s in all_samples:
        s["system"] = system_prompt

    # ── 写出 ──
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"\n[完成] 合并后 {len(all_samples)} 条 -> {args.out.resolve()}")
    print(f"       每条带 system 字段（{len(system_prompt)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
