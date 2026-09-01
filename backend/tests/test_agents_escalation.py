"""AC-B2: 危机命中 → escalation 零 LLM 调用 + crisis_*.json 落盘。

验证：
1. escalation_node 不调用 provider.chat（mock 验证 call_count=0）
2. 返回 crisis=true + crisis_message 硬编码话术含 12355
3. write_crisis_audit 落盘 → crisis_<sid>_<ts>.json 文件存在
"""
import glob
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.nodes.escalation import escalation_node
from app.agents.state import AgentState


@pytest.fixture
def temp_logs_dir(monkeypatch):
    """临时 logs_dir 让 audit 落盘到可控路径。"""
    tmp = tempfile.mkdtemp(prefix="psylogs_test_")
    from app.core import audit as audit_mod
    monkeypatch.setattr(audit_mod, "ensure_logs_dir", lambda: tmp)
    # settings.logs_dir 也 patch 一下（write_crisis_audit 内部 os.makedirs 用）
    from app.core.config import settings
    monkeypatch.setattr(settings, "logs_dir", tmp)
    return tmp


@pytest.mark.asyncio
async def test_escalation_never_calls_llm(temp_logs_dir):
    """escalation 节点永不调用 LLM（零幻觉风险）。

    通过 patch 全局 LLM 单例 app.core.llm.provider.chat 验证零调用，
    因为 escalation_node 自身不 import provider，但调用链上 crisis_message /
    write_crisis_audit 若间接调 LLM 也会被本 mock 捕获。
    """
    state: AgentState = {
        "session_id": "test-sid-001",
        "account_id": "test-acc-001",
        "user_message": "我想自杀",
        "detected_words": ["自杀"],
        "agent_trace": ["triage"],
    }
    with patch("app.core.llm.provider") as mock_provider:
        mock_provider.chat = AsyncMock(return_value="should_not_be_called")
        result = await escalation_node(state)
        # 核心断言：LLM 没被调用
        mock_provider.chat.assert_not_awaited()

    assert result["crisis"] is True
    assert result["is_crisis"] is True
    assert result["current_agent"] == "escalation"
    assert result["agent_trace"] == ["triage", "escalation"]
    # 硬编码 12355 必须出现
    assert "12355" in result["final_reply"]
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_escalation_writes_crisis_audit_json(temp_logs_dir):
    """escalation 调 write_crisis_audit 落盘 crisis_<sid>_<ts>.json"""
    state: AgentState = {
        "session_id": "test-sid-002",
        "account_id": "test-acc-002",
        "user_message": "我不想活了",
        "detected_words": ["不想活"],
        "agent_trace": ["triage"],
    }
    with patch("app.core.llm.provider"):
        result = await escalation_node(state)

    # 验证落盘文件存在
    crisis_files = glob.glob(os.path.join(temp_logs_dir, "crisis_test-sid-002_*.json"))
    assert len(crisis_files) >= 1, f"期望至少 1 个 crisis_*.json，实际：{crisis_files}"

    # 验证 JSON 内容
    import json
    with open(crisis_files[0], "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["session_id"] == "test-sid-002"
    assert payload["account_id"] == "test-acc-002"
    assert "不想活" in payload["trigger_words"]
    assert payload["user_input_raw"] == "我不想活了"
    assert "12355" in payload["crisis_reply"]
    assert payload["referred_12355_bool"] is True


@pytest.mark.asyncio
async def test_escalation_with_assessment_context(temp_logs_dir):
    """escalation 节点接收 assessment_context 并写入 audit"""
    state: AgentState = {
        "session_id": "test-sid-003",
        "account_id": "test-acc-003",
        "user_message": "我打算跳楼",
        "detected_words": ["跳楼"],
        "assessment_context": {
            "scale_id": "phq_a",
            "severity": "severe",
            "crisis_level": "elevated",
            "total_score": 27,
        },
        "agent_trace": ["triage", "assessment"],
    }
    with patch("app.core.llm.provider"):
        result = await escalation_node(state)

    assert result["crisis"] is True
    # 验证 audit 含 assessment_context
    crisis_files = glob.glob(os.path.join(temp_logs_dir, "crisis_test-sid-003_*.json"))
    assert len(crisis_files) >= 1
    import json
    with open(crisis_files[0], "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["assessment_context"]["severity"] == "severe"
    assert payload["assessment_context"]["total_score"] == 27
