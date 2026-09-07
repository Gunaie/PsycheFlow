# -*- coding: utf-8 -*-
"""对话质量冒烟脚本：多轮模拟真实消息，检验干预回复骨架/防重复规则遵循度。

复用生产 prompt 拼装（build_intervention_messages 含 RAG 检索）+ 生产流式角色
（dialog_stream，本地模式挂 qwen2.5:dialog-lora），逐轮累积 history 模拟多轮。

用法：docker exec -e PYTHONUTF8=1 psycheflow-backend uv run python scripts/dialog_smoke.py
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app")

from app.agents.nodes.intervention import build_intervention_messages  # noqa: E402
from app.agents.state import AgentState  # noqa: E402
from app.core.llm import provider  # noqa: E402

MESSAGES = [
    "我最近因为期末考试，压力很大",
    "我还很焦虑，总是睡不好",
    "晚上总担心考砸，越想越睡不着",
]

TEMPERATURE = float(os.environ.get("DIALOG_SMOKE_TEMP", "0.6"))


async def main() -> None:
    state: AgentState = {
        "persona_id": "default",
        "history": [],
        "has_assessment": False,
        "assessment_context": {},
    }
    for msg in MESSAGES:
        state["user_message"] = msg
        state["triage_intent"] = "倾诉"
        messages, _, _, decision = await build_intervention_messages(state)
        tokens: list[str] = []
        async for token in provider.stream(
            role="dialog_stream", messages=messages, temperature=TEMPERATURE, max_tokens=3000
        ):
            tokens.append(token)
        reply = "".join(tokens).strip()
        rag = decision.get("rag", {})
        rag_desc = "skipped" if rag.get("skipped") else rag.get("count")
        print(f"用户: {msg}")
        print(f"暖暖: {reply}")
        print(f"[RAG {rag_desc}] {'-' * 40}")
        state["history"] = (state.get("history") or []) + [
            {"role": "user", "content": msg},
            {"role": "assistant", "content": reply},
        ]


asyncio.run(main())
