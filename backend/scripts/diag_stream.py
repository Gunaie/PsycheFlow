# -*- coding: utf-8 -*-
"""诊断 qwen3.8 stream 模式的 chunk 结构与首 token 时间。

验证：qwen3.8-2.4t-a95b 流式是否也有 reasoning_content 思考链，
      首 content token 到达时间（判断首 token 瓶颈）。
"""
import asyncio
import sys
import time

sys.path.insert(0, "/app")

from openai import AsyncOpenAI  # noqa: E402

from app.core.config import settings  # noqa: E402


async def probe(model: str, enable_thinking: bool = True, label: str = ""):
    """探测 chunk 结构。enable_thinking=False 时通过 extra_body 传给百炼。

    label 用于区分同一 model 的两次调用打印。
    """
    client = AsyncOpenAI(base_url=settings.dashscope_base_url, api_key=settings.dashscope_api_key)
    t0 = time.time()
    t_first_content = None
    t_first_reasoning = None
    content_chunks = 0
    reasoning_chunks = 0
    full_content = ""
    extra_fields_seen = set()

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

    tag = f"[{label}] " if label else ""
    print(f"\n=== {model} (enable_thinking={enable_thinking}) {tag}===")
    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        # 列出 delta 上所有非空字段，便于发现新字段
        for field in ("content", "reasoning_content", "role", "tool_calls"):
            v = getattr(delta, field, None)
            if v is not None and field not in extra_fields_seen:
                extra_fields_seen.add(field)
                print(f"  [{time.time()-t0:6.2f}s] 首见字段 {field}: {repr(str(v)[:60])}")
        rc = getattr(delta, "reasoning_content", None)
        c = getattr(delta, "content", None)
        if rc and t_first_reasoning is None:
            t_first_reasoning = time.time() - t0
            print(f"  [{time.time()-t0:6.2f}s] 首 reasoning: {repr(rc[:40])}")
        if c and t_first_content is None:
            t_first_content = time.time() - t0
            print(f"  [{time.time()-t0:6.2f}s] 首 content : {repr(c[:40])}")
        if rc:
            reasoning_chunks += 1
        if c:
            content_chunks += 1
            full_content += c

    print(f"  content chunks: {content_chunks}, reasoning chunks: {reasoning_chunks}")
    print(f"  首 content : {t_first_content:.2f}s" if t_first_content else "  无 content")
    print(f"  首 reasoning: {t_first_reasoning:.2f}s" if t_first_reasoning else "  无 reasoning")
    print(f"  delta 字段: {sorted(extra_fields_seen)}")
    print(f"  完整 content: {full_content[:120]}")


async def main():
    # qwen3.8: 开启思考（关闭会 400）
    try:
        await probe("qwen3.8-2.4t-a95b", enable_thinking=True, label="qwen3.8")
    except Exception as e:
        print(f"  异常: {type(e).__name__}: {e}")
    # deepseek 作参照
    try:
        await probe("deepseek-v4-pro-0813", enable_thinking=True, label="ds-pro")
    except Exception as e:
        print(f"  异常: {type(e).__name__}: {e}")
    # 候选无思考链模型：qwen-plus / qwen-turbo
    for m in ("qwen-plus", "qwen-turbo"):
        try:
            await probe(m, enable_thinking=True, label=m)
        except Exception as e:
            print(f"  异常[{m}]: {type(e).__name__}: {e}")


asyncio.run(main())
