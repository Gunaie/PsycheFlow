"""Intervention 干预节点：RAG 检索 + LLM 共情回应。

核心对话智能体，温度 0.35（共情自然）。引用 RAG 知识库片段时回复末尾用
「来源：《xxx》」格式，sources 字段同时返回供前端渲染卡片。
"""
import logging

from app.agents.personas import build_system_prompt, get_persona
from app.agents.prompts import INTERVENTION_USER_TEMPLATE
from app.agents.state import AgentState
from app.core.llm import provider
from app.rag.service import rag_service

logger = logging.getLogger("psycheflow.agents.intervention")


async def intervention_node(state: AgentState) -> dict:
    """干预节点：RAG 检索 + LLM 共情回应。

    流程：
    1. rag_service.search(user_message, top_k=3) → 检索知识库片段
    2. 拼接 rag_context 注入 LLM prompt
    3. provider.chat(role="dialog", temp=0.35) 生成共情回应
       （system prompt = 安全底线 + persona_id 对应人格特色，未知人格回退 default）
    4. sources 字段返回供前端渲染
    """
    message = state.get("user_message", "")
    trace = state.get("agent_trace", []) + ["intervention"]

    # 0. 解析人格（未知/为空回退 default）
    persona = get_persona(state.get("persona_id"))
    system_prompt = build_system_prompt(persona)
    logger.info("intervention: persona=%s", persona.persona_id)

    # 1. RAG 检索
    rag_sources: list = []
    try:
        rag_sources = await rag_service.search(message, top_k=3)
        logger.info("intervention: rag retrieved %d chunks", len(rag_sources))
    except Exception as e:
        logger.warning("intervention: rag search failed: %s", str(e))

    # 2. 拼接 rag_context（最多 3 段，每段 200 字截断）
    rag_parts = []
    for i, src in enumerate(rag_sources[:3], 1):
        text = (src.get("text") or "")[:200]
        source = src.get("source") or "未知来源"
        rag_parts.append(f"[{i}] 《{source}》:\n{text}")
    rag_context = "\n\n".join(rag_parts) if rag_parts else "（无相关片段）"

    # 3. LLM 共情回应（history 拼在 system 后保持对话上下文，向后兼容旧 chat 行为）
    try:
        user_prompt = INTERVENTION_USER_TEMPLATE.format(
            triage_intent=state.get("triage_intent", "倾诉"),
            has_assessment=state.get("has_assessment", False),
            assessment_context=state.get("assessment_context", {}),
            message=message,
            rag_context=rag_context,
        )
        history = [
            {"role": h["role"], "content": h["content"]}
            for h in (state.get("history") or [])
            if h.get("role") in ("user", "assistant")
        ]
        reply = await provider.chat(
            role="dialog",
            messages=[
                {"role": "system", "content": system_prompt},
                *history,
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.35,
            max_tokens=600,
        )
        # 空字符串/纯空白不抛异常，须显式检查触发 fallback（同 reports 教训）
        if not reply or not reply.strip():
            logger.warning("intervention: LLM returned empty reply, triggering fallback")
            raise ValueError("empty reply from LLM")
        logger.info("intervention: reply len=%d", len(reply))
    except Exception as e:
        logger.warning("intervention: LLM failed: %s", str(e))
        reply = (
            "我听到你的分享，谢谢你的信任。"
            "作为校园心理陪伴助手，我现在的回复能力受限，"
            "请把你正在承受的告诉信任的老师或家长，"
            "或拨打青少年心理援助热线 12355 寻求专业陪伴。"
        )

    # 4. sources 格式化为前端兼容字段（text + source + chunk_id）
    formatted_sources = [
        {
            "text": s.get("text", ""),
            "source": s.get("source", ""),
            "chunk_id": s.get("chunk_id", 0),
        }
        for s in rag_sources
    ]

    return {
        "final_reply": reply,
        "sources": formatted_sources,
        "rag_sources": rag_sources,
        "current_agent": "intervention",
        "agent_trace": trace,
    }
