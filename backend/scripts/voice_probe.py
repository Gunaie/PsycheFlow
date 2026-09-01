# -*- coding: utf-8 -*-
"""探测 qwen-audio-3.0-asr-flash 的 DashScope 同步 multimodal-generation 正确形态。"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.core.voice import _dashscope_generate, transcribe  # noqa: E402

MP3 = open("/app/data/voice_probe.mp3", "rb").read()


async def probe():
    # 直接跑完整 transcribe（mp3 路径）
    try:
        text = await transcribe(MP3, mime_type="audio/mpeg")
        print(f"[transcribe mp3] OK -> {text!r}")
    except Exception as e:
        print(f"[transcribe mp3] FAIL -> {e}")


asyncio.run(probe())
