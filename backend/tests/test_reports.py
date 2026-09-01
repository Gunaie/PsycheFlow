"""报告服务单测：HTML 渲染危机框 + WeasyPrint 实出 PDF（mock LLM）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.reports.service import generate_report_pdf, render_report_html


def _session(label="测试"):
    return SimpleNamespace(id="abc123def456", label=label)


def _assessment(**kw):
    base = {
        "scale_id": "phq_a",
        "scale_name": "PHQ-A 青少年抑郁筛查量表",
        "total_score": 5,
        "severity": "mild",
        "crisis_level": "safe",
        "crisis_triggers": [],
        "interpretation": "存在轻度抑郁症状。",
        "needs_crisis_escalation": False,
    }
    base.update(kw)
    return base


class TestRenderHtml:
    def test_crisis_shows_hotline_and_box(self):
        a = _assessment(
            crisis_level="elevated",
            needs_crisis_escalation=True,
            crisis_triggers=["第9题 自杀意念 得分 1"],
            total_score=1,
        )
        html = render_report_html(_session(), [a], "建议段")
        assert "12355" in html
        assert "需要立即寻求帮助" in html
        assert "第9题" in html

    def test_no_crisis_no_box(self):
        html = render_report_html(_session(), [_assessment()], "建议段")
        assert "需要立即寻求帮助" not in html
        assert "12355" not in html

    def test_narrative_and_sections_present(self):
        html = render_report_html(_session(), [_assessment()], "这是建议文本内容")
        assert "这是建议文本内容" in html
        # MHT 风格章节：发展建议、测评结果、6 大章节全在 + 子维度 PHQ-A 4 项
        assert "发展建议" in html
        assert "测评结果" in html
        assert "1. 测评工具介绍" in html
        assert "2. 测评结果解读注意事项" in html
        assert "3. 测评人员信息" in html
        assert "4. 测评结果" in html
        assert "5. 测评结果剖析" in html
        assert "6. 发展建议" in html
        assert "因子剖析" in html
        assert "认知/情感症状" in html
        assert "PHQ-A" in html
        # 综合等级横幅存在
        assert "综合水平被评定为" in html

    def test_severity_label_and_color(self):
        html = render_report_html(_session(), [_assessment(severity="severe")], "")
        assert "重度" in html


class TestGeneratePdf:
    async def test_calls_llm_report_role_and_returns_bytes(self):
        with patch("app.reports.service.provider") as mock_p:
            mock_p.chat = AsyncMock(return_value="建议：规律作息，找人倾诉。")
            pdf = await generate_report_pdf(_session(), [_assessment()])
            assert isinstance(pdf, (bytes, bytearray))
            assert len(pdf) > 1000
            mock_p.chat.assert_awaited_once()
            assert mock_p.chat.call_args.kwargs["role"] == "report"

    async def test_llm_failure_still_renders_pdf(self):
        with patch("app.reports.service.provider") as mock_p:
            mock_p.chat = AsyncMock(side_effect=RuntimeError("llm down"))
            pdf = await generate_report_pdf(_session(), [_assessment()])
            assert len(pdf) > 1000  # 兜底叙事，PDF 仍生成

    async def test_crisis_pdf_renders(self):
        with patch("app.reports.service.provider") as mock_p:
            mock_p.chat = AsyncMock(return_value="请尽快寻求专业帮助。")
            a = _assessment(
                crisis_level="elevated", needs_crisis_escalation=True,
                crisis_triggers=["第9题 自杀意念 得分 1"],
            )
            pdf = await generate_report_pdf(_session(), [a])
            assert len(pdf) > 1000
