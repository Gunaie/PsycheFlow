# -*- coding: utf-8 -*-
"""验证 deepseek 增大 max_tokens 后是否正常输出 content。"""
import asyncio
import sys

sys.path.insert(0, "/app")

from openai import AsyncOpenAI
from app.core.config import settings

async def main():
    client = AsyncOpenAI(
        base_url=settings.dashscope_base_url,
        api_key=settings.dashscope_api_key,
    )
    model = "deepseek-v4-pro-0813"
    for max_tok in [50, 200, 1000, 2000]:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个心理助手。"},
                {"role": "user", "content": "你好，请回复一句话。"},
            ],
            temperature=0.35,
            max_tokens=max_tok,
        )
        content = resp.choices[0].message.content or ""
        rc = resp.choices[0].message.reasoning_content or ""
        print(f"max_tokens={max_tok:5d} | finish={resp.choices[0].finish_reason:8s} | content_len={len(content):4d} | reasoning_len={len(rc):4d} | content={repr(content[:80])}")

asyncio.run(main())
