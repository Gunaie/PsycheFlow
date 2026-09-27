# -*- coding: utf-8 -*-
"""对话质量冒烟脚本：多轮模拟真实消息，检验干预回复骨架/防重复/防幻觉规则遵循度。

复用生产 prompt 拼装（build_intervention_messages 含 RAG 检索）+ 生产流式角色
（dialog_stream，本地模式挂 qwen2.5:dialog-lora），逐轮累积 history 模拟多轮。

场景：
- 独立咨询（空历史）：回归 2026-09-08「什么是焦虑」答非所问 + 空历史幻觉归因
  「最近你说过…」+ 闭合问句的失败案例；验证咨询骨架（先答问）与防幻觉规则
- 倾诉转咨询（多轮）：验证多轮下防复读、建议不重样，及咨询意图穿插

自动检查项：闭合问句（对吧/对吗/是不是/是吧/好吗）、空历史下的「你说过…」幻觉归因。

用法：docker exec -e PYTHONUTF8=1 psycheflow-backend uv run python scripts/dialog_smoke.py
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, "/app")

from app.agents.nodes.intervention import build_intervention_messages, check_reply_quality, RETRY_HINT  # noqa: E402
from app.agents.nodes.triage import detect_method_question  # noqa: E402
from app.agents.state import AgentState  # noqa: E402
from app.core.llm import provider  # noqa: E402

TEMPERATURE = float(os.environ.get("DIALOG_SMOKE_TEMP", "0.6"))

SCENARIOS = [
    {
        "name": "独立咨询（空历史）",
        "turns": [
            ("什么是焦虑", "咨询"),
        ],
    },
    {
        "name": "倾诉转咨询（多轮）",
        "turns": [
            ("我最近因为期末考试，压力很大", "倾诉"),
            ("我还很焦虑，总是睡不好", "倾诉"),
            ("晚上总担心考砸，越想越睡不着", "倾诉"),
            ("那我这种是焦虑吗，和普通紧张有什么区别", "咨询"),
        ],
    },
]

# 违规检查：闭合问句 / 空历史下的幻觉归因
_BANNED_CLOSE_Q = re.compile(r"(对吧|对吗|是不是|是吧|好吗|对不对|好不好|会不会|有没有|能不能|可以吗|吗[？?]|吧[？?])")
# 「你提到」常合法引用当前消息内容（"你提到最近考试压力大"），不算幻觉；
# 「你说过/你之前说」才指向历史轮次，空历史下出现即编造
_FABRICATED_ATTR = re.compile(r"(你说过|你之前说|你上次说|你以前说)")


def check_reply(reply: str, history_len: int) -> list[str]:
    issues = []
    if _BANNED_CLOSE_Q.search(reply):
        issues.append("闭合问句")
    if not history_len and _FABRICATED_ATTR.search(reply):
        issues.append("空历史幻觉归因（「你说过…」）")
    return issues


async def run_scenario(scenario: dict) -> None:
    print(f"\n===== 场景：{scenario['name']} =====")
    state: AgentState = {
        "persona_id": "default",
        "history": [],
        "has_assessment": False,
        "assessment_context": {},
    }
    for msg, intent in scenario["turns"]:
        state["user_message"] = msg
        state["triage_intent"] = intent
        messages, _, _, decision = await build_intervention_messages(state)
        tokens: list[str] = []
        async for token in provider.stream(
            role="dialog_stream", messages=messages, temperature=TEMPERATURE, max_tokens=3000
        ):
            tokens.append(token)
        reply = "".join(tokens).strip()
        # 质检重试（与生产 intervention_node 同口径：最多重试 1 次，
        # 重试回复须重新质检，不合格则保留首次回复）
        history = [
            {"role": h["role"], "content": h["content"]}
            for h in (state.get("history") or [])
            if h.get("role") in ("user", "assistant")
        ][-20:]
        min_methods = 2 if detect_method_question(msg) else 1
        first_reply = reply
        if not check_reply_quality(reply, history, min_method_categories=min_methods, min_len=50):
            retry_tokens: list[str] = []
            async for token in provider.stream(
                role="dialog_stream",
                messages=[*messages, {"role": "system", "content": RETRY_HINT}],
                temperature=0.35, max_tokens=3000,
            ):
                retry_tokens.append(token)
            retry_reply = "".join(retry_tokens).strip()
            if retry_reply and check_reply_quality(
                retry_reply, history, min_method_categories=min_methods, min_len=50
            ):
                reply = retry_reply
            else:
                reply = first_reply
        rag = decision.get("rag", {})
        rag_desc = "skipped" if rag.get("skipped") else rag.get("count")
        print(f"用户({intent}): {msg}")
        print(f"暖暖: {reply}")
        issues = check_reply(reply, len(state.get("history") or []))
        flag = f" | ⚠ {'；'.join(issues)}" if issues else ""
        print(f"[RAG {rag_desc}{flag}] {'-' * 40}")
        state["history"] = (state.get("history") or []) + [
            {"role": "user", "content": msg},
            {"role": "assistant", "content": reply},
        ]


async def main() -> None:
    for scenario in SCENARIOS:
        await run_scenario(scenario)


asyncio.run(main())
