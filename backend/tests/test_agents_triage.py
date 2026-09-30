"""Triage 分诊路由测试（2026-09-30 起 triage 为全规则路由，零 LLM）。

验证路由顺序：
1. 危机词前置短路（detect_crisis_with_words）
2. 寒暄静态话术直达（detect_greeting + greeting_static_reply）
3. 咨询规则：服务边界/方法问句/知识问句
4. 求助渠道静态话术直达（detect_help_channel）
5. 显式求助祈使（detect_help_plea）
6. 其余默认倾诉
"""
import pytest

from app.agents.nodes.triage import (
    detect_greeting,
    detect_help_channel,
    detect_knowledge_question,
    detect_method_question,
    detect_service_question,
    greeting_static_reply,
    triage_node,
)
from app.agents.state import AgentState


def _state(message: str) -> AgentState:
    return {"user_message": message, "agent_trace": []}


# ================= 基础五类路由 =================

@pytest.mark.asyncio
async def test_triage_intent_help_request():
    """「我想做测评」→ 求助渠道快速通道（硬编码渠道话术直达，零 LLM）"""
    result = await triage_node(_state("我想做测评"))
    assert result["triage_intent"] == "求助"
    assert result["is_crisis"] is False
    assert result["current_agent"] == "triage"
    assert result["agent_trace"] == ["triage"]
    assert "final_reply" in result  # 求助渠道直达话术


@pytest.mark.asyncio
async def test_triage_intent_venting():
    """「我感觉最近压力大」→ 默认倾诉"""
    result = await triage_node(_state("我感觉最近压力大"))
    assert result["triage_intent"] == "倾诉"
    assert result["is_crisis"] is False
    assert "final_reply" not in result
    assert result["node_decisions"]["triage"]["decision"] == "default_vent"


@pytest.mark.asyncio
async def test_triage_intent_consult_knowledge():
    """「什么是抑郁」→ 知识问句 → 咨询（先答问骨架）"""
    result = await triage_node(_state("什么是抑郁"))
    assert result["triage_intent"] == "咨询"
    assert result["node_decisions"]["triage"]["decision"] == "knowledge_question"
    assert "final_reply" not in result


@pytest.mark.asyncio
async def test_triage_crisis_short_circuit():
    """「我想自杀」→ 危机词命中，最高优先级短路"""
    result = await triage_node(_state("我想自杀"))
    assert result["is_crisis"] is True
    assert result["crisis"] is True
    assert "自杀" in result["detected_words"]
    assert result["triage_intent"] == "危机"


@pytest.mark.asyncio
async def test_triage_unknown_message_defaults_venting():
    """无关消息（今天天气如何）→ 默认倾诉（最安全路径）"""
    result = await triage_node(_state("今天天气如何"))
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


class TestGreetingStaticReply:
    def test_identity_question_introduces_persona(self):
        reply = greeting_static_reply("你是谁", "暖暖")
        assert "暖暖" in reply
        assert "心理支持伙伴" in reply

    def test_plain_greeting_warm_ack(self):
        reply = greeting_static_reply("你好", "暖暖")
        assert "暖暖" in reply
        # 静态话术不允许出现闭合问句句末
        assert not reply.rstrip().endswith(("吗", "吗？", "吧？"))


@pytest.mark.asyncio
async def test_triage_greeting_static_fast_path():
    """「你好，你是谁」→ 寒暄命中：静态自我介绍话术直达（零 LLM）"""
    result = await triage_node(_state("你好，你是谁"))
    assert result["triage_intent"] == "寒暄"
    assert "暖暖" in result["final_reply"]
    assert result["is_crisis"] is False
    assert result["current_agent"] == "triage"
    assert result["node_decisions"]["triage"]["decision"] == "fast_path_greeting"


@pytest.mark.asyncio
async def test_triage_plain_greeting_static_reply():
    """「你好」→ 纯打招呼静态话术"""
    result = await triage_node(_state("你好"))
    assert result["triage_intent"] == "寒暄"
    assert "final_reply" in result


@pytest.mark.asyncio
async def test_triage_greeting_with_content_defaults_venting():
    """「你好，我最近压力大」混有倾诉内容 → 不命中寒暄 → 默认倾诉"""
    result = await triage_node(_state("你好，我最近压力大"))
    assert result["triage_intent"] == "倾诉"
    assert "final_reply" not in result


# ================= 咨询规则：方法/服务/知识问句 =================

class TestDetectMethodQuestion:
    @pytest.mark.parametrize("text", [
        "怎么缓解焦虑", "如何改善睡眠", "怎样克服考前紧张",
        "如何应对考试焦虑", "焦虑怎么缓解", "缓解焦虑的方法",
        "睡不着怎么办", "压力大怎么办？", "有什么办法缓解紧张", "失眠有什么方法",
        "怎么控制情绪", "我该怎么跟他沟通", "怎么才能自律一点",
    ])
    def test_method_hit(self, text):
        assert detect_method_question(text) is True

    @pytest.mark.parametrize("text", [
        "", "   ",
        "我怎么这么没用",           # 自我否定倾诉，非方法问句
        "你好，我最近压力大",
        "什么是抑郁",
        "我想做测评",               # 求助渠道，方法问句不命中
        "我最近考试压力很大晚上总是睡不着白天上课也提不起精神快撑不住了怎么办啊老师我真的很累很累很累很累",  # >30 长度排除
    ])
    def test_method_miss(self, text):
        assert detect_method_question(text) is False


