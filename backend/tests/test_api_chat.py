"""对话 API 端点单测：mock provider 与 rag_service，验证危机短路与正常流程。"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestChatCrisis:
    def test_crisis_keyword_short_circuits_llm_and_rag(self):
        with patch("app.api.chat.provider") as mock_provider, \
             patch("app.api.chat.rag_service") as mock_rag:
            mock_provider.chat = AsyncMock()
            mock_rag.search = AsyncMock()
            r = client.post("/api/chat", json={"message": "我想自杀"})

            assert r.status_code == 200
            data = r.json()
            assert data["crisis"] is True
            assert "12355" in data["reply"]
            assert data["sources"] == []
            mock_provider.chat.assert_not_awaited()
            mock_rag.search.assert_not_awaited()


class TestChatNormal:
    def test_normal_calls_rag_then_llm_dialog(self):
        with patch("app.api.chat.provider") as mock_provider, \
             patch("app.api.chat.rag_service") as mock_rag:
            mock_rag.search = AsyncMock(return_value=[
                {"text": "深呼吸放松", "source": "04_放松技术.txt", "distance": 0.4},
            ])
            mock_provider.chat = AsyncMock(return_value="我理解你的压力，试试深呼吸。")

            r = client.post("/api/chat", json={"message": "最近考试压力大"})
            assert r.status_code == 200
            data = r.json()
            assert data["crisis"] is False
            assert data["reply"] == "我理解你的压力，试试深呼吸。"
            assert len(data["sources"]) == 1
            assert data["sources"][0]["source"] == "04_放松技术.txt"

            mock_rag.search.assert_awaited_once()
            mock_provider.chat.assert_awaited_once()
            assert mock_provider.chat.call_args.kwargs["role"] == "dialog"

    def test_rag_failure_degrades_to_plain_chat(self):
        with patch("app.api.chat.provider") as mock_provider, \
             patch("app.api.chat.rag_service") as mock_rag:
            mock_rag.search = AsyncMock(side_effect=RuntimeError("chroma down"))
            mock_provider.chat = AsyncMock(return_value="我在听你说。")

            r = client.post("/api/chat", json={"message": "我有点难过"})
            assert r.status_code == 200
            data = r.json()
            assert data["crisis"] is False
            assert data["sources"] == []
            mock_provider.chat.assert_awaited_once()

    def test_history_is_forwarded(self):
        with patch("app.api.chat.provider") as mock_provider, \
             patch("app.api.chat.rag_service") as mock_rag:
            mock_rag.search = AsyncMock(return_value=[])
            mock_provider.chat = AsyncMock(return_value="嗯。")

            r = client.post("/api/chat", json={
                "message": "继续",
                "history": [
                    {"role": "user", "content": "我难过"},
                    {"role": "assistant", "content": "我在听"},
                ],
            })
            assert r.status_code == 200
            messages = mock_provider.chat.call_args.kwargs["messages"]
            roles = [m["role"] for m in messages]
            assert roles == ["system", "user", "assistant", "user"]
