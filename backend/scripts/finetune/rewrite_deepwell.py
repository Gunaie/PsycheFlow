"""改写 DeepWell-Adol 中被清洗剔除的模板腔样本为合规版本。

背景（2026-09-08 反模板重训 - 第 3 步）：
  convert_deepwell.py 清洗时把命中 TEMPLATE_PATTERNS 的 gpt 回复整条剔除，
  丢失了这部分样本的 human 多样性。本脚本把这些被剔除的样本捞回来，
  保留原 human 不变，让云端 LLM 改写 gpt 为合规版本，扩 200-300 条样本。

设计：
  - 输入：DeepWell 原始数据目录（含 Human.json / Computer.json）
  - 识别被剔除样本：gpt 回复命中 TEMPLATE_PATTERNS 或 CLOSED_QUESTION_PATTERNS
  - 调云端 LLM 改写 gpt（system prompt 同 gen_samples_cloud.py 风格约束）
  - 每条原样本产出 N 条差异化改写（默认 2 条，扩大数据规模）
  - 输出 sharegpt JSONL，可直接被 merge_datasets.py 合并训练

用法（云 GPU 上，数据在 /root/autodl-tmp/ft/DeepWell-Adolescent）：
  $env:DASHSCOPE_API_KEY="sk-xxx"   # 或 export DASHSCOPE_API_KEY=sk-xxx
  python3 rewrite_deepwell.py /root/autodl-tmp/ft/DeepWell-Adolescent -o deepwell_rewritten.jsonl

  # 可选参数：
  #   --per-sample 2     每条原样本产出多少改写（默认 2）
  #   --concurrency 6    并发上限（默认 6）
  #   --model qwen3.8-max  云端模型名
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

from openai import AsyncOpenAI

# ── 配置 ─────────────────────────────────────────────────────
DEFAULT_OUT = Path(__file__).parent / "deepwell_rewritten.jsonl"
DEFAULT_MODEL = "qwen3.8-max"
DEFAULT_PER_SAMPLE = 2          # 每条原样本产出多少改写
DEFAULT_CONCURRENCY = 6
MAX_RETRIES = 2

# ── 清洗规则（与 convert_deepwell.py / gen_samples_cloud.py 同步）──
TEMPLATE_PATTERNS = [
    re.compile(r"这一定让你感到.{0,6}(疲惫|沮丧|难过|痛苦|焦虑)"),
    re.compile(r"我.{0,4}理解你的感受"),
    re.compile(r"我能感受到你.{0,6}(的心情|的感受|的痛苦)"),
    re.compile(r"这确实是一件.{0,8}(令人|让人).{0,4}(难过|痛苦|沮丧)的事"),
    re.compile(r"听起来你.{0,6}(最近|这段时间).{0,4}(过得|过得很).{0,4}(不容易|辛苦)"),
]

CLOSED_QUESTION_PATTERNS = [
    re.compile(r"(对吧|对吗|是不是|是吧|好吗)[？?]"),
    re.compile(r"(对吧|对吗|是不是|是吧|好吗)(。|$)"),
    re.compile(r"你觉得.{0,10}(对吧|对吗|是不是)\s*[？?]"),
    re.compile(r"是不是.{0,10}(让你|使你|令你).{0,4}(感到|觉得)"),
]


def is_template_reply(text: str) -> bool:
    return any(p.search(text) for p in TEMPLATE_PATTERNS)


def has_closed_question(text: str) -> bool:
    return any(p.search(text) for p in CLOSED_QUESTION_PATTERNS)


def is_noncompliant(text: str) -> bool:
    """是否命中任一清洗规则（需改写）。"""
    return is_template_reply(text) or has_closed_question(text)


# ── 改写 system prompt（与 gen_samples_cloud.py 风格约束一致）──
REWRITE_SYSTEM_PROMPT = """你是一名心理援助对话数据改写专家，专门把不合规的 AI 回复改写为合规版本。

【任务】
给定青少年表达 + 原 AI 回复（不合规），改写出 {per_sample} 个差异化的合规版本。

【不合规类型】
1. 模板腔：使用了"我理解你的感受""这一定让你感到...""我能感受到你...""这确实是一件让人难过的事""听起来你最近过得很辛苦"等套路开头
2. 封闭式问句：以"对吧""对吗""是不是""好吗"结尾

【改写原则】
1. 保留原 human 不变（已给定）
2. 简洁：50-100 字、不超过 3 句
3. 共情但不套话：开头先承认对方感受，**严禁**使用上述模板腔
4. 具体化：针对原 human 的具体细节，不空泛
5. 开放式追问结尾：以开放式问句结尾，**严禁**封闭问句
6. 自然口语：不堆排比、不机械"第一第二第三"
7. 危机敏感：涉及自伤/自杀/被侵害，引导寻求成年人或专业机构，可附热线（12355/120/110）

【输出格式】
严格输出 JSON，不要 markdown 代码块：
{{"rewrites":["改写1","改写2",...]}}

