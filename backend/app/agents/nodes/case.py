"""病例科普解读节点：危机前置扫描 + RAG 术语检索 + LLM 结构化解读 + 结构化摘要提取。

安全原则（与对话链路一致）：
1. detect_crisis_with_words 在任何 LLM 调用前执行，命中即走 escalation
   硬编码话术（零 LLM）+ 危机审计落盘，不解读病例内容。
2. LLM 仅做科普级术语解释，prompt 强约束不诊断/不指导用药（CASE_SYSTEM）。
3. LLM 失败/空回复 → 硬编码兜底话术（含 12355）。

一期输入：文字版 PDF 抽取文本 / 手动粘贴文本（图片 OCR 留二期）。
增强（2026-09-08）：结构化摘要提取（诊断/用药/复诊/关注）+ 多轮追问支持。
"""
import logging
import re

from app.agents.nodes.escalation import escalation_node
from app.agents.prompts import (
    CASE_FOLLOWUP_SYSTEM,
    CASE_FOLLOWUP_TEMPLATE,
    CASE_SYSTEM,
    CASE_USER_TEMPLATE,
)
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

# 结构化摘要的解析正则：匹配 【摘要】...【正文】 之间的内容
_SUMMARY_RE = re.compile(r"【摘要】\s*\n(.*?)(?=【正文】)", re.DOTALL)


def _parse_case_summary(reply: str) -> dict | None:
    """从 LLM 回复中解析结构化摘要块。

    回复格式：
      【摘要】
      诊断：...
      用药：...
      复诊：...
      关注：...
      【正文】
      一、...

    解析失败时返回 None（前端退化为不展示卡片）。
    """
    m = _SUMMARY_RE.search(reply)
    if not m:
        return None
    block = m.group(1).strip()
    summary: dict[str, str] = {}
    for key in ("诊断", "用药", "复诊", "关注"):
        # 匹配 "诊断：xxx" 或 "诊断: xxx"
        km = re.search(rf"{key}[：:]\s*(.+?)(?=\n\S+[：:]|\Z)", block, re.DOTALL)
        if km:
            summary[key] = km.group(1).strip()
    return summary if summary else None


def _strip_summary_block(reply: str) -> str:
    """从回复中移除 【摘要】...【正文】 块，只保留正文部分供前端展示。"""
    return _SUMMARY_RE.sub("", reply).replace("【正文】", "").strip()


async def analyze_case(
    case_text: str,
    *,
    session_id: str = "",
    account_id: str = "",
) -> dict:
    """病例解读主入口。

    返回 dict：{reply, sources, crisis, agent, case_summary, case_text}
    - crisis=True 时 reply 为硬编码危机话术（零 LLM），sources=[]
    - 正常时 reply 为 5 段科普解读（已剥离摘要块），case_summary 为结构化摘要 dict
    - case_text 回传原文，供前端持有并支持后续追问
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
            case_summary = None
        else:
            # 解析结构化摘要并从回复中剥离（前端展示正文，卡片渲染摘要）
            case_summary = _parse_case_summary(reply)
            if case_summary:
                reply = _strip_summary_block(reply)
            logger.info("case: reply len=%d, summary=%s", len(reply), bool(case_summary))
    except Exception as e:
        logger.warning("case: LLM failed: %s, using fallback", e)
        reply = CASE_FALLBACK_REPLY
        case_summary = None

    return {
        "reply": reply,
        "sources": formatted_sources,
        "crisis": False,
        "agent": "case",
        "case_summary": case_summary,
        "case_text": case_text,
    }


async def case_followup(
    case_text: str,
    interpretation: str,
    question: str,
) -> str:
    """病例追问：基于已解读的文书原文和首次解读，回答用户的追问。

    赻轻量 LLM 调用（role=report，温度 0.3），不重新跑 5 段结构。
    前置危机扫描同样适用。
    """
    # 追问内容也需危机扫描
    is_crisis, words = detect_crisis_with_words(question)
    if is_crisis:
        from app.agents.nodes.escalation import escalation_node as _esc
        state: AgentState = {
            "user_message": question,
            "detected_words": words,
            "is_crisis": True,
            "agent_trace": ["case_followup", "escalation"],
        }
        esc_out = await _esc(state)
        return esc_out["final_reply"]

    user_prompt = CASE_FOLLOWUP_TEMPLATE.format(
        case_text=case_text[:3000],  # 截断防超长
        interpretation=interpretation[:1000],
        question=question,
    )
    try:
        reply = await provider.chat(
            role="report",
            messages=[
                {"role": "system", "content": CASE_FOLLOWUP_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        if not reply or not reply.strip():
            reply = "这一点我暂时无法回答，建议带上文书当面咨询医生。你还想了解哪部分？"
    except Exception as e:
        logger.warning("case_followup: LLM failed: %s", e)
        reply = "这一点我暂时无法回答，建议带上文书当面咨询医生。你还想了解哪部分？"
    return reply
