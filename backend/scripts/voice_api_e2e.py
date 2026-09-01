# -*- coding: utf-8 -*-
"""D3 API 层真实 E2E：打运行中的 FastAPI（localhost:8000），TTS/ASR 全链路。"""
import sys

sys.path.insert(0, "/app")

import httpx

BASE = "http://localhost:8000"

text_in = "你好，今天心情怎么样？"
print("[1] POST /api/voice/synthesize:", text_in)
with httpx.Client(timeout=60.0) as cli:
    r1 = cli.post(f"{BASE}/api/voice/synthesize", json={"text": text_in})
    print("    HTTP", r1.status_code, "| content-type:", r1.headers.get("content-type"), "| bytes:", len(r1.content))
    assert r1.status_code == 200 and r1.headers["content-type"] == "audio/mpeg" and len(r1.content) > 1000
    print("[2] POST /api/voice/transcribe (回环喂回 mp3)...")
    r2 = cli.post(
        f"{BASE}/api/voice/transcribe",
        files={"file": ("speech.mp3", r1.content, "audio/mpeg")},
    )
    print("    HTTP", r2.status_code, "|", r2.json())
    assert r2.status_code == 200
    text_out = r2.json()["text"]
    ok = ("你好" in text_out) or ("心情" in text_out)
    print("[3] 断言:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
