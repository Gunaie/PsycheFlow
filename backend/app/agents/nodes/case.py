"""病例科普解读节点：危机前置扫描 + RAG 术语检索 + LLM 结构化解读。

安全原则（与对话链路一致）：
1. detect_crisis_with_words 在任何 LLM 调用前执行，命中即走 escalation
   硬编码话术（零 LLM）+ 危机审计落盘，不解读病例内容。
2. LLM 仅做科普级术语解释，prompt 强约束不诊断/不指导用药（CASE_SYSTEM）。
3. LLM 失败/空回复 → 硬编码兜底话术（含 12355）。

一期输入：文字版 PDF 抽取文本 / 手动粘贴文本（图片 OCR 留二期）。
"""
import logging

from app.agents.nodes.escalation import escalation_node
from app.agents.prompts import CASE_SYSTEM, CASE_USER_TEMPLATE
from app.agents.state import AgentState
from app.core.llm import provider
from app.core.safety import detect_crisis_with_words
from app.rag.service import rag_service

logger = logging.getLogger("psycheflow.agents.case")

# LLM 失败/空回复时的硬编码兜底话术（含 12355，安全底线）
CASE_FALLBACK_REPLY = (
    "我暂时无法完成这份文书的解读（服务可能繁忙）。"
    "文书上的诊断和治疗建议请以主治医生的当面解释为准；"
    "如果你感到难受或有自伤的念头，请立刻告诉信任的老师、家长，"
    "或拨打青少年心理援助热线 12355。你也可以稍后再试一次。"
)


async def analyze_case(
    case_text: str,
    *,
    session_id: str = "",
    account_id: str = "",
) -> dict:
    """病例解读主入口。

    返回 dict：{reply, sources, crisis, agent}
    - crisis=True 时 reply 为硬编码危机话术（零 LLM），sources=[]
    - 正常时 reply 为 5 段结构化科普解读
    """
    # 1. 前置硬编码危机扫描（零 LLM，红线不受任何因素影响）
    is_crisis, detected_words = detect_crisis_with_words(case_text)
    if is_crisis:
        logger.info("case: crisis hit words=%s, skip LLM", detected_words)
        state: AgentState = {
            "session_id": session_id,
            "account_id": account_id,
            "user_message": case_text,
            "detected_words": detected_words,
            "is_crisis": True,
            "agent_trace": ["case_upload", "triage", "escalation"],
        }
        esc_out = await escalation_node(state)
        return {
            "reply": esc_out["final_reply"],
            "sources": [],
            "crisis": True,
            "agent": "escalation",
        }

    # 2. RAG 术语检索（用病例开头做 query，失败不阻断）
    formatted_sources: list[dict] = []
    rag_context = "（无相关片段）"
    try:
        query = case_text[:500]
        rag_sources = await rag_service.search(query, top_k=3)
        if rag_sources:
            parts = [
                f"[{i}] 《{s.get('source') or '未知来源'}》:\n{(s.get('text') or '')[:200]}"
                for i, s in enumerate(rag_sources[:3], 1)
            ]
            rag_context = "\n\n".join(parts)
            formatted_sources = [
                {
                    "text": s.get("text", ""),
                    "source": s.get("source", ""),
                    "chunk_id": s.get("chunk_id", 0),
                }
                for s in rag_sources
            ]
            logger.info("case: rag retrieved %d chunks", len(rag_sources))
    except Exception as e:
        logger.warning("case: rag search failed: %s", e)

    # 3. LLM 结构化解读（role=report：cloud 用 deepseek-v4-flash 性价比高，
    #    local 模式自动走 Ollama；温度 0.2 准确优先）
    user_prompt = CASE_USER_TEMPLATE.format(case_text=case_text)
    # 知识库片段作为可选背景附在 user prompt 末尾（有则增强，无则不提）
    if formatted_sources:
        user_prompt += (
            "\n\n以下是心理科普知识库中可能相关的公开资料片段，"
            "仅供你解释术语时参考，不要直接复述：\n" + rag_context
        )

    try:
        reply = await provider.chat(
            role="report",
            messages=[
                {"role": "system", "content": CASE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        if not reply or not reply.strip():
            logger.warning("case: LLM returned empty reply, triggering fallback")
            reply = CASE_FALLBACK_REPLY
        else:
            logger.info("case: reply len=%d", len(reply))
    except Exception as e:
        logger.warning("case: LLM failed: %s, using fallback", e)
        reply = CASE_FALLBACK_REPLY

    return {
        "reply": reply,
        "sources": formatted_sources,
        "crisis": False,
        "agent": "case",
    }
