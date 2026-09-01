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
    with patch("app.agents.nodes.triage.provider") as mock_triage_p, \
         patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_triage_p.chat = AsyncMock(return_value="倾诉")
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
    with patch("app.agents.nodes.triage.provider") as mock_triage_p, \
         patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.escalation.write_crisis_audit") as mock_audit:
        mock_triage_p.chat = AsyncMock(return_value="should_not_be_called_for_crisis")
        mock_intv_p.chat = AsyncMock(return_value="should_not_be_called_for_crisis")
        mock_audit.return_value = "/tmp/crisis_test.json"
        final_state = await graph.ainvoke(initial_state)

    # triage 命中 crisis 应跳过 LLM 意图分类
    mock_triage_p.chat.assert_not_called()
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
    """咨询场景：triage→assessment→intervention + RAG sources 返回"""
    initial_state = {
        "session_id": "test-int-sid-003",
        "account_id": "test-int-acc-003",
        "user_message": "什么是抑郁",
        "history": [],
        "agent_trace": [],
    }
    with patch("app.agents.nodes.triage.provider") as mock_triage_p, \
         patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_triage_p.chat = AsyncMock(return_value="咨询")
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
    assert final_state["triage_intent"] == "咨询"
    assert len(final_state["sources"]) == 2
    assert final_state["sources"][0]["source"] == "ccmd3_summary.md"
    assert "抑郁" in final_state["final_reply"]


@pytest.mark.asyncio
async def test_integration_help_request_no_assessment_context():
    """求助场景：Session 无 assessment → has_assessment=false"""
    initial_state = {
        "session_id": "test-int-sid-004-no-record",  # 不存在的 session
        "account_id": "test-int-acc-004",
        "user_message": "我想做测评",
        "history": [],
        "agent_trace": [],
    }
    with patch("app.agents.nodes.triage.provider") as mock_triage_p, \
         patch("app.agents.nodes.intervention.provider") as mock_intv_p, \
         patch("app.agents.nodes.intervention.rag_service") as mock_rag:
        mock_triage_p.chat = AsyncMock(return_value="求助")
        mock_intv_p.chat = AsyncMock(return_value="建议你前往 /scale 完成测评。")
        mock_rag.search = AsyncMock(return_value=[])
        final_state = await graph.ainvoke(initial_state)

    assert final_state["current_agent"] == "intervention"
    assert final_state["agent_trace"] == ["triage", "assessment", "intervention"]
    assert final_state["triage_intent"] == "求助"
    assert final_state["has_assessment"] is False
    assert final_state["assessment_context"] == {}
