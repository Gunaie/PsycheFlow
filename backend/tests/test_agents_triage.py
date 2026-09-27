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

from app.agents.nodes.triage import detect_greeting, detect_method_question, triage_node
from app.agents.state import AgentState


@pytest.mark.asyncio
async def test_triage_intent_help_request():
    """「我想做测评」→ 求助渠道快速通道（硬编码渠道话术直达，零 LLM）"""
    state: AgentState = {"user_message": "我想做测评", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="求助")
        result = await triage_node(state)
    assert result["triage_intent"] == "求助"
    assert result["is_crisis"] is False
    assert result["current_agent"] == "triage"
    assert result["agent_trace"] == ["triage"]
    assert "final_reply" in result  # 求助渠道直达话术
    mock_provider.chat.assert_not_called()  # 零 LLM


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
    """「什么是抑郁」→ 默认倾诉（规则化分诊：知识问句走 intervention，由 RAG+dialog 回答）"""
    state: AgentState = {"user_message": "什么是抑郁", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="咨询")
        result = await triage_node(state)
    assert result["triage_intent"] == "倾诉"
    mock_provider.chat.assert_not_called()  # 规则化，不调 LLM


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
    """「你好，我最近压力大」混有倾诉内容 → 不命中寒暄硬编码 → 默认倾诉（零 LLM）"""
    state: AgentState = {"user_message": "你好，我最近压力大", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="倾诉")
        result = await triage_node(state)
    mock_provider.chat.assert_not_called()  # 规则化分诊，不调 LLM
    assert result["triage_intent"] == "倾诉"
    assert "final_reply" not in result


@pytest.mark.asyncio
async def test_triage_greeting_fast_path_llm_failure_fallback():
    """硬编码命中但问候生成失败 → 回退默认倾诉（规则化分诊，不再二次调 LLM）"""
    state: AgentState = {"user_message": "你好", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(side_effect=Exception("network error"))
        result = await triage_node(state)
    # 只调用 1 次（问候生成失败），回退默认倾诉不调第二次 LLM
    assert mock_provider.chat.await_count == 1
    assert result["triage_intent"] == "倾诉"
    assert "final_reply" not in result
    assert result["is_crisis"] is False


# ================= detect_method_question 方法问句纠偏（求助→咨询） =================

class TestDetectMethodQuestion:
    @pytest.mark.parametrize("text", [
        "怎么缓解焦虑", "如何改善睡眠", "怎样克服考前紧张",
        "如何应对考试焦虑", "焦虑怎么缓解", "缓解焦虑的方法",
        "睡不着怎么办", "压力大怎么办？", "有什么办法缓解紧张", "失眠有什么方法",
    ])
    def test_method_hit(self, text):
        assert detect_method_question(text) is True

    @pytest.mark.parametrize("text", [
        "", "   ",
        "我怎么这么没用",           # 自我否定倾诉，非方法问句
        "你好，我最近压力大",
        "什么是抑郁",
        "我想做测评",               # 真求助，方法问句不命中，测评引导不受影响
        "我最近考试压力很大晚上总是睡不着白天上课也提不起精神快撑不住了怎么办啊老师我真的很累很累很累很累",  # >30 长度排除
    ])
    def test_method_miss(self, text):
        assert detect_method_question(text) is False


@pytest.mark.asyncio
async def test_triage_method_question_overrides_help_plea():
    """「帮帮我，有什么办法缓解焦虑」求助祈使 + 方法问句 → 咨询（求做法非求渠道）。
    规则化分诊链路：默认倾诉 → help_plea 改判求助 → method_question 改判咨询。"""
    state: AgentState = {"user_message": "帮帮我，有什么办法缓解焦虑", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="should_not_be_called")
        result = await triage_node(state)
    assert result["triage_intent"] == "咨询"
    assert result["node_decisions"]["triage"]["decision"] == "method_question_override"
    assert result["node_decisions"]["triage"]["original_intent"] == "求助"
    assert result["is_crisis"] is False
    mock_provider.chat.assert_not_called()


@pytest.mark.asyncio
async def test_triage_true_help_request_not_overridden():
    """「我想做测评」真求助（无方法问句）→ 保持求助，测评引导不受影响"""
    state: AgentState = {"user_message": "我想做测评", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="求助")
        result = await triage_node(state)
    assert result["triage_intent"] == "求助"


@pytest.mark.asyncio
async def test_triage_method_question_venting_stays_venting():
    """「好烦啊怎么办」LLM 判倾诉 → 保持倾诉（倾诉骨架同样给做法且 RAG 不跳过）"""
    state: AgentState = {"user_message": "好烦啊怎么办", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="倾诉")
        result = await triage_node(state)
    assert result["triage_intent"] == "倾诉"


@pytest.mark.asyncio
async def test_triage_crisis_precedes_method_question():
    """「我想自杀怎么办」危机词前置短路优先于方法问句（安全顺序不破坏）"""
    state: AgentState = {"user_message": "我想自杀怎么办", "agent_trace": []}
    with patch("app.agents.nodes.triage.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="咨询")
        result = await triage_node(state)
    mock_provider.chat.assert_not_called()
    assert result["is_crisis"] is True
    assert result["triage_intent"] == "危机"
