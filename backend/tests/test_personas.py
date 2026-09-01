"""多角色人格（D2 四期）单测。

覆盖：
- personas 注册表：4 人格齐全、安全底线共享、未知 id 回退 default
- GET /api/personas 端点
- POST /api/chat 透传 persona_id → intervention system prompt 变化
- 不传 persona_id → 默认人格（行为向后兼容）
- 危机消息 + persona_id → 危机升级硬编码不变（零 LLM，人格不影响安全链路）
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.agents.personas import (
    DEFAULT_PERSONA_ID,
    PERSONAS,
    build_system_prompt,
    get_persona,
    list_personas,
)
from app.main import app

client = TestClient(app)

PATCH_TRIAGE_PROVIDER = "app.agents.nodes.triage.provider"
PATCH_INTV_PROVIDER = "app.agents.nodes.intervention.provider"
PATCH_INTV_RAG = "app.agents.nodes.intervention.rag_service"


def _patch_chat_graph():
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


class TestPersonaRegistry:
    def test_four_personas_registered(self):
        assert set(PERSONAS) == {"default", "sister", "senior", "listener"}
        assert DEFAULT_PERSONA_ID == "default"

    def test_all_personas_share_safety_baseline(self):
        """切换人格绝不削弱安全底线：每个 system prompt 都含 7 条硬规则关键词。"""
        for p in PERSONAS.values():
            prompt = build_system_prompt(p)
            assert "共情优先" in prompt
            assert "不替代专业诊疗" in prompt
            assert "12355" in prompt
            assert "来源透明" in prompt
            assert p.style_prompt in prompt

    def test_unknown_persona_falls_back_to_default(self):
        assert get_persona("nonexistent").persona_id == "default"
        assert get_persona("").persona_id == "default"
        assert get_persona(None).persona_id == "default"

    def test_persona_prompts_are_distinct(self):
        prompts = {pid: build_system_prompt(p) for pid, p in PERSONAS.items()}
        assert len(set(prompts.values())) == 4

    def test_list_personas_shape(self):
        items = list_personas()
        assert len(items) == 4
        for item in items:
            assert set(item) == {"persona_id", "name", "avatar", "description"}


class TestPersonasEndpoint:
    def test_get_personas(self):
        r = client.get("/api/personas")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 4
        ids = {p["persona_id"] for p in data}
        assert ids == {"default", "sister", "senior", "listener"}
        # 不泄露 prompt 原文
        for p in data:
            assert "style_prompt" not in p


class TestChatPersona:
    def _mock_normal_graph(self, m):
        m.triage_provider.chat = AsyncMock(return_value="倾诉")
        m.rag_service.search = AsyncMock(return_value=[])
        m.intv_provider.chat = AsyncMock(return_value="我在听你说。")

    def test_persona_id_reaches_intervention_system_prompt(self):
        with _patch_chat_graph() as m:
            self._mock_normal_graph(m)
            r = client.post("/api/chat", json={
                "message": "最近压力好大",
                "persona_id": "sister",
            })
            assert r.status_code == 200
            data = r.json()
            # 回传 canonical persona_id
            assert data["persona_id"] == "sister"
            # system prompt = 安全底线 + 安安姐姐人格
            messages = m.intv_provider.chat.call_args.kwargs["messages"]
            system = messages[0]["content"]
            assert "安安姐姐" in system
            assert "共情优先" in system

    def test_default_persona_without_field(self):
        """不传 persona_id → 默认人格，向后兼容旧行为。"""
        with _patch_chat_graph() as m:
            self._mock_normal_graph(m)
            r = client.post("/api/chat", json={"message": "嗨"})
            assert r.status_code == 200
            data = r.json()
            assert data["persona_id"] == "default"
            messages = m.intv_provider.chat.call_args.kwargs["messages"]
            assert "暖暖" in messages[0]["content"]

    def test_unknown_persona_falls_back_to_default(self):
        with _patch_chat_graph() as m:
            self._mock_normal_graph(m)
            r = client.post("/api/chat", json={
                "message": "嗨",
                "persona_id": "hacker_persona",
            })
            assert r.status_code == 200
            assert r.json()["persona_id"] == "default"
            messages = m.intv_provider.chat.call_args.kwargs["messages"]
            assert "暖暖" in messages[0]["content"]

    def test_crisis_ignores_persona(self):
        """危机消息即使带 persona_id 仍走硬编码升级，零 LLM/RAG，回复含 12355。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="should_not_be_called")
            m.intv_provider.chat = AsyncMock(return_value="should_not_be_called")
            m.rag_service.search = AsyncMock(return_value=[])

            r = client.post("/api/chat", json={
                "message": "我不想活了",
                "persona_id": "sister",
            })
            assert r.status_code == 200
            data = r.json()
            assert data["crisis"] is True
            assert "12355" in data["reply"]
            m.intv_provider.chat.assert_not_awaited()
            m.rag_service.search.assert_not_awaited()
