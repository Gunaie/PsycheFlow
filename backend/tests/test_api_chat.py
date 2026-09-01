"""对话 API 端点单测：mock triage/intervention 节点 provider 与 rag_service，验证危机短路与正常流程。

B 二期架构：chat.py 不再直接持有 provider/rag_service，所有 LLM 调用通过 LangGraph 节点触发：
- triage 节点：from app.core.llm import provider（意图分类 LLM）
- intervention 节点：from app.core.llm import provider + from app.rag.service import rag_service
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 统一 patch 路径
PATCH_TRIAGE_PROVIDER = "app.agents.nodes.triage.provider"
PATCH_INTV_PROVIDER = "app.agents.nodes.intervention.provider"
PATCH_INTV_RAG = "app.agents.nodes.intervention.rag_service"


def _patch_chat_graph():
    """统一返回 contextmanager：mock triage + intervention 节点的 provider/rag_service。

    用法：with _patch_chat_graph() as m: ...
    m.triage_provider / m.intv_provider / m.rag_service
    """
    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def _ctx():
        with patch(PATCH_TRIAGE_PROVIDER) as triage_p, \
             patch(PATCH_INTV_PROVIDER) as intv_p, \
             patch(PATCH_INTV_RAG) as rag:
            yield SimpleNamespace(
                triage_provider=triage_p,
                intv_provider=intv_p,
                rag_service=rag,
            )

    return _ctx()


class TestChatCrisis:
    def test_crisis_keyword_short_circuits_llm_and_rag(self):
        """危机消息 → triage 硬编码命中 → escalation，零 LLM 调用 + 零 RAG 调用。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="should_not_be_called")
            m.intv_provider.chat = AsyncMock(return_value="should_not_be_called")
            m.rag_service.search = AsyncMock(return_value=[])

            r = client.post("/api/chat", json={"message": "我想自杀"})

            assert r.status_code == 200
            data = r.json()
            assert data["crisis"] is True
            assert "12355" in data["reply"]
            assert data["sources"] == []
            # 危机短路：triage 节点未命中 LLM 分诊（detect_crisis_with_words 前置拦截）
            m.triage_provider.chat.assert_not_awaited()
            # escalation 节点不调 intervention 的 LLM/RAG
            m.intv_provider.chat.assert_not_awaited()
            m.rag_service.search.assert_not_awaited()


class TestChatNormal:
    def test_normal_calls_rag_then_llm_dialog(self):
        """正常消息 → triage 调 LLM 意图分类 → assessment → intervention 调 RAG + LLM dialog。"""
        with _patch_chat_graph() as m:
            # triage 意图分类 LLM 返回
            m.triage_provider.chat = AsyncMock(return_value="倾诉")
            # intervention RAG 检索返回
            m.rag_service.search = AsyncMock(return_value=[
                {"text": "深呼吸放松", "source": "04_放松技术.txt", "distance": 0.4},
            ])
            # intervention LLM 共情回应
            m.intv_provider.chat = AsyncMock(return_value="我理解你的压力，试试深呼吸。")

            r = client.post("/api/chat", json={"message": "最近考试压力大"})
            assert r.status_code == 200
            data = r.json()
            assert data["crisis"] is False
            assert data["reply"] == "我理解你的压力，试试深呼吸。"
            assert len(data["sources"]) == 1
            assert data["sources"][0]["source"] == "04_放松技术.txt"

            # RAG 被调用 1 次
            m.rag_service.search.assert_awaited_once()
            # intervention LLM 被调用 1 次，role=dialog（共情回应，不是分诊）
            m.intv_provider.chat.assert_awaited_once()
            assert m.intv_provider.chat.call_args.kwargs["role"] == "dialog"
            # triage LLM 也被调用 1 次，role=intake（意图分类）
            m.triage_provider.chat.assert_awaited_once()
            assert m.triage_provider.chat.call_args.kwargs["role"] == "intake"

    def test_rag_failure_degrades_to_plain_chat(self):
        """RAG 抛 RuntimeError → intervention 捕获，sources=[]，LLM 仍生成回复。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="倾诉")
            m.rag_service.search = AsyncMock(side_effect=RuntimeError("chroma down"))
            m.intv_provider.chat = AsyncMock(return_value="我在听你说。")

            r = client.post("/api/chat", json={"message": "我有点难过"})
            assert r.status_code == 200
            data = r.json()
            assert data["crisis"] is False
            assert data["sources"] == []
            # RAG 失败不阻断 LLM
            m.intv_provider.chat.assert_awaited_once()

    def test_history_is_forwarded(self):
        """history 通过 graph state 转发到 intervention 节点的 LLM messages。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="倾诉")
            m.rag_service.search = AsyncMock(return_value=[])
            m.intv_provider.chat = AsyncMock(return_value="嗯。")

            r = client.post("/api/chat", json={
                "message": "继续",
                "history": [
                    {"role": "user", "content": "我难过"},
                    {"role": "assistant", "content": "我在听"},
                ],
            })
            assert r.status_code == 200
            # intervention 节点的 messages 应包含 history（system, user, assistant, user）
            messages = m.intv_provider.chat.call_args.kwargs["messages"]
            roles = [m["role"] for m in messages]
            assert roles == ["system", "user", "assistant", "user"]
