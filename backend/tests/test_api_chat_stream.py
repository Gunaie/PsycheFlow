"""SSE 流式对话端点单测：mock triage/intervention 节点 provider 与 rag_service，
验证 SSE 事件序列（agent/sources/token/crisis/done）与首 token 路径。

NFR-5 首 token 优化：POST /api/chat/stream，非危机路径流式 yield token，
危机路径不流式推完整 crisis_message 后 close。
"""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.agents.nodes.intervention import FALLBACK_REPLY
from app.main import app

client = TestClient(app)

# 统一 patch 路径（与 test_api_chat.py 一致）
PATCH_TRIAGE_PROVIDER = "app.agents.nodes.triage.provider"
PATCH_INTV_PROVIDER = "app.agents.nodes.intervention.provider"
PATCH_INTV_RAG = "app.agents.nodes.intervention.rag_service"


def _patch_chat_graph():
    """统一返回 contextmanager：mock triage + intervention 节点的 provider/rag_service。"""

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


def _parse_sse(text: str) -> list[dict]:
    """解析 SSE 文本流为事件列表 [{event, data}]。"""
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        events.append({"event": event, "data": json.loads(data) if data else {}})
    return events


def _make_fake_stream(tokens: list[str]) -> MagicMock:
    """构造 async generator mock provider.stream，返回 MagicMock(side_effect=_fake)。

    side_effect 是 async generator function，mock 调用时返回 async generator；
    可用 assert_called_once/ assert_not_called 验证调用次数。
    """
    async def _fake(*args, **kwargs):
        for tok in tokens:
            yield tok

    return MagicMock(side_effect=_fake)


class TestChatStreamNormal:
    def test_stream_normal_yields_token_events(self):
        """正常消息 → SSE 事件序列：agent(triage/assessment/intervention) → sources? → token×3 → done。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="倾诉")
            m.rag_service.search = AsyncMock(return_value=[
                {"text": "深呼吸放松", "source": "04_放松技术.txt", "distance": 0.4},
            ])
            # mock provider.stream 为 async generator
            m.intv_provider.stream = _make_fake_stream(["我", "听到", "你"])

            r = client.post("/api/chat/stream", json={"message": "最近考试压力大"})

            assert r.status_code == 200
            events = _parse_sse(r.text)
            event_types = [e["event"] for e in events]
            # 序列：agent(triage) → agent(assessment) → agent(intervention) → sources → token×3 → done
            assert "agent" in event_types
            assert event_types.count("token") == 3
            assert event_types[-1] == "done"

            # done 事件 payload
            done = events[-1]["data"]
            assert done["reply"] == "我听到你"
            assert done["current_agent"] == "intervention"
            assert done["crisis"] is False
            assert done["persona_id"] == "default"

            # sources 事件提前推送（intervention 前）
            sources_evts = [e for e in events if e["event"] == "sources"]
            assert len(sources_evts) == 1
            assert sources_evts[0]["data"]["sources"][0]["source"] == "04_放松技术.txt"

            # token 拼接 = 完整回复
            tokens = [e["data"]["token"] for e in events if e["event"] == "token"]
            assert "".join(tokens) == "我听到你"

            # triage 调 LLM 分类 1 次（triage 角色配 qwen-plus 无思考链）
            m.triage_provider.chat.assert_awaited_once()
            assert m.triage_provider.chat.call_args.kwargs["role"] == "triage"
            # intervention 用 stream（非 chat），被调用 1 次
            m.intv_provider.stream.assert_called_once()
            # RAG 检索 1 次（build_intervention_messages 调 1 次，stream_intervention 复用 prebuilt）
            m.rag_service.search.assert_awaited_once()

    def test_stream_no_sources_when_rag_empty(self):
        """RAG 返回空 → 不推 sources 事件，token 仍流式。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="倾诉")
            m.rag_service.search = AsyncMock(return_value=[])
            m.intv_provider.stream = _make_fake_stream(["嗯", "。"])

            r = client.post("/api/chat/stream", json={"message": "我有点难过"})
            assert r.status_code == 200
            events = _parse_sse(r.text)
            # 无 sources 事件
            assert not any(e["event"] == "sources" for e in events)
            # 仍有 token + done
            assert any(e["event"] == "token" for e in events)
            assert events[-1]["event"] == "done"
            assert events[-1]["data"]["sources"] == []


