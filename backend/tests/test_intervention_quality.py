"""回复质检重试层单测：check_reply_quality 正则质检 + intervention_node /
stream_intervention 三级重试阶梯（hint 针对性禁令 → rag_refresh 换 RAG 片段 →
context_trim 剔除历史 assistant 回复）的行为（封闭式问句 / 历史逐字重复），
以及 build_retry_hint 针对性禁令生成（点名复读原句 / 上轮做法类别）。

质检口径与 scripts/dialog_smoke.py 一致：闭合问句（对吧/对吗/是不是/是吧/好吗）。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.nodes.intervention import (
    FALLBACK_REPLY,
    RETRY_HINT,
    _find_verbatim_overlap,
    _refresh_rag_sources,
    build_retry_hint,
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

    def test_breath_formula_not_verbatim_repeat(self):
        """呼吸参数公式（4吸6呼标准口径）跨轮重现不算复读——标准化表述非复读。

        项目硬约束统一「吸气4秒呼气6秒」口径，LoRA 表达呼吸法只有这一种句式，
        若按 ≥12 字逐字重叠判定会把任何跨轮呼吸建议误杀（2026-09-30 多轮评测实证：
        S3T4/S4T4 因该公式被判复读，三级重试也无法让模型用别的措辞表达同一参数）。
        注意历史末条须为非呼吸轮（同类做法重复只查上一轮 assistant），以隔离验证逐字规则。
        """
        history = [
            {"role": "assistant", "content": "紧张时试试腹式呼吸：吸气数四秒、呼气数六秒，做几轮。"},
            {"role": "user", "content": "嗯"},
            {"role": "assistant", "content": "被这么多人盯着确实很难受，你已经撑了很久了，这份委屈不容易。"},
        ]
        reply = "心慌时可以先做三轮腹式呼吸，吸气数四秒、呼气数六秒，让身体先稳住，再把注意力放回题目。"
        assert check_reply_quality(reply, history) is True

    def test_repeat_beyond_breath_formula_still_fails(self):
        """公式之外的真实复读仍被判不合格（剥离不能掩盖其余句子的照抄）。"""
        history = [{"role": "assistant", "content": "试试把担心的事写在纸上，写完就合上本子，告诉自己明天再处理。"}]
        reply = "可以试试把担心的事写在纸上，写完就合上本子，告诉自己明天再处理，然后做几轮腹式呼吸，吸气数四秒、呼气数六秒。"
        assert check_reply_quality(reply, history) is False

    def test_planning_category_detected(self):
        """任务拆解与 prompt 骨架示例做法同列，QC 分类学必须认识它。"""
        from app.agents.nodes.intervention import _detect_method_categories
        cats = _detect_method_categories("把复习任务拆成小块，每完成一件划掉一件，再列个清单。")
        assert "planning" in cats

    def test_planning_vs_breathing_not_same_category(self):
        """任务拆解轮与呼吸轮互不算同类做法重复。"""
        history = [{"role": "assistant", "content": "试试腹式呼吸，吸气四秒呼气六秒，做几轮让身体松下来。"}]
        reply = "把明天要做的事列个清单，划掉一件就轻一点，别把所有事都压在脑子里，睡前也可以听点轻音乐。"
        assert check_reply_quality(reply, history) is True

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


class TestRetryHintBuilder:
    def test_no_violation_returns_plain_hint(self):
        """无复读、无同类做法时返回纯 RETRY_HINT（闭合问句等由通用提示覆盖）。"""
        history = [{"role": "assistant", "content": "听起来确实不容易。"}]
        assert build_retry_hint("这样说你能理解对吧？", history) == RETRY_HINT

    def test_verbatim_overlap_names_sentence(self):
        history = [
            {"role": "assistant", "content": "今晚可以试试把憋着的话写在纸上，写完撕掉也没关系。"}
        ]
        reply = "反复被嘲讽心里肯定很堵。今晚可以试试把憋着的话写在纸上，写完撕掉也没关系。"
        hint = build_retry_hint(reply, history)
        assert hint != RETRY_HINT
        # 点名复读原句（归一化后去除标点的连续片段）
        assert "写在纸上" in hint

    def test_same_category_ban_names_label(self):
        history = [{"role": "assistant", "content": "可以试试腹式呼吸，吸气四秒呼气六秒。"}]
        reply = "也可以试试把注意力放在呼吸上，慢慢把节奏放下来会好一些的。"
        hint = build_retry_hint(reply, history)
        assert "呼吸调节" in hint and "禁止再提这类做法" in hint

    def test_overlap_empty_when_no_repeat(self):
        history = [{"role": "assistant", "content": "听起来确实不容易，愿意多说说吗？"}]
        assert _find_verbatim_overlap("完全不同的新表述，没有任何历史重叠内容。", history) == ""

    def test_overlap_finds_longest_extension(self):
        history = [{"role": "assistant", "content": "我们一起来试试把担心的事写在纸上。"}]
        reply = "所以，我们一起来试试把担心的事写在纸上，好吗？"
        overlap = _find_verbatim_overlap(reply, history)
        assert "我们一起来试试把担心的事写在纸上" in overlap

    def test_overlap_ignores_user_history(self):
        """逐字复读只针对 assistant 历史，用户原话被复述不算复读。"""
        history = [{"role": "user", "content": "我最近压力大到晚上翻来覆去睡不着觉"}]
        assert _find_verbatim_overlap("你提到最近压力大到晚上翻来覆去睡不着觉。", history) == ""


class TestRefreshRagSources:
    @pytest.mark.asyncio
    async def test_excludes_cited_chunks_and_takes_three(self):
        hits = [{"chunk_id": i, "text": f"t{i}", "source": "s"} for i in range(1, 6)]
        with patch(PATCH_RAG) as rag:
            rag.search = AsyncMock(return_value=hits)
            out = await _refresh_rag_sources(_state(), [{"chunk_id": 1}, {"chunk_id": 3}])
            assert [c["chunk_id"] for c in out] == [2, 4, 5]
            assert rag.search.await_args.kwargs["top_k"] == 6

    @pytest.mark.asyncio
    async def test_search_failure_returns_empty(self):
        with patch(PATCH_RAG) as rag:
            rag.search = AsyncMock(side_effect=RuntimeError("chroma down"))
            assert await _refresh_rag_sources(_state(), []) == []


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
            # max_retries=3 → 最多 4 次调用；全部不合格则保留首次回复
            p.chat = AsyncMock(side_effect=["你说的对吧？", "还是对吧？", "仍然对吧？", "就是对吧？"])
            rag.search = AsyncMock(return_value=[])
            out = await intervention_node(_state())
            assert out["final_reply"] == "你说的对吧？"
            assert p.chat.await_count == 4
            # 阶梯耗尽：hint → rag_refresh → context_trim（rag_refresh/context_trim 共享一次换片检索）
            assert out["node_decisions"]["intervention"]["llm"]["retry_modes"] == [
                "hint", "rag_refresh", "context_trim"
            ]
            assert rag.search.await_count == 2

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

    @pytest.mark.asyncio
    async def test_retry_ladder_rag_refresh_on_second_attempt(self):
        """重试阶梯：第 1 次重试（同 prompt+禁令）仍不合格 → 第 2 次换 RAG 片段重拼。"""
        good = "听起来你这段时间确实过得很熬人，考试压力加上周围人的期待一定让你紧绷，愿意说说最让你紧绷的是哪部分？"
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.chat = AsyncMock(side_effect=["你说的对吧？", "还是对吧？", good])
            rag.search = AsyncMock(side_effect=[
                [{"chunk_id": 1, "text": "旧片段", "source": "旧文件"}],
                [
                    {"chunk_id": 1, "text": "旧片段", "source": "旧文件"},
                    {"chunk_id": 2, "text": "新片段", "source": "新文件"},
                ],
            ])
            out = await intervention_node(_state())
            assert out["final_reply"] == good
            assert p.chat.await_count == 3
            assert rag.search.await_count == 2
            assert rag.search.await_args_list[1].kwargs["top_k"] == 6
            assert out["node_decisions"]["intervention"]["llm"]["retry_modes"] == ["hint", "rag_refresh"]
            # 第 2 次重试的 prompt 换用新片段（排除首轮已引用的 chunk 1）
            retry2_messages = p.chat.await_args_list[2].kwargs["messages"]
            assert any("新片段" in m.get("content", "") for m in retry2_messages)
            assert not any("旧片段" in m.get("content", "") for m in retry2_messages)


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
            # max_retries=3 → 最多 4 次调用；全部不合格沿用首次回复
            p.stream = _stream_mock(["你说的", "对吧？"], ["还是", "对吧？"], ["仍然", "对吧？"], ["就是", "对吧？"])
            rag.search = AsyncMock(return_value=[])
            tokens = [t async for t in stream_intervention(_state())]
            assert "".join(tokens) == "你说的对吧？"
            assert p.stream.call_count == 4

    @pytest.mark.asyncio
    async def test_ladder_context_trim_drops_assistant_history(self):
        """终极重试 context_trim：prompt 剔除 assistant 历史回复（模型无从照抄），
        质检仍按真实 history 校验（不复读的合格回复被采用）。"""
        history = [
            {"role": "user", "content": "我压力很大"},
            {"role": "assistant", "content": "我听到你最近考试压力很大，一定很累。"},
        ]
        good = "听起来你这段时间确实过得很熬人，考试压力加上周围人的期待一定让你紧绷，愿意说说最让你紧绷的是哪部分？"
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.chat = AsyncMock(side_effect=["你说的对吧？", "还是对吧？", "仍然对吧？", good])
            rag.search = AsyncMock(side_effect=[
                [{"chunk_id": 1, "text": "旧片段", "source": "旧文件"}],
                [{"chunk_id": 2, "text": "新片段", "source": "新文件"}],
            ])
            out = await intervention_node(_state(history=history))
            assert out["final_reply"] == good
            assert p.chat.await_count == 4
            assert out["node_decisions"]["intervention"]["llm"]["retry_modes"] == [
                "hint", "rag_refresh", "context_trim"
            ]
            # 终极重试的 prompt：不含 assistant 历史回复，仍含用户消息与换片后的 RAG
            retry3_messages = p.chat.await_args_list[3].kwargs["messages"]
            assert not any(m.get("role") == "assistant" for m in retry3_messages)
            assert any("我压力很大" in m.get("content", "") for m in retry3_messages)
            assert any("新片段" in m.get("content", "") for m in retry3_messages)

    @pytest.mark.asyncio
    async def test_empty_stream_yields_fallback(self):
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.stream = _stream_mock([])
            rag.search = AsyncMock(return_value=[])
            tokens = [t async for t in stream_intervention(_state())]
            assert "".join(tokens) == FALLBACK_REPLY

    @pytest.mark.asyncio
    async def test_retry_ladder_rag_refresh_on_second_attempt(self):
        """流式同口径：第 1 次重试仍不合格 → 第 2 次换 RAG 片段重拼 prompt。"""
        good = "听起来你这段时间确实过得很熬人，考试压力加上周围人的期待一定让你紧绷，愿意说说最让你紧绷的是哪部分？"
        with patch(PATCH_PROVIDER) as p, patch(PATCH_RAG) as rag:
            p.stream = _stream_mock(["你说的", "对吧？"], ["还是", "对吧？"], [good])
            rag.search = AsyncMock(side_effect=[
                [{"chunk_id": 1, "text": "旧片段", "source": "旧文件"}],
                [{"chunk_id": 2, "text": "新片段", "source": "新文件"}],
            ])
            tokens = [t async for t in stream_intervention(_state())]
            assert "".join(tokens) == good
            assert p.stream.call_count == 3
            assert rag.search.await_count == 2
            retry2_messages = p.stream.call_args_list[2].kwargs["messages"]
            assert any("新片段" in m.get("content", "") for m in retry2_messages)
            assert not any("旧片段" in m.get("content", "") for m in retry2_messages)

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