【参考样例】
原 human: 今天被老师当着全班面批评了，感觉特别丢人
原 gpt（不合规）: 我理解你的感受，被当众批评一定让你感到很难过，对吧？
改写1: 被当众批评确实会让人觉得很难堪，你当时的心情一定很复杂。你能说说是因为什么事情吗？这样我能更好地理解你的处境。
改写2: 当众被批评确实不好受，那种想找个地缝钻进去的感觉很真实。当时老师是因为什么事批评你的？聊聊能帮你理清思路。
"""


def build_user_prompt(human: str, gpt: str, per_sample: int) -> str:
    return (
        f"【原 human】{human}\n"
        f"【原 gpt（不合规）】{gpt}\n\n"
        f"请改写出 {per_sample} 个差异化的合规版本。直接输出 JSON。"
    )


def parse_json_response(raw: str) -> list[str]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return []
    cleaned = cleaned[first:last + 1]
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    rewrites = obj.get("rewrites") if isinstance(obj, dict) else None
    if not isinstance(rewrites, list):
        return []
    return [w.strip() for w in rewrites if isinstance(w, str) and w.strip()]


async def rewrite_one(
    client: AsyncOpenAI,
    model: str,
    human: str,
    gpt: str,
    per_sample: int,
    semaphore: asyncio.Semaphore,
) -> list[str]:
    system_prompt = REWRITE_SYSTEM_PROMPT.format(per_sample=per_sample)
    user_prompt = build_user_prompt(human, gpt, per_sample)
    async with semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.85,
                    top_p=0.9,
                    max_tokens=800,
                    extra_body={"enable_thinking": False},
                    timeout=60,
                )
                raw = resp.choices[0].message.content or ""
                rewrites = parse_json_response(raw)
                if rewrites:
                    return rewrites
            except Exception as e:
                print(f"  [ERROR] 改写失败（attempt {attempt}）: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 * attempt)
    return []


def load_raw_conversations(path: Path) -> list[dict]:
    """加载 DeepWell 原始 JSON，保留结构。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for item in data:
        convs = item.get("conversations") or []
        if len(convs) < 2:
            continue
        # 只取首对 human+gpt（多轮的取首对用于改写）
        h = convs[0].get("value", "").strip() if convs[0].get("from") == "human" else ""
        g = convs[1].get("value", "").strip() if convs[1].get("from") == "gpt" else ""
        if h and g:
            out.append({"human": h, "gpt": g})
    return out


def find_noncompliant(rows: list[dict]) -> list[dict]:
    """挑出 gpt 命中清洗规则的样本。"""
    return [r for r in rows if is_noncompliant(r["gpt"])]


async def main_async(args) -> int:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    if not api_key:
        print("[ERROR] 未设置 DASHSCOPE_API_KEY 环境变量")
        return 1

    src_dir = Path(args.src_dir)
    rows: list[dict] = []
    for fname in ("Human.json", "Computer.json"):
        p = src_dir / fname
        if p.exists():
            loaded = load_raw_conversations(p)
            print(f"[INFO] 加载 {p.name}: {len(loaded)} 条原始样本")
            rows.extend(loaded)
        else:
            print(f"[WARN] 未找到 {p}，跳过")

    if not rows:
        print("[ERROR] 未加载到任何原始样本，检查路径")
        return 1

    noncompliant = find_noncompliant(rows)
    print(f"[INFO] 原始 {len(rows)} 条 → 命中清洗规则 {len(noncompliant)} 条（待改写）")

    if not noncompliant:
        print("[INFO] 没有需要改写的样本，退出")
        return 0

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    semaphore = asyncio.Semaphore(args.concurrency)

    # gather 保序，便于把原 human 配对回改写结果
    tasks = [
        rewrite_one(client, args.model, r["human"], r["gpt"], args.per_sample, semaphore)
        for r in noncompliant
    ]
    rewrites_list = await asyncio.gather(*tasks)

    results: list[dict] = []
    raw_count = 0
    cleaned_count = 0
    done = 0
    total = len(noncompliant)
    for src, rewrites in zip(noncompliant, rewrites_list):
        done += 1
        for w in rewrites:
            raw_count += 1
            # 二次清洗：兜底，理论上云端 LLM 已合规
            if is_noncompliant(w):
                cleaned_count += 1
                continue
            results.append({"conversations": [
                {"from": "human", "value": src["human"]},
                {"from": "gpt", "value": w},
            ]})
        if done % 20 == 0 or done == total:
            print(f"  [进度] {done}/{total} 改写完成，累计有效 {len(results)} 条（剔除 {cleaned_count} 条）")

    print(f"\n[完成] 原始改写 {raw_count} 条 → 二次清洗后 {len(results)} 条（剔除 {cleaned_count} 条）")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in results:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"[写出] {len(results)} 条 -> {args.out.resolve()}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="改写 DeepWell 模板腔样本为合规版本")
    parser.add_argument("src_dir", type=Path, help="DeepWell 原始数据目录（含 Human.json/Computer.json）")
    parser.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help="输出 JSONL 路径")
    parser.add_argument("--per-sample", type=int, default=DEFAULT_PER_SAMPLE, help="每条原样本产出多少改写")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="并发上限")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="云端模型名")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