class TestChatStreamCrisis:
    def test_stream_crisis_no_token_events(self):
        """危机消息 → SSE 事件序列：agent(triage) → crisis → done，零 token 事件。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="should_not_be_called")
            m.intv_provider.stream = _make_fake_stream(["should_not_be_called"])
            m.rag_service.search = AsyncMock(return_value=[])

            r = client.post("/api/chat/stream", json={"message": "我想自杀"})
            assert r.status_code == 200
            events = _parse_sse(r.text)
            event_types = [e["event"] for e in events]

            # 无 token 事件（危机不流式）
            assert "token" not in event_types
            # 有 crisis 事件
            crisis_evts = [e for e in events if e["event"] == "crisis"]
            assert len(crisis_evts) == 1
            assert "12355" in crisis_evts[0]["data"]["reply"]
            # done 事件 crisis=true
            done = events[-1]["data"]
            assert done["crisis"] is True
            assert done["current_agent"] == "escalation"

            # 危机短路：triage/intervention LLM 与 RAG 全部未被调用
            m.triage_provider.chat.assert_not_awaited()
            m.intv_provider.stream.assert_not_called()
            m.rag_service.search.assert_not_awaited()


class TestChatStreamFallback:
    def test_stream_empty_llm_reply_yields_fallback(self):
        """LLM 流式返回空 → stream_intervention yield FALLBACK_REPLY（含 12355）。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="倾诉")
            m.rag_service.search = AsyncMock(return_value=[])
            # provider.stream 返回空（模拟 deepseek 思考链吃光 max_tokens）
            m.intv_provider.stream = _make_fake_stream([])

            r = client.post("/api/chat/stream", json={"message": "我难过"})
            assert r.status_code == 200
            events = _parse_sse(r.text)

            # token 事件应包含 FALLBACK_REPLY
            tokens = [e["data"]["token"] for e in events if e["event"] == "token"]
            assert "".join(tokens) == FALLBACK_REPLY
            assert "12355" in "".join(tokens)
            # done 事件 reply = fallback
            assert events[-1]["data"]["reply"] == FALLBACK_REPLY

    def test_stream_llm_exception_yields_fallback(self):
        """provider.stream 抛异常 → stream_intervention 捕获并 yield FALLBACK_REPLY。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="倾诉")
            m.rag_service.search = AsyncMock(return_value=[])

            async def _exploding_stream(*args, **kwargs):
                raise RuntimeError("dashscope 500")
                yield  # 让它成为 async generator（never reached）

            m.intv_provider.stream = _exploding_stream

            r = client.post("/api/chat/stream", json={"message": "我难过"})
            assert r.status_code == 200
            events = _parse_sse(r.text)
            tokens = [e["data"]["token"] for e in events if e["event"] == "token"]
            # 异常后仍有 fallback token
            assert "".join(tokens) == FALLBACK_REPLY


class TestChatStreamPersona:
    def test_stream_unknown_persona_falls_back_to_default(self):
        """未知 persona_id → 后端回退 default，done.persona_id 校正为 default。"""
        with _patch_chat_graph() as m:
            m.triage_provider.chat = AsyncMock(return_value="倾诉")
            m.rag_service.search = AsyncMock(return_value=[])
            m.intv_provider.stream = _make_fake_stream(["嗯"])

            r = client.post(
                "/api/chat/stream",
                json={"message": "我难过", "persona_id": "nonexistent"},
            )
            assert r.status_code == 200
            events = _parse_sse(r.text)
            assert events[-1]["data"]["persona_id"] == "default"
