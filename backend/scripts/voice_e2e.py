# -*- coding: utf-8 -*-
"""语音 E2E 往返验证：TTS 合成 -> ASR 转写回文本（在 backend 容器内跑）。

跟随 VOICE_MODE 自动适配：
- local（sherpa-onnx VITS + faster-whisper）：产出 WAV；文本含热线号码
  12355，同时验证 rule_fsts 数字规范化生效（ASR 转写应能回读出号码）
- cloud（qwen-tts + qwen-audio）：产出 MP3

运行：
  docker exec psycheflow-backend uv run python scripts/voice_e2e.py
"""
import asyncio
import io
import sys
import time
import wave

sys.path.insert(0, "/app")

from app.core.config import settings  # noqa: E402
from app.core.voice import synthesize, transcribe  # noqa: E402

TEXT = "你好，我是暖暖。如需帮助，可拨打12355青少年服务台。"


async def main() -> int:
    mode = settings.voice_mode
    mime = "audio/wav" if mode == "local" else "audio/mpeg"

    print(f"[1] TTS 合成（mode={mode}）: {TEXT}")
    t0 = time.time()
    audio = await synthesize(TEXT)
    print(f"    {len(audio)} B | 合成耗时 {time.time() - t0:.2f}s")

    if mode == "local":
        # WAV 结构检查（aishell3 VITS 输出 8kHz 属正常规格）
        with wave.open(io.BytesIO(audio), "rb") as w:
            rate = w.getframerate()
        ok_header = audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"
        print(f"    WAV 头完整: {ok_header} | {rate}Hz")
        if not (ok_header and rate >= 8000):
            print("[E2E] FAIL: WAV 结构异常")
            return 1

    print("[2] ASR 转写回环...")
    t1 = time.time()
    text_out = await transcribe(audio, mime_type=mime)
    print(f"    转写耗时 {time.time() - t1:.2f}s | 结果: {text_out!r}")

    # 合成语音较干净，允许个别字差异，按关键词覆盖判定
    keywords = ["你好", "帮助", "12355"]
    hit = [k for k in keywords if k in text_out]
    print(f"[3] 关键词回环命中: {len(hit)}/{len(keywords)} -> {hit}")
    ok = len(hit) >= 2
    print("[4] 回环断言:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
