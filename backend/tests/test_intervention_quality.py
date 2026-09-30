"""回复质检重试层单测：check_reply_quality 正则质检 + intervention_node /
stream_intervention 不合格重试 1 次的行为（封闭式问句 / 历史逐字重复）。

质检口径与 scripts/dialog_smoke.py 一致：闭合问句（对吧/对吗/是不是/是吧/好吗）。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.nodes.intervention import (
    FALLBACK_REPLY,
    RETRY_HINT,
    check_reply_quality,
    intervention_node,
    stream_intervention,
)

PATCH_PROVIDER = "app.agents.nodes.intervention.provider"
PATCH_RAG = "app.agents.nodes.intervention.rag_service"


def _state(**overrides):
    """构造最小可用的 AgentState（intervention 直调，不走 graph）。"""
    state = {
        "user_message": "最近考试压力大",
        "triage_intent": "倾诉",
        "history": [],
        "persona_id": "default",
        "has_assessment": False,
        "assessment_context": {},
        "agent_trace": ["triage"],
        "node_decisions": {},
    }
    state.update(overrides)
    return state


class TestCheckReplyQuality:
    def test_clean_reply_passes(self):
        assert check_reply_quality("我听到了，愿意多说说吗", []) is True

    def test_empty_reply_fails(self):
        assert check_reply_quality("", []) is False
        assert check_reply_quality("   ", []) is False

    def test_closed_question_fails(self):
        for bad in ("这样说你能理解对吧？", "你是不是压力很大", "我们聊聊好吗", "是吧"):
            assert check_reply_quality(bad, []) is False, bad

    def test_verbatim_clause_repeat_fails(self):
        history = [{"role": "assistant", "content": "我听到你最近考试压力很大，一定很累。"}]
        reply = "我听到你最近考试压力很大。我们可以一起想想办法。"
        assert check_reply_quality(reply, history) is False

    def test_short_overlap_passes(self):
        """短分句（<12 字）出现在历史中不判重复（如「我们一起加油」口头禅式短语）。"""
        history = [{"role": "assistant", "content": "我们一起加油！"}]
        assert check_reply_quality("我们一起加油，慢慢来。", history) is True

    def test_history_user_role_ignored(self):
        """重复只比对 assistant 回复，用户原话被引用不算编造也不算重复。"""
        history = [{"role": "user", "content": "我最近考试压力大到睡不着觉"}]
        assert check_reply_quality("你说你考试压力大到睡不着觉，这很难熬。", history) is True

    def test_partial_template_repeat_fails(self):
        """换头不换身的半改写模板句（≥12 字逐字公共片段）也算复读。"""
        history = [{"role": "assistant", "content": "除了焦虑，你有没有感觉到身体其他地方也跟着紧绷起来了？"}]
        reply = "除了担心，你有没有感觉到身体其他地方也跟着紧绷起来了？"
        assert check_reply_quality(reply, history) is False

    def test_same_source_citation_repeat_passes(self):
        """多轮引用同一知识来源（来源：《xxx》）属合法，不算复读。"""
        history = [{"role": "assistant", "content": "可以试试腹式呼吸，来源：《06_焦虑科普.txt》"}]
        reply = "也可以尝试把担心写下来，来源：《06_焦虑科普.txt》"
        assert check_reply_quality(reply, history) is True


class TestInterventionNodeRetry:
    @pytest.mark.asyncio
    async def test_closed_question_triggers_retry_and_adopts_retry(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.chat = AsyncMock(side_effect=[
                "这样说你能理解对吧？",
                "我听到了你说的这些，最近考试的压力确实很大，让人有些喘不过气来，你愿意现在多和我说说你最担心的是哪部分吗",
            ])
            rag.search = AsyncMock(return_value=[])
            out = await intervention_node(_state())
            assert out["final_reply"] == "我听到了你说的这些，最近考试的压力确实很大，让人有些喘不过气来，你愿意现在多和我说说你最担心的是哪部分吗"
            assert p.chat.await_count == 2
            # 重试请求末尾附带纠正提示
            retry_messages = p.chat.await_args_list[1].kwargs["messages"]
            assert retry_messages[-1] == {"role": "system", "content": RETRY_HINT}
            assert out["node_decisions"]["intervention"]["llm"]["quality_retry"] is True

    @pytest.mark.asyncio
    async def test_retry_still_bad_keeps_first_reply(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            # max_retries=2 → 最多 3 次调用；全部不合格则保留首次回复
            p.chat = AsyncMock(side_effect=["你说的对吧？", "还是对吧？", "仍然对吧？"])
            rag.search = AsyncMock(return_value=[])
            out = await intervention_node(_state())
            assert out["final_reply"] == "你说的对吧？"
            assert p.chat.await_count == 3

    @pytest.mark.asyncio
    async def test_clean_reply_no_retry(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.chat = AsyncMock(return_value="我完全理解你最近承受的压力，考试临近确实容易让人感到焦虑不安，你愿意现在多和我说说具体是哪方面让你最困扰吗")
            rag.search = AsyncMock(return_value=[])
            out = await intervention_node(_state())
            assert out["final_reply"] == "我完全理解你最近承受的压力，考试临近确实容易让人感到焦虑不安，你愿意现在多和我说说具体是哪方面让你最困扰吗"
            assert p.chat.await_count == 1

    @pytest.mark.asyncio
    async def test_retry_exception_keeps_first_reply(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            first = "这样说你能理解对吧？"
            p.chat = AsyncMock(side_effect=[first, RuntimeError("ollama down")])
            rag.search = AsyncMock(return_value=[])
            out = await intervention_node(_state())
            assert out["final_reply"] == first

    @pytest.mark.asyncio
    async def test_repeat_against_history_triggers_retry(self):
        history = [
            {"role": "user", "content": "我压力很大"},
            {"role": "assistant", "content": "我听到你最近考试压力很大，一定很累。"},
            {"role": "user", "content": "嗯"},
        ]
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.chat = AsyncMock(
                side_effect=[
                    "我听到你最近考试压力很大。我们聊聊。",
                    "听起来你这段时间确实过得很熬人，考试压力加上周围人的期待一定让你紧绷，愿意说说最让你紧绷的是哪部分？",
                ]
            )
            rag.search = AsyncMock(return_value=[])
            out = await intervention_node(_state(history=history))
            assert out["final_reply"] == "听起来你这段时间确实过得很熬人，考试压力加上周围人的期待一定让你紧绷，愿意说说最让你紧绷的是哪部分？"


def _stream_mock(*token_lists):
    """构造 MagicMock(side_effect=[async_gen, ...])：每次调用返回下一个 token 列表的生成器。"""

    async def _gen(tokens):
        for t in tokens:
            yield t

    return MagicMock(side_effect=[_gen(tokens) for tokens in token_lists])


class TestStreamInterventionRetry:
    @pytest.mark.asyncio
    async def test_closed_question_triggers_retry_and_yields_retry(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.stream = _stream_mock(
                ["这样说你能理解", "对吧？"],
                ["我听到了你说的这些，最近考试的压力确实很大，", "让人有些喘不过气来，", "你愿意现在多和我说说你最担心的是哪部分吗"],
            )
            rag.search = AsyncMock(return_value=[])
            tokens = [t async for t in stream_intervention(_state())]
            assert "".join(tokens) == "我听到了你说的这些，最近考试的压力确实很大，让人有些喘不过气来，你愿意现在多和我说说你最担心的是哪部分吗"
            assert p.stream.call_count == 2

    @pytest.mark.asyncio
    async def test_clean_reply_streams_original_tokens_once(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            clean_tokens = ["我听到了你说的这些，最近考试的压力确实很大，", "让人有些喘不过气来，", "你愿意现在多和我说说你最担心的是哪部分吗"]
            p.stream = _stream_mock(clean_tokens)
            rag.search = AsyncMock(return_value=[])
            tokens = [t async for t in stream_intervention(_state())]
            # 保持原始 token 粒度（SSE token 事件语义不变），不重试
            assert tokens == clean_tokens
            assert p.stream.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_still_bad_yields_first_reply(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            # max_retries=2 → 最多 3 次调用；全部不合格沿用首次回复
            p.stream = _stream_mock(["你说的", "对吧？"], ["还是", "对吧？"], ["仍然", "对吧？"])
            rag.search = AsyncMock(return_value=[])
            tokens = [t async for t in stream_intervention(_state())]
            assert "".join(tokens) == "你说的对吧？"
            assert p.stream.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_stream_yields_fallback(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.stream = _stream_mock([])
            rag.search = AsyncMock(return_value=[])
            tokens = [t async for t in stream_intervention(_state())]
            assert "".join(tokens) == FALLBACK_REPLY

    @pytest.mark.asyncio
    async def test_stream_exception_yields_fallback(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            async def _exploding(*args, **kwargs):
                raise RuntimeError("dashscope 500")
                yield  # pragma: no cover

            p.stream = _exploding
            rag.search = AsyncMock(return_value=[])
            tokens = [t async for t in stream_intervention(_state())]
            assert "".join(tokens) == FALLBACK_REPLY
