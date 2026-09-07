"""AC-B1: Triage 意图分类（4 case）+ detect_crisis 前置短路 + detect_greeting 硬编码寒暄。

验证：
1. 「我想做测评」→ triage_intent=求助
2. 「我感觉最近压力大」→ triage_intent=倾诉
3. 「什么是抑郁」→ triage_intent=咨询
4. 「我想自杀」→ is_crisis=true，跳过 LLM 意图分类直接 return
5. 「你好，你是谁」→ detect_greeting 硬编码命中，跳过 LLM 分类直达快速通道
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.nodes.triage import detect_greeting, triage_node
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


# ================= detect_greeting 硬编码寒暄识别 =================

class TestDetectGreeting:
    @pytest.mark.parametrize("text", [
        "你好", "您好", "hi", "hello", "Hello!", "嗨", "哈喽",
        "在吗", "在么", "在吗？在吗", "早安", "晚安",
        "你是谁", "你是谁？", "你是谁呀", "你到底是谁",
        "你是机器人吗", "你是AI吗", "你是真人吗",
        "你好，你是谁", "请问你是谁", "你能做什么？",
        "who are you",
    ])
    def test_greeting_hit(self, text):
        assert detect_greeting(text) is True

    @pytest.mark.parametrize("text", [
        "", "   ",
        "你好，我最近压力大",          # 混有倾诉内容
        "你好，我叫小明",              # 混有自我介绍
        "我想做测评", "今天天气如何", "我心情不好", "什么是抑郁",
        "你好你好你好你好你好你好你好你好你好你好你好你好",  # >30 长度排除
    ])
    def test_greeting_miss(self, text):
        assert detect_greeting(text) is False


@pytest.mark.asyncio
async def test_triage_greeting_hardcoded_fast_path():
    """「你好，你是谁」→ 硬编码寒暄命中：跳过 LLM 意图分类，直达快速通道产出 final_reply"""
    state: AgentState = {"user_message": "你好，你是谁", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="你好呀，我是暖暖～")
        result = await triage_node(state)
    # 仅调用 1 次 LLM（生成问候回复），未做意图分类
    mock_provider.chat.assert_awaited_once()
    assert mock_provider.chat.call_args.kwargs["role"] == "triage"
    assert result["final_reply"] == "你好呀，我是暖暖～"
    assert result["triage_intent"] == "寒暄"
    assert result["is_crisis"] is False
    assert result["current_agent"] == "triage"
    assert result["node_decisions"]["triage"]["decision"] == "fast_path_greeting"
    assert result["node_decisions"]["triage"]["type"] == "keyword_match"


@pytest.mark.asyncio
async def test_triage_greeting_with_content_not_hardcoded():
    """「你好，我最近压力大」混有倾诉内容 → 不命中硬编码，走 LLM 意图分类"""
    state: AgentState = {"user_message": "你好，我最近压力大", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="倾诉")
        result = await triage_node(state)
    mock_provider.chat.assert_awaited_once()  # 只有意图分类这一跳
    assert result["triage_intent"] == "倾诉"
    assert "final_reply" not in result


@pytest.mark.asyncio
async def test_triage_greeting_fast_path_llm_failure_fallback():
    """硬编码命中但问候生成失败 → 回退意图分类（同样失败）→ 倾诉正常链路"""
    state: AgentState = {"user_message": "你好", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(side_effect=Exception("network error"))
        result = await triage_node(state)
    # 调用 2 次：快速通道生成 + 意图分类，全部失败后兜底倾诉
    assert mock_provider.chat.await_count == 2
    assert result["triage_intent"] == "倾诉"
    assert "final_reply" not in result
    assert result["is_crisis"] is False
