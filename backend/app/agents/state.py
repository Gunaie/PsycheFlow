"""AgentState：四智能体共享状态 TypedDict。

参考 EshaRana17/mental-health-multi-agent-pipeline 的 LangGraph 状态范式。
每个节点接收 state 子集，返回 dict 增量 merge 到全局 state。
"""
from typing import TypedDict


class AgentState(TypedDict, total=False):
    """LangGraph 共享状态（total=False 让所有字段可选，便于增量更新）。"""

    # 输入
    session_id: str
    account_id: str
    user_message: str
    history: list  # [{role, content}, ...]
    persona_id: str  # 干预人格（default/sister/senior/listener），未知回退 default

    # Triage 节点输出
    detected_words: list  # [str]
    is_crisis: bool
    triage_intent: str  # 求助/倾诉/咨询/危机

    # Assessment 节点输出
    has_assessment: bool
    assessment_context: dict  # {scale_id, severity, crisis_level, total_score}

    # Intervention 节点输出
    rag_sources: list  # [{text, source, chunk_id, distance}]
    final_reply: str
    sources: list  # 前端渲染用 sources 字段（同 rag_sources 结构）

    # 通用
    current_agent: str  # triage/assessment/intervention/escalation
    agent_trace: list  # [str] 节点访问顺序审计
    crisis: bool  # 最终是否危机（同 is_crisis，给 API 返回用）
