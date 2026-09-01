"""会话与报告 API 单测（内存 DB + mock LLM）。"""
from unittest.mock import AsyncMock, patch


def _phq_answers(item9=0):
    a = {str(i): 0 for i in range(1, 10)}
    a["9"] = item9
    return a


class TestCreateSession:
    def test_create_returns_id(self, client):
        r = client.post("/api/sessions", json={"label": "小明"})
        assert r.status_code == 200
        d = r.json()
        assert len(d["session_id"]) == 32
        assert d["label"] == "小明"


class TestSubmitAssessment:
    def test_submit_persists_and_scores(self, client):
        sid = client.post("/api/sessions", json={}).json()["session_id"]
        r = client.post(
            f"/api/sessions/{sid}/assessments",
            json={"scale_id": "phq_a", "answers": _phq_answers()},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["total_score"] == 0
        assert d["severity"] == "none"
        assert d["assessment_id"]

    def test_submit_crisis_item(self, client):
        sid = client.post("/api/sessions", json={}).json()["session_id"]
        r = client.post(
            f"/api/sessions/{sid}/assessments",
            json={"scale_id": "phq_a", "answers": _phq_answers(item9=1)},
        )
        d = r.json()
        assert d["crisis_level"] == "elevated"
        assert d["needs_crisis_escalation"] is True

    def test_get_session_lists_assessments(self, client):
        sid = client.post("/api/sessions", json={"label": "x"}).json()["session_id"]
        client.post(
            f"/api/sessions/{sid}/assessments",
            json={"scale_id": "phq_a", "answers": _phq_answers()},
        )
        r = client.get(f"/api/sessions/{sid}")
        assert r.status_code == 200
        d = r.json()
        assert d["session_id"] == sid
        assert len(d["assessments"]) == 1
        assert d["assessments"][0]["needs_crisis_escalation"] is False

    def test_missing_item_422(self, client):
        sid = client.post("/api/sessions", json={}).json()["session_id"]
        a = {str(i): 0 for i in range(1, 9)}  # 缺第 9 题
        r = client.post(
            f"/api/sessions/{sid}/assessments",
            json={"scale_id": "phq_a", "answers": a},
        )
        assert r.status_code == 422

    def test_unknown_session_404(self, client):
        r = client.post(
            "/api/sessions/nope/assessments",
            json={"scale_id": "phq_a", "answers": {}},
        )
        assert r.status_code == 404


class TestReport:
    def test_report_returns_pdf_and_calls_llm(self, client):
        with patch("app.reports.service.provider") as mock_p:
            mock_p.chat = AsyncMock(return_value="建议：规律作息，找人倾诉。")
            sid = client.post("/api/sessions", json={"label": "测试"}).json()["session_id"]
            client.post(
                f"/api/sessions/{sid}/assessments",
                json={"scale_id": "phq_a", "answers": _phq_answers()},
            )
            r = client.post(f"/api/sessions/{sid}/report")
            assert r.status_code == 200
            assert r.headers["content-type"] == "application/pdf"
            assert len(r.content) > 1000
            mock_p.chat.assert_awaited_once()
            assert mock_p.chat.call_args.kwargs["role"] == "report"

    def test_report_crisis_session_still_pdf(self, client):
        with patch("app.reports.service.provider") as mock_p:
            mock_p.chat = AsyncMock(return_value="请尽快寻求帮助。")
            sid = client.post("/api/sessions", json={}).json()["session_id"]
            client.post(
                f"/api/sessions/{sid}/assessments",
                json={"scale_id": "phq_a", "answers": _phq_answers(item9=1)},
            )
            r = client.post(f"/api/sessions/{sid}/report")
            assert r.status_code == 200
            assert len(r.content) > 1000

    def test_report_empty_session_400(self, client):
        sid = client.post("/api/sessions", json={}).json()["session_id"]
        r = client.post(f"/api/sessions/{sid}/report")
        assert r.status_code == 400