class TestDetectKnowledgeQuestion:
    @pytest.mark.parametrize("text", [
        "什么是抑郁症", "抑郁症和心情不好有什么区别",
        "深呼吸有科学依据吗", "什么是正念",
        "青春期情绪波动大是正常的吗", "抑郁症需要吃药吗",
        "怎样才算心理健康", "焦虑障碍包括哪些类型",
        "心理咨询一般要做多少次才有效果",
    ])
    def test_knowledge_hit(self, text):
        assert detect_knowledge_question(text) is True

    @pytest.mark.parametrize("text", [
        "", "   ",
        "最近心情很差，什么都懒得做",  # 倾诉，非知识问句
        "我想做测评",
        "心理咨询师会把我说的告诉家长吗，我真的真的真的很担心他们知道后会不会骂我打我不让我上学了",  # >32 长度排除
    ])
    def test_knowledge_miss(self, text):
        assert detect_knowledge_question(text) is False


class TestDetectServiceQuestion:
    @pytest.mark.parametrize("text", [
        "这里聊天会保密吗",
        "心理咨询师会把我说的内容告诉学校和家长吗",
        "你这里都能做什么",
    ])
    def test_service_hit(self, text):
        assert detect_service_question(text) is True


@pytest.mark.asyncio
async def test_triage_method_question_routes_consult():
    """「帮帮我，有什么办法缓解焦虑」方法问句命中 → 咨询（咨询规则先于求助祈使）"""
    result = await triage_node(_state("帮帮我，有什么办法缓解焦虑"))
    assert result["triage_intent"] == "咨询"
    assert result["node_decisions"]["triage"]["decision"] == "method_question"
    assert result["is_crisis"] is False


@pytest.mark.asyncio
async def test_triage_bare_zenmeban_routes_consult():
    """「好烦啊怎么办」含明确求做法 → 咨询（先答问骨架同样给做法）"""
    result = await triage_node(_state("好烦啊怎么办"))
    assert result["triage_intent"] == "咨询"
    assert result["node_decisions"]["triage"]["decision"] == "method_question"


@pytest.mark.asyncio
async def test_triage_service_question_routes_consult():
    """服务边界问句（保密）→ 咨询，且不误入求助渠道直达"""
    result = await triage_node(_state("心理咨询师会把我说的内容告诉学校和家长吗"))
    assert result["triage_intent"] == "咨询"
    assert result["node_decisions"]["triage"]["decision"] == "service_question"
    assert "final_reply" not in result


@pytest.mark.asyncio
async def test_triage_consult_background_not_misrouted_help():
    """「心理咨询一般要做多少次才有效果」知识问句含"心理咨询"，不得误入求助渠道"""
    result = await triage_node(_state("心理咨询一般要做多少次才有效果"))
    assert result["triage_intent"] == "咨询"
    assert "final_reply" not in result


@pytest.mark.asyncio
async def test_triage_crisis_precedes_method_question():
    """「我想自杀怎么办」危机词前置短路优先于方法问句（安全顺序不破坏）"""
    result = await triage_node(_state("我想自杀怎么办"))
    assert result["is_crisis"] is True
    assert result["triage_intent"] == "危机"


# ================= 求助渠道 / 求助祈使 =================

class TestDetectHelpChannel:
    @pytest.mark.parametrize("text", [
        "我想做测评",
        "有没有测抑郁的量表",
        "学校心理咨询室怎么预约",
        "想找心理咨询师聊聊，怎么联系",
        "网上有没有靠谱的心理咨询平台",
        "班主任让我做个心理评估",
        "想找专业的人聊聊，哪里可以去",
        "我想知道自己是不是有焦虑症，该做什么检查",
    ])
    def test_channel_hit(self, text):
        assert detect_help_channel(text) is True


@pytest.mark.asyncio
async def test_triage_true_help_request_fast_path():
    """「我想做测评」求助渠道 → 直达渠道话术"""
    result = await triage_node(_state("我想做测评"))
    assert result["triage_intent"] == "求助"
    assert "final_reply" in result


@pytest.mark.asyncio
async def test_triage_help_plea_routes_help():
    """「被起外号…帮帮我」显式求助祈使（非渠道）→ 求助，走 intervention 求助骨架"""
    msg = "他们当着全班的面给我起难听的外号，还故意把我课本藏起来，我实在受不了了，谁来帮帮我"
    result = await triage_node(_state(msg))
    assert result["triage_intent"] == "求助"
    assert result["node_decisions"]["triage"]["decision"] == "help_plea"
    assert "final_reply" not in result  # 不直达，进 intervention
