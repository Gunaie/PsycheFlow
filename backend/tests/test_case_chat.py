"""病例解读 + 对话增强 的单测。

覆盖：
- case_parser：文本清洗/截断、扫描件无文本层异常
- analyze_case：危机前置零 LLM、正常解读走 role=report、空回复兜底
- POST /api/chat/case-upload：粘贴文本/危机/无附件/非 PDF/扫描件/短文本
- GET /api/chat/history：对话轮次回填 + 404
- POST /api/chat：message 超长 422、history 截断 20 条
- assessment 节点：登录用户按 account_id 跨 session 回退查到测评
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 合格长度的干预回复占位（质检要求 50-100 字，避免触发 min_len 重试）
_OK_REPLY = "我听到了你说的这些，最近考试的压力确实很大，让人有些喘不过气来，你愿意现在多和我说说你最担心的是哪部分吗"

from app.agents.nodes.assessment import assessment_node
from app.agents.nodes.case import CASE_FALLBACK_REPLY, analyze_case
from app.core.case_parser import (
    NoTextLayerError,
    clean_text,
    extract_pdf_text,
    truncate_case_text,
)

# ============ case_parser 纯函数 ============

class TestCaseParser:
    def test_clean_text_collapses_blank_lines(self):
        raw = "诊断证明\n\n\n\n抑郁发作\n  多次   空格"
        out = clean_text(raw)
        assert "\n\n\n" not in out
        assert "多次 空格" in out

    def test_truncate_marks_overflow(self):
        text = "字" * 7000
        out, truncated = truncate_case_text(text)
        assert truncated is True
        assert len(out) <= 6000 + 20
        assert "省略" in out

    def test_truncate_short_text_untouched(self):
        out, truncated = truncate_case_text("短文本")
        assert truncated is False
        assert out == "短文本"

    def test_extract_pdf_scanned_raises_no_text_layer(self):
        """扫描件（页面抽不出文字）→ NoTextLayerError。"""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        fake_pypdf = MagicMock(PdfReader=MagicMock(return_value=mock_reader))
        with patch.dict(sys.modules, {"pypdf": fake_pypdf}):
            with pytest.raises(NoTextLayerError):
                extract_pdf_text(b"%PDF-fake")

    def test_extract_pdf_normal_returns_text(self):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = (
            "诊断证明书\n\n患者因情绪低落、兴趣减退就诊，诊断：抑郁发作 F32.1，"
            "建议门诊规律随诊，必要时心理治疗，注意休息，按时服药。"
        )
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        fake_pypdf = MagicMock(PdfReader=MagicMock(return_value=mock_reader))
        with patch.dict(sys.modules, {"pypdf": fake_pypdf}):
            out = extract_pdf_text(b"%PDF-fake")
        assert "抑郁发作" in out


# ============ analyze_case 节点 ============

class TestAnalyzeCase:
    @pytest.mark.asyncio
    async def test_crisis_text_skips_llm(self):
        """含危机关键词 → 硬编码话术 + 零 LLM + 零 RAG。"""
        with patch("app.agents.nodes.case.provider") as p, \
             patch("app.agents.nodes.case.rag_service") as rag, \
             patch("app.agents.nodes.escalation.write_crisis_audit", return_value="/tmp/c.json"):
            p.chat = AsyncMock(return_value="不该被调用")
            rag.search = AsyncMock(return_value=[])
            result = await analyze_case("我真的不想活了，想结束生命", session_id="s1", account_id="a1")
        assert result["crisis"] is True
        assert "12355" in result["reply"]
        assert result["sources"] == []
        p.chat.assert_not_awaited()
        rag.search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_normal_text_calls_report_role_with_rag(self):
        with patch("app.agents.nodes.case.provider") as p, \
             patch("app.agents.nodes.case.rag_service") as rag:
            p.chat = AsyncMock(return_value="一、这是诊断证明：……")
            rag.search = AsyncMock(return_value=[
                {"text": "抑郁发作是心境障碍", "source": "psych_case_glossary.md", "chunk_id": 0},
            ])
            result = await analyze_case("诊断：抑郁发作 F32.1，建议门诊随诊，规律服药。")
        assert result["crisis"] is False
        assert result["reply"].startswith("一、")
        assert result["sources"][0]["source"] == "psych_case_glossary.md"
        p.chat.assert_awaited_once()
        assert p.chat.call_args.kwargs["role"] == "report"

    @pytest.mark.asyncio
    async def test_empty_llm_reply_triggers_fallback(self):
        with patch("app.agents.nodes.case.provider") as p, \
             patch("app.agents.nodes.case.rag_service") as rag:
            p.chat = AsyncMock(return_value="   ")
            rag.search = AsyncMock(return_value=[])
            result = await analyze_case("诊断：焦虑状态")
        assert result["crisis"] is False
        assert result["reply"] == CASE_FALLBACK_REPLY
        assert "12355" in result["reply"]


# ============ HTTP 端点 ============

def _patch_case_deps(reply: str = "一、这是门诊病历，记录了……"):
    """patch case 节点的 provider/rag，返回 (context_manager, provider_mock)。

    注意 patch.multiple 显式传入的 mock 不进其 as 变量，故由调用方持有返回的 mock。
    """
    provider_mock = MagicMock(chat=AsyncMock(return_value=reply))
    rag_mock = MagicMock(search=AsyncMock(return_value=[]))
    cm = patch.multiple(
        "app.agents.nodes.case",
        provider=provider_mock,
        rag_service=rag_mock,
    )
    return cm, provider_mock


class TestCaseUploadEndpoint:
    def test_pasted_text_success(self, client):
        cm, _ = _patch_case_deps()
        with cm, patch("app.agents.nodes.escalation.write_crisis_audit", return_value="/tmp/c.json"):
            r = client.post(
                "/api/chat/case-upload",
                data={"text": "诊断：焦虑状态，建议门诊随诊、规律作息。" * 3, "session_id": "case-sid-1"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["crisis"] is False
        assert data["attachment"]["kind"] == "text"
        assert "门诊病历" in data["reply"]

    def test_crisis_text_returns_hotline(self, client):
        cm, provider_mock = _patch_case_deps()
        with cm, patch("app.agents.nodes.escalation.write_crisis_audit", return_value="/tmp/c.json"):
            r = client.post(
                "/api/chat/case-upload",
                data={"text": "我不想活了，我想自杀结束生命，谁也别拦我", "session_id": "case-sid-2"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["crisis"] is True
        assert "12355" in data["reply"]
        provider_mock.chat.assert_not_awaited()

    def test_no_file_no_text_returns_400(self, client):
        r = client.post("/api/chat/case-upload", data={"session_id": "case-sid-3"})
        assert r.status_code == 400

    def test_non_pdf_file_returns_400(self, client):
        r = client.post(
            "/api/chat/case-upload",
            files={"file": ("note.txt", b"hello world", "text/plain")},
        )
        assert r.status_code == 400
        assert "PDF" in r.json()["detail"]

    def test_scanned_pdf_returns_friendly_400(self, client):
        with patch(
            "app.api.chat.extract_pdf_text",
            side_effect=NoTextLayerError("该 PDF 是扫描件/图片版"),
        ):
            r = client.post(
                "/api/chat/case-upload",
                files={"file": ("scan.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
        assert r.status_code == 400
        assert "扫描件" in r.json()["detail"]

    def test_too_short_text_returns_400(self, client):
        r = client.post("/api/chat/case-upload", data={"text": "太短"})
        assert r.status_code == 400


class TestChatHistoryEndpoint:
    def test_history_roundtrip(self, client):
        # 真实流程：前端先 POST /api/sessions 创建会话，再在会话内对话
        sid = client.post("/api/sessions", json={"label": "对话"}).json()["session_id"]
        p_intv = patch.multiple(
            "app.agents.nodes.intervention",
            provider=MagicMock(chat=AsyncMock(return_value=_OK_REPLY)),
            rag_service=MagicMock(search=AsyncMock(return_value=[])),
        )
        with p_intv:
            r = client.post("/api/chat", json={"message": "我最近压力大", "session_id": sid})
            assert r.status_code == 200
        r2 = client.get("/api/chat/history", params={"session_id": sid})
        assert r2.status_code == 200
        items = r2.json()["items"]
        assert len(items) == 2
        assert items[0]["role"] == "user"
        assert items[1]["role"] == "assistant"
        assert items[1]["content"] == _OK_REPLY

    def test_history_unknown_session_404(self, client):
        r = client.get("/api/chat/history", params={"session_id": "no-such-session"})
        assert r.status_code == 404

    def test_delete_history_roundtrip(self, client):
        sid = client.post("/api/sessions", json={"label": "对话"}).json()["session_id"]
        p_intv = patch.multiple(
            "app.agents.nodes.intervention",
            provider=MagicMock(chat=AsyncMock(return_value=_OK_REPLY)),
            rag_service=MagicMock(search=AsyncMock(return_value=[])),
        )
        with p_intv:
            r = client.post("/api/chat", json={"message": "我最近压力大", "session_id": sid})
            assert r.status_code == 200

        r2 = client.delete("/api/chat/history", params={"session_id": sid})
        assert r2.status_code == 200
        assert r2.json() == {"session_id": sid, "deleted": 2}

        r3 = client.get("/api/chat/history", params={"session_id": sid})
        assert r3.status_code == 200
        assert r3.json()["items"] == []

    def test_delete_history_unknown_session_404(self, client):
        r = client.delete("/api/chat/history", params={"session_id": "no-such-session"})
        assert r.status_code == 404


class TestChatGuardrails:
    def test_message_over_limit_422(self, client):
        r = client.post("/api/chat", json={"message": "压" * 2001})
        assert r.status_code == 422

    def test_history_clipped_to_recent_turns(self, client):
        """上送 25 条 history → 最近 RECENT_TURNS*2 条保留原文 + 摘要（system+summary+recent+user）。

        P1 滑动窗口 + 语义摘要替代硬截断 10 轮（20 条）：
        - RECENT_TURNS=4 → 保留最近 8 条原文
        - 更早的 17 条压缩为摘要（mock 后摘要为空，但 system 消息槽位存在）
        """
        with patch("app.agents.nodes.intervention.provider") as ip, \
             patch("app.agents.nodes.intervention.rag_service") as rag:
            ip.chat = AsyncMock(return_value=_OK_REPLY)
            rag.search = AsyncMock(return_value=[])
            history = [
                {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                for i in range(25)
            ]
            r = client.post("/api/chat", json={"message": "继续", "history": history, "session_id": "clip-sid"})
            assert r.status_code == 200
            messages = ip.chat.call_args.kwargs["messages"]
            # system + 摘要(可能为空时跳过) + 最近 8 条原文 + 当前 user
            # 摘要为空时 messages = system + 8 + 1 = 10；摘要非空时 = system + 1 + 8 + 1 = 11
            assert 10 <= len(messages) <= 11
            # 最旧保留 m17（25 条 - 最近 8 条 = 17 条进压缩/摘要）
            # 摘要内容以 "【前文摘要" 开头（_compress_history 实际生成内容）
            second = messages[1]["content"]
            assert second == "m17" or second.startswith("【前文摘要（保留核心情绪与建议）】")
            assert messages[-2]["content"] == "m24"  # 最新保留到第 24 条


class TestAssessmentAccountFallback:
    @pytest.mark.asyncio
    async def test_fallback_by_account_id_finds_record(self):
        """chat session 查不到 → 按 account_id 跨 session 回退查到最近测评。"""
        fake_record = MagicMock(
            scale_id="phq_a", scale_name="PHQ-A", severity="moderate",
            crisis_level="none", total_score=15, crisis_triggers=[],
        )
        fake_result = MagicMock()
        # 第 1 次查询（按 sid）无记录；第 2 次（按 account 回退）命中
        fake_result.scalar_one_or_none = MagicMock(side_effect=[None, fake_record])
        fake_db = MagicMock()
        fake_db.execute.return_value = fake_result

        with patch("app.agents.nodes.assessment.SessionLocal", return_value=fake_db):
            out = await assessment_node({
                "session_id": "chat-sid-x",
                "account_id": "acc-1",
                "agent_trace": [],
            })

        assert out["has_assessment"] is True
        assert out["assessment_context"]["scale_id"] == "phq_a"
        assert out["assessment_context"]["severity"] == "moderate"
        assert fake_db.execute.call_count == 2
        fake_db.close.assert_called_once()
