"""Triage 分诊节点：前置 detect_crisis 硬编码短路 + LLM 意图分类。

安全原则：detect_crisis_with_words 在任何 LLM 调用前执行，命中即直接设置
is_crisis=true 跳过 LLM（Escalation 节点处理），不进入意图分类 LLM 调用。
"""
import logging

from app.agents.personas import get_persona, build_system_prompt
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
    decisions = state.get("node_decisions", {})
    
    if is_crisis:
        logger.info("triage: crisis hit, words=%s, skip LLM", detected_words)
        decisions["triage"] = {
            "decision": "crisis_detected",
            "type": "keyword_match",
            "detected_words": detected_words
        }
        return {
            "is_crisis": True,
            "crisis": True,
            "detected_words": detected_words,
            "triage_intent": "危机",
            "current_agent": "triage",
            "agent_trace": trace,
            "node_decisions": decisions
        }

    # 2. LLM 意图分类
    try:
        user_prompt = TRIAGE_USER_TEMPLATE.format(message=message)
        reply = await provider.chat(
            role="triage",
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=50,
        )
        intent = reply.strip()
        # 兜底：LLM 幻觉出非 5 类标签 → 默认走倾诉
        if intent not in ("寒暄", "求助", "倾诉", "咨询", "危机"):
            logger.warning("triage: unexpected intent %r, fallback to 倾诉", intent)
            intent = "倾诉"
            
        # 3. 极速直达路径：若是寒暄，直接用 0.5b 生成简短回复并跳过后续节点
        if intent == "寒暄":
            logger.info("triage: greeting detected, using fast-path")
            decisions["triage"] = {
                "decision": "fast_path_greeting",
                "intent": intent,
                "model": "qwen2.5:0.5b"
            }
            persona = get_persona(state.get("persona_id"))
            greeting_reply = await provider.chat(
                role="triage", # 继续复用 0.5b 模型
                messages=[
                    {"role": "system", "content": build_system_prompt(persona) + "\n请简洁地回应用户的打招呼或询问，不要开启 RAG 或深度对话。"},
                    {"role": "user", "content": message},
                ],
                temperature=0.7,
                max_tokens=100,
            )
            return {
                "is_crisis": False,
                "triage_intent": intent,
                "final_reply": greeting_reply,
                "current_agent": "triage",
                "agent_trace": trace,
                "node_decisions": decisions
            }

    except Exception as e:
        logger.warning("triage: LLM failed %s, fallback to 倾诉", str(e))
        intent = "倾诉"
        decisions["triage"] = {
            "decision": "fallback",
            "reason": str(e),
            "intent": intent
        }

    logger.info("triage: intent=%s", intent)
    if "triage" not in decisions:
        decisions["triage"] = {
            "decision": "intent_classified",
            "intent": intent
        }
        
    return {
        "is_crisis": False,
        "crisis": False,
        "detected_words": [],
        "triage_intent": intent,
        "current_agent": "triage",
        "agent_trace": trace,
        "node_decisions": decisions
    }
