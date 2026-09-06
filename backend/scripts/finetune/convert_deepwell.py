#!/usr/bin/env python3
"""把 DeepWell-Adol 的 Human.json / Computer.json 转成 LLaMA-Factory sharegpt JSONL。

DeepWell-Adol（清华深研院 EMNLP 2025，青少年积极心理对话）原始格式：
  [ {"conversations": [{"from": "human"|"gpt", "value": "..."}, ...]}, ... ]
这正是 LLaMA-Factory 的 sharegpt 格式，本脚本做清洗 + 专家/扩增配比 + 逐行写出。

在云 GPU 上运行（也可本地，只需 python3，无第三方依赖）：
  python3 convert_deepwell.py <DeepWell仓库目录> <输出.jsonl>
  python3 convert_deepwell.py ./DeepWell-Adolescent ./deepwell_dialog.jsonl
"""
import json
import random
import sys
from pathlib import Path

MIN_TURNS = 2      # 至少一轮 human + gpt
MAX_SAMPLES = 2000  # dialog 训练样本上限（4090 上 3 epoch 约 1-2 小时）


def load_conversations(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for item in data:
        convs = item.get("conversations") or []
        cleaned = []
        for c in convs:
            role = (c.get("from") or "").strip()
            val = (c.get("value") or "").strip()
            if not val or role not in ("human", "gpt"):
                continue
            cleaned.append({"from": role, "value": val})
        # 至少一对、human 开头、角色严格交替
        if len(cleaned) < MIN_TURNS or cleaned[0]["from"] != "human":
            continue
        if any(cleaned[i]["from"] == cleaned[i - 1]["from"] for i in range(1, len(cleaned))):
            continue
        out.append({"conversations": cleaned})
    return out


def main() -> None:
    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("deepwell_dialog.jsonl")

    human = load_conversations(src_dir / "Human.json")
    try:
        computer = load_conversations(src_dir / "Computer.json")
    except FileNotFoundError:
        print(f"[WARN] 未找到 {src_dir / 'Computer.json'}，仅用专家种子数据")
        computer = []

    random.seed(42)
    random.shuffle(computer)
    need = max(0, MAX_SAMPLES - len(human))
    picked_computer = computer[:need]
    samples = human + picked_computer
    random.shuffle(samples)

    with open(out_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"专家种子 {len(human)} 条 + 机器扩增 {len(picked_computer)} 条 "
          f"= {len(samples)} 条 -> {out_path.resolve()}")


if __name__ == "__main__":
    main()
