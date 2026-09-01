# -*- coding: utf-8 -*-
"""D3 真实 E2E：TTS -> ASR 回环验证（在 backend 容器内跑）。

1. synthesize("你好，今天心情怎么样？") -> mp3 bytes
2. 把 mp3 喂给 transcribe -> 转写文本
3. 断言转写文本包含关键词
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.core.voice import synthesize, transcribe  # noqa: E402


async def main():
    text_in = "你好，今天心情怎么样？"
    print("[1] TTS 合成:", text_in)
    mp3 = await synthesize(text_in)
    print("    mp3 bytes:", len(mp3))

    print("[2] ASR 转写回环...")
    text_out = await transcribe(mp3, mime_type="audio/mpeg")
    print("    转写结果:", text_out)

    ok = ("你好" in text_out) or ("心情" in text_out)
    print("[3] 回环断言:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


asyncio.run(main())
