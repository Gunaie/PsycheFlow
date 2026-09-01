"""Triage 分诊节点：前置 detect_crisis 硬编码短路 + LLM 意图分类。

安全原则：detect_crisis_with_words 在任何 LLM 调用前执行，命中即直接设置
is_crisis=true 跳过 LLM（Escalation 节点处理），不进入意图分类 LLM 调用。
"""
import logging

from app.agents.prompts import TRIAGE_SYSTEM, TRIAGE_USER_TEMPLATE
from app.agents.state import AgentState
from app.core.llm import provider
from app.core.safety import detect_crisis_with_words

logger = logging.getLogger("psycheflow.agents.triage")


async def triage_node(state: AgentState) -> dict:
    """分诊节点。

    流程：
    1. detect_crisis_with_words 硬编码前置扫描（零 LLM）
    2. 命中 → is_crisis=true，current_agent=triage，不调 LLM
    3. 未命中 → 调 provider.chat(role="intake", temp=0.1) 分类意图
    """
    message = state.get("user_message", "")
    trace = state.get("agent_trace", []) + ["triage"]

    # 1. 前置硬编码危机扫描
    is_crisis, detected_words = detect_crisis_with_words(message)
    if is_crisis:
        logger.info("triage: crisis hit, words=%s, skip LLM", detected_words)
        return {
            "is_crisis": True,
            "crisis": True,
            "detected_words": detected_words,
            "triage_intent": "危机",
            "current_agent": "triage",
            "agent_trace": trace,
        }

    # 2. LLM 意图分类（role=intake 温度 0.1 确定性优先）
    try:
        user_prompt = TRIAGE_USER_TEMPLATE.format(message=message)
        reply = await provider.chat(
            role="intake",
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=20,
        )
        intent = reply.strip()
        # 兜底：LLM 幻觉出非 4 类标签 → 默认走倾诉（最安全路径）
        if intent not in ("求助", "倾诉", "咨询", "危机"):
            logger.warning("triage: unexpected intent %r, fallback to 倾诉", intent)
            intent = "倾诉"
    except Exception as e:
        logger.warning("triage: LLM failed %s, fallback to 倾诉", str(e))
        intent = "倾诉"

    logger.info("triage: intent=%s", intent)
    return {
        "is_crisis": False,
        "crisis": False,
        "detected_words": [],
        "triage_intent": intent,
        "current_agent": "triage",
        "agent_trace": trace,
    }
