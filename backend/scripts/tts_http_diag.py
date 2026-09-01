# -*- coding: utf-8 -*-
"""TTS 原生 HTTP API 诊断：验证 dashscope 域名下 /api/v1/services/audio/tts/SpeechSynthesizer。"""
import base64
import os
import sys

sys.path.insert(0, "/app")

import httpx

api_key = os.environ.get("DASHSCOPE_API_KEY", "")
url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

# voice 位置两种可能：input 内（CSDN curl 示例）/ 顶层（文档表格）
bodies = {
    "voice-in-input": {
        "model": "qwen-audio-3.0-tts-flash",
        "input": {
            "text": "你好，我是心流助手，今天感觉怎么样？",
            "voice": "longanhuan_v3.6",
            "format": "mp3",
        },
    },
    "voice-top-level": {
        "model": "qwen-audio-3.0-tts-flash",
        "input": {"text": "你好，我是心流助手。"},
        "voice": "longanhuan_v3.6",
        "format": "mp3",
    },
}

for name, body in bodies.items():
    print("====", name, "====")
    with httpx.Client(timeout=60.0) as cli:
        resp = cli.post(url, json=body, headers=headers)
    print("HTTP", resp.status_code, "| Content-Type:", resp.headers.get("content-type"))
    ct = (resp.headers.get("content-type") or "").lower()
    if resp.status_code == 200 and "json" not in ct:
        audio = resp.content
        print("binary audio bytes:", len(audio), "head:", audio[:4])
        with open(f"/tmp/tts_http_{name}.mp3", "wb") as f:
            f.write(audio)
        print(f"saved /tmp/tts_http_{name}.mp3")
    else:
        data = resp.json()
        out = (data.get("output") or {})
        audio_url = ((out.get("audio") or {}).get("url")) or ""
        audio_b64 = ((out.get("audio") or {}).get("data")) or ""
        if audio_url:
            with httpx.Client(timeout=60.0) as cli:
                dl = cli.get(audio_url)
            print("download HTTP", dl.status_code, "bytes:", len(dl.content), "head:", dl.content[:4])
            with open(f"/tmp/tts_http_{name}.mp3", "wb") as f:
                f.write(dl.content)
            print(f"saved /tmp/tts_http_{name}.mp3")
        elif audio_b64:
            raw = base64.b64decode(audio_b64)
            print("decoded audio bytes:", len(raw), "head:", raw[:4])
            with open(f"/tmp/tts_http_{name}.mp3", "wb") as f:
                f.write(raw)
            print(f"saved /tmp/tts_http_{name}.mp3")
