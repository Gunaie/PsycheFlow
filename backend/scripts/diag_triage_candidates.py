# -*- coding: utf-8 -*-
"""探测 qwen-plus 备选模型的思考链与首 content token 时间。

目标：在 qwen-plus 无额度时，从 qwen3.8-max / qwen3.7-max-2026-06-08 / qwen3.8-27b
中选出「无思考链」或「可关思考链且关后首 token 快」的模型，用于 triage + dialog_stream 角色。

判定标准（满足其一即可用于 SSE 流式）：
  A. 无 reasoning_content 字段（天然无思考链）
  B. enable_thinking=False 被百炼接受且关后首 content < 2s
"""
import asyncio
import sys
import time

sys.path.insert(0, "/app")

from openai import AsyncOpenAI  # noqa: E402

from app.core.config import settings  # noqa: E402

CANDIDATES = [
    "qwen3.8-max",
    "qwen3.7-max-2026-06-08",
    "qwen3.8-27b",
]

# qwen-plus 作基线参照（若仍有额度则跑，无额度则跳过）
BASELINE = "qwen-plus"


async def probe(model: str, enable_thinking: bool):
    """返回单次探测的结果 dict；异常时返回错误信息。"""
    client = AsyncOpenAI(
        base_url=settings.dashscope_base_url, api_key=settings.dashscope_api_key
    )
    t0 = time.time()
    t_first_content = None
    t_first_reasoning = None
    content_chunks = 0
    reasoning_chunks = 0
    full_content = ""

    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": "你是一个校园心理陪伴助手，面向中小学生。"},
            {"role": "user", "content": "最近考试压力大，睡不好，给我一句安慰"},
        ],
        temperature=0.35,
        max_tokens=500,
        stream=True,
    )
    if not enable_thinking:
        kwargs["extra_body"] = {"enable_thinking": False}

    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        rc = getattr(delta, "reasoning_content", None)
        c = getattr(delta, "content", None)
        if rc and t_first_reasoning is None:
            t_first_reasoning = time.time() - t0
        if c and t_first_content is None:
            t_first_content = time.time() - t0
        if rc:
            reasoning_chunks += 1
        if c:
            content_chunks += 1
            full_content += c

    return {
        "model": model,
        "enable_thinking": enable_thinking,
        "t_first_content": t_first_content,
        "t_first_reasoning": t_first_reasoning,
        "content_chunks": content_chunks,
        "reasoning_chunks": reasoning_chunks,
        "full_content": full_content[:80],
    }


def fmt(r):
    if "error" in r:
        return f"  {r['model']} (think={r['enable_thinking']}): 异常 {r['error']}"
    fc = f"{r['t_first_content']:.2f}s" if r["t_first_content"] else "无"
    fr = f"{r['t_first_reasoning']:.2f}s" if r["t_first_reasoning"] else "无"
    return (
        f"  {r['model']} (think={r['enable_thinking']}): "
        f"首content={fc} | 首reasoning={fr} | "
        f"content_chunks={r['content_chunks']} reasoning_chunks={r['reasoning_chunks']}"
    )


def judge(results):
    """给出推荐结论。"""
    print("\n=== 判定 ===")
    usable = []
    for m in CANDIDATES:
        on = next((r for r in results if r["model"] == m and r["enable_thinking"]), None)
        off = next(
            (r for r in results if r["model"] == m and not r["enable_thinking"]), None
        )
        # A: 天然无思考链（开思考时也无 reasoning_chunks）
        if on and on.get("reasoning_chunks", 0) == 0 and on.get("t_first_content"):
            print(f"  {m}: ✅ 天然无思考链，首content {on['t_first_content']:.2f}s（可直接用于SSE）")
            usable.append((m, "天然无思考链"))
            continue
        # B: 关思考被接受且关后快
        if off and "error" not in off:
            if off.get("reasoning_chunks", 0) == 0 and off.get("t_first_content"):
                ft = off["t_first_content"]
                flag = "✅ 可关思考且关后快" if ft < 2 else "⚠️ 可关思考但关后仍慢"
                print(f"  {m}: {flag}，关后首content {ft:.2f}s")
                if ft < 2:
                    usable.append((m, "可关思考链"))
                continue
        # 关思考被拒（400）且开思考有思考链 → 不可用于SSE
        if off and "error" in off and on and on.get("reasoning_chunks", 0) > 0:
            print(f"  {m}: ❌ 思考链强制开启（关不了），开思考时首content "
                  f"{(on.get('t_first_content') or 0):.2f}s → 不可用于SSE流式")
            continue
        print(f"  {m}: ⚠️ 情况不明，需人工看上面数据")
    if usable:
        print(f"\n推荐用于 triage+dialog_stream: {usable[0][0]}（{usable[0][1]}）")
    else:
        print("\n⚠️ 三个候选均不适合 SSE 流式，建议：1) 申请 qwen-plus 额度；"
              "2) 或改架构让 triage 非流式阻塞等待（放弃 NFR-5）")


async def main():
    results = []
    # 基线
    print("=== 基线参照 ===")
    try:
        r = await probe(BASELINE, True)
        print(fmt(r))
        results.append(r)
    except Exception as e:
        print(f"  {BASELINE}: 异常（可能无额度）{type(e).__name__}: {str(e)[:120]}")

    print("\n=== 候选模型（开思考）===")
    for m in CANDIDATES:
        try:
            r = await probe(m, True)
            print(fmt(r))
            results.append(r)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:120]}"
            print(f"  {m} (think=True): 异常 {err}")
            results.append({"model": m, "enable_thinking": True, "error": err})

    print("\n=== 候选模型（关思考 enable_thinking=False）===")
    for m in CANDIDATES:
        try:
            r = await probe(m, False)
            print(fmt(r))
            results.append(r)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:120]}"
            print(f"  {m} (think=False): 异常 {err}")
            results.append({"model": m, "enable_thinking": False, "error": err})

    judge(results)


if __name__ == "__main__":
    asyncio.run(main())
