"""Escalation 升级节点：硬编码兜底，零 LLM 调用。

安全原则：本节点永不调用 provider.chat()。crisis_message() + 12355 硬编码 +
write_crisis_audit 落盘。crisis_*.json 审计不可丢。
"""
import logging

from app.agents.state import AgentState
from app.core.audit import write_crisis_audit
from app.core.safety import crisis_message

logger = logging.getLogger("psycheflow.agents.escalation")


async def escalation_node(state: AgentState) -> dict:
    """升级节点：零 LLM 调用。

    流程：
    1. crisis_message() 取硬编码话术（含 12355）
    2. write_crisis_audit 落盘审计 JSON
    3. 设置 final_reply + crisis=true + sources=[]
    """
    trace = state.get("agent_trace", []) + ["escalation"]

    # 1. 硬编码兜底话术
    reply = crisis_message()
    detected_words = state.get("detected_words", [])
    sid = state.get("session_id")
    account_id = state.get("account_id")
    assessment_context = state.get("assessment_context") or None

    # 2. 审计落盘（不阻断主业务）
    audit_path = write_crisis_audit(
        session_id=sid,
        account_id=account_id,
        trigger_words=detected_words,
        user_input_raw=state.get("user_message", ""),
        crisis_reply=reply,
        assessment_context=assessment_context,
    )
    logger.info(
        "escalation: crisis words=%s, audit_path=%s",
        detected_words, audit_path,
    )

    return {
        "final_reply": reply,
        "sources": [],
        "crisis": True,
        "is_crisis": True,
        "current_agent": "escalation",
        "agent_trace": trace,
    }
