"""AC-B5: 4 智能体完整 happy path 集成测试。

验证 LangGraph StateGraph 端到端编排：
- 普通倾诉 → triage→assessment→intervention，agent_trace 完整
- 危机场景 → triage→escalation，跳过 assessment+intervention
- API /api/chat 集成：返回 current_agent + agent_trace 字段
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph import graph
from app.agents.nodes.intervention import build_intervention_messages
from app.agents.state import AgentState


@pytest.mark.asyncio
async def test_integration_normal_path_talk_venting():
    """普通倾诉：triage→assessment→intervention 完整链路"""
    initial_state = {
        "session_id": "test-int-sid-001",
        "account_id": "test-int-acc-001",
        "user_message": "我感觉最近压力大",
        "history": [],
        "agent_trace": [],
    }
    with patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_intv_p.chat = AsyncMock(return_value="我听到你最近压力很大，我陪你聊聊。")
        mock_rag.search = AsyncMock(return_value=[
            {"text": "压力管理技巧", "source": "cbt_techniques.md", "chunk_id": 0, "distance": 0.5},
        ])
        final_state = await graph.ainvoke(initial_state)

    assert final_state["current_agent"] == "intervention"
    assert final_state["agent_trace"] == ["triage", "assessment", "intervention"]
    assert final_state["crisis"] is False
    assert final_state["triage_intent"] == "倾诉"
    assert final_state["final_reply"] == "我听到你最近压力很大，我陪你聊聊。"
    assert len(final_state["sources"]) == 1
    assert final_state["sources"][0]["source"] == "cbt_techniques.md"


@pytest.mark.asyncio
async def test_integration_crisis_path_skips_assessment_intervention():
    """危机场景：triage→escalation，跳过 assessment+intervention"""
    initial_state = {
        "session_id": "test-int-sid-002",
        "account_id": "test-int-acc-002",
        "user_message": "我想自杀",
        "history": [],
        "agent_trace": [],
    }
    with patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.escalation.write_crisis_audit") as mock_audit:
        mock_intv_p.chat = AsyncMock(return_value="should_not_be_called_for_crisis")
        mock_audit.return_value = "/tmp/crisis_test.json"
        final_state = await graph.ainvoke(initial_state)

    # intervention 节点不应被调用
    mock_intv_p.chat.assert_not_called()
    # 验证路由
    assert final_state["current_agent"] == "escalation"
    assert final_state["agent_trace"] == ["triage", "escalation"]
    assert final_state["crisis"] is True
    assert "12355" in final_state["final_reply"]
    assert final_state["sources"] == []


@pytest.mark.asyncio
async def test_integration_consult_path_with_rag_sources():
    """咨询场景（知识问句规则 → 咨询）：triage→assessment→intervention + RAG sources 返回"""
    initial_state = {
        "session_id": "test-int-sid-003",
        "account_id": "test-int-acc-003",
        "user_message": "什么是抑郁",
        "history": [],
        "agent_trace": [],
    }
    with patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_intv_p.chat = AsyncMock(return_value=(
            "抑郁是持续心境低落的状态。"
            "来源：《ccmd3_summary.md》"
        ))
        mock_rag.search = AsyncMock(return_value=[
            {"text": "抑郁发作核心症状三条", "source": "ccmd3_summary.md", "chunk_id": 0, "distance": 0.3},
            {"text": "CBT 认知重构", "source": "cbt_techniques.md", "chunk_id": 1, "distance": 0.6},
        ])
        final_state = await graph.ainvoke(initial_state)

    assert final_state["current_agent"] == "intervention"
    assert final_state["agent_trace"] == ["triage", "assessment", "intervention"]
    # 知识问句规则路由 → 咨询（先答问骨架 + RAG）
    assert final_state["triage_intent"] == "咨询"
    assert len(final_state["sources"]) == 2
    assert final_state["sources"][0]["source"] == "ccmd3_summary.md"
    assert "抑郁" in final_state["final_reply"]


@pytest.mark.asyncio
async def test_integration_help_request_no_assessment_context():
    """求助场景：help fast-path 直达渠道话术，不经过 assessment/intervention"""
    initial_state = {
        "session_id": "test-int-sid-004-no-record",  # 不存在的 session
        "account_id": "test-int-acc-004",
        "user_message": "我想做测评",
        "history": [],
        "agent_trace": [],
    }
    with patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_intv_p.chat = AsyncMock(return_value="建议你前往 /scale 完成测评。")
        mock_rag.search = AsyncMock(return_value=[])
        final_state = await graph.ainvoke(initial_state)

    # 求助渠道走 fast-path 直达，不经 assessment/intervention
    assert final_state["current_agent"] == "triage"
    assert final_state["agent_trace"] == ["triage"]
    assert final_state["triage_intent"] == "求助"
    assert "final_reply" in final_state
    mock_intv_p.chat.assert_not_called()


@pytest.mark.asyncio
async def test_integration_empty_llm_reply_triggers_fallback():
    """LLM 返回空字符串 → Intervention 节点 fallback 话术（同 reports 教训）。"""
    initial_state = {
        "session_id": "test-int-sid-005-empty",
        "account_id": "test-int-acc-005",
        "user_message": "我心情不好",
        "history": [],
        "agent_trace": [],
    }
    with patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_intv_p.chat = AsyncMock(return_value="")  # 空回复
        mock_rag.search = AsyncMock(return_value=[])
        final_state = await graph.ainvoke(initial_state)

    assert final_state["current_agent"] == "intervention"
    reply = final_state["final_reply"]
    # fallback 话术非空且含 12355 热线
    assert reply and reply.strip()
    assert "12355" in reply


# ================= intervention 按意图跳过 RAG =================

@pytest.mark.asyncio
async def test_build_messages_skips_rag_for_greeting_intent():
    """triage_intent=寒暄 → build_intervention_messages 跳过 RAG（快速通道异常兜底场景）。"""
    state: AgentState = {"user_message": "你好", "triage_intent": "寒暄", "agent_trace": []}
    with patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_rag.search = AsyncMock(return_value=[
            {"text": "无关片段", "source": "s.md", "chunk_id": 0},
        ])
        _, formatted_sources, rag_sources, decision = await build_intervention_messages(state)
    mock_rag.search.assert_not_awaited()
    assert formatted_sources == []
    assert rag_sources == []
    assert decision["rag"]["skipped"] is True


@pytest.mark.asyncio
async def test_build_messages_searches_rag_for_venting_intent():
    """triage_intent=倾诉 → RAG 正常检索（对照用例）。"""
    state: AgentState = {"user_message": "我压力大", "triage_intent": "倾诉", "agent_trace": []}
    with patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_rag.search = AsyncMock(return_value=[
            {"text": "深呼吸放松", "source": "04_放松技术.txt", "chunk_id": 3},
        ])
        _, formatted_sources, rag_sources, decision = await build_intervention_messages(state)
    mock_rag.search.assert_awaited_once()
    assert len(rag_sources) == 1
    assert formatted_sources[0]["source"] == "04_放松技术.txt"
    assert decision["rag"]["count"] == 1
