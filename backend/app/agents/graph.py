"""LangGraph StateGraph：四智能体编排拓扑。

[START] → triage → (is_crisis=true) → escalation → END
                  → (is_crisis=false) → assessment → intervention → END

节点均为 async function(state) -> dict，graph.ainvoke 入口支持 async。
"""
from typing import Literal

from langgraph.graph import END, StateGraph

from app.agents.nodes.assessment import assessment_node
from app.agents.nodes.escalation import escalation_node
from app.agents.nodes.intervention import intervention_node
from app.agents.nodes.triage import triage_node
from app.agents.state import AgentState


def route_after_triage(state: AgentState) -> Literal["escalation", "assessment", "END"]:
    """conditional edge：
    1. is_crisis=true 走 escalation
    2. triage 已产出 final_reply（如寒暄直达）走 END
    3. 否则进入 assessment
    """
    if state.get("is_crisis", False):
        return "escalation"
    if state.get("final_reply"):
        return "END"
    return "assessment"


def build_graph():
    """构建并编译 StateGraph。"""
    g = StateGraph(AgentState)

    g.add_node("triage", triage_node)
    g.add_node("assessment", assessment_node)
    g.add_node("intervention", intervention_node)
    g.add_node("escalation", escalation_node)

    g.set_entry_point("triage")
    g.add_conditional_edges(
        "triage",
        route_after_triage,
        {
            "escalation": "escalation",
            "assessment": "assessment",
            "END": END
        },
    )
    g.add_edge("assessment", "intervention")
    g.add_edge("intervention", END)
    g.add_edge("escalation", END)

    return g.compile()


# 单例
graph = build_graph()
