"""量表 API 端点单测（FastAPI TestClient，纯计分不触 LLM/向量库）。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestListScales:
    def test_returns_scales_with_item_count(self):
        r = client.get("/api/scales")
        assert r.status_code == 200
        data = r.json()
        ids = {s["scale_id"] for s in data}
        assert "phq_a" in ids
        assert "scared" in ids
        for s in data:
            assert s["item_count"] > 0


class TestGetScale:
    def test_phq_a_meta_has_items_and_options(self):
        r = client.get("/api/scales/phq_a")
        assert r.status_code == 200
        data = r.json()
        assert data["scale_id"] == "phq_a"
        assert len(data["items"]) == 9
        # JSON 序列化后 dict 键变字符串
        assert "0" in data["options"]
        assert "3" in data["options"]

    def test_scared_meta_has_41_items(self):
        r = client.get("/api/scales/scared")
        assert r.status_code == 200
        assert len(r.json()["items"]) == 41

    def test_unknown_returns_404(self):
        r = client.get("/api/scales/nope")
        assert r.status_code == 404


class TestScore:
    def _all_zero_phq(self):
        return {str(i): 0 for i in range(1, 10)}

    def test_phq_a_all_zero_no_crisis(self):
        r = client.post("/api/scales/phq_a/score", json={"answers": self._all_zero_phq()})
        assert r.status_code == 200
        data = r.json()
        assert data["total_score"] == 0
        assert data["severity"] == "none"
        assert data["crisis_level"] == "safe"
        assert data["needs_crisis_escalation"] is False

    def test_phq_a_crisis_when_item9_positive(self):
        answers = self._all_zero_phq()
        answers["9"] = 1
        r = client.post("/api/scales/phq_a/score", json={"answers": answers})
        assert r.status_code == 200
        data = r.json()
        assert data["crisis_level"] == "elevated"
        assert data["needs_crisis_escalation"] is True
        assert data["crisis_triggers"]  # 非空

    def test_missing_item_returns_422(self):
        answers = {str(i): 0 for i in range(1, 9)}  # 缺第 9 题
        r = client.post("/api/scales/phq_a/score", json={"answers": answers})
        assert r.status_code == 422

    def test_invalid_value_returns_422(self):
        answers = self._all_zero_phq()
        answers["1"] = 5  # 超出 0-3
        r = client.post("/api/scales/phq_a/score", json={"answers": answers})
        assert r.status_code == 422

    def test_unknown_scale_returns_404(self):
        r = client.post("/api/scales/nope/score", json={"answers": {}})
        assert r.status_code == 404
