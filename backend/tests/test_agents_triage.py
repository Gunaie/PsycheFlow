"""AC-B1: Triage 意图分类（4 case）+ detect_crisis 前置短路。

验证：
1. 「我想做测评」→ triage_intent=求助
2. 「我感觉最近压力大」→ triage_intent=倾诉
3. 「什么是抑郁」→ triage_intent=咨询
4. 「我想自杀」→ is_crisis=true，跳过 LLM 意图分类直接 return
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.nodes.triage import triage_node
from app.agents.state import AgentState


@pytest.mark.asyncio
async def test_triage_intent_help_request():
    """「我想做测评」→ 求助"""
    state: AgentState = {"user_message": "我想做测评", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="求助")
        result = await triage_node(state)
    assert result["triage_intent"] == "求助"
    assert result["is_crisis"] is False
    assert result["crisis"] is False
    assert result["detected_words"] == []
    assert result["current_agent"] == "triage"
    assert result["agent_trace"] == ["triage"]


@pytest.mark.asyncio
async def test_triage_intent_venting():
    """「我感觉最近压力大」→ 倾诉"""
    state: AgentState = {"user_message": "我感觉最近压力大", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="倾诉")
        result = await triage_node(state)
    assert result["triage_intent"] == "倾诉"
    assert result["is_crisis"] is False


@pytest.mark.asyncio
async def test_triage_intent_consult():
    """「什么是抑郁」→ 咨询"""
    state: AgentState = {"user_message": "什么是抑郁", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="咨询")
        result = await triage_node(state)
    assert result["triage_intent"] == "咨询"


@pytest.mark.asyncio
async def test_triage_crisis_short_circuit_skips_llm():
    """「我想自杀」→ detect_crisis_with_words 命中 → is_crisis=true + 不调 LLM"""
    state: AgentState = {"user_message": "我想自杀", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="should_not_be_called")
        result = await triage_node(state)
    # 验证 LLM 没被调用（硬编码前置短路）
    mock_provider.chat.assert_not_called()
    assert result["is_crisis"] is True
    assert result["crisis"] is True
    assert "自杀" in result["detected_words"]
    assert result["triage_intent"] == "危机"


@pytest.mark.asyncio
async def test_triage_llm_returns_unknown_label_fallback():
    """LLM 幻觉出非 4 类标签 → 默认 fallback 倾诉（最安全路径）"""
    state: AgentState = {"user_message": "今天天气如何", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="天气问题")
        result = await triage_node(state)
    assert result["triage_intent"] == "倾诉"  # fallback
    assert result["is_crisis"] is False


@pytest.mark.asyncio
async def test_triage_llm_failure_fallback():
    """LLM 调用失败 → fallback 倾诉不抛错"""
    state: AgentState = {"user_message": "我心情不好", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(side_effect=Exception("network error"))
        result = await triage_node(state)
    assert result["triage_intent"] == "倾诉"
    assert result["is_crisis"] is False
