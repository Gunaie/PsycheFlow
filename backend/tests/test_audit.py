"""A3 审计留痕 + 对话 turns 入库单测。"""
import glob
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.models import ConversationTurn
from app.api.chat import router as chat_router
from app.reports.service import generate_report_pdf
from app.core import audit as audit_mod


@pytest.fixture(name="env_setup")
def _env():
    fd, db_path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    logs_dir = tempfile.mkdtemp(prefix="psylogs_")
    # monkeypatch settings.logs_dir 临时
    from app.core.config import settings
    old_logs = settings.logs_dir
    # Pydantic v2 settings 构造后通常 frozen 或 protected；如果 model_validate 不支持，直接改私有 attr，最简单：mock _ensure 里取 settings.logs_dir 返回值 override，临时用 monkeypatch env var SETTINGS_LOGS_DIR（如 settings 支持），或者改 audit.ensure_logs_dir 读取 override env。我们改用 monkeypatch 函数级 fixture 里直接 monkeypatch audit_mod.ensure_logs_dir。

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    TSL = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    app = FastAPI()
    app.include_router(chat_router)
    def _db(): db = TSL(); yield db; db.close()
    app.dependency_overrides[get_db] = _db
    from app.api import deps as _deps
    app.dependency_overrides[_deps.get_db_session] = _db

    with TestClient(app) as c:
        yield {
            "client": c,
            "logs_dir": logs_dir,
            "TestingSessionLocal": TSL,
            "engine": engine,
            "db_path": db_path,
            "old_logs": old_logs,
        }
    # cleanup
    engine.dispose()
    import shutil
    try:
        shutil.rmtree(logs_dir, ignore_errors=True)
        os.unlink(db_path)
    except OSError:
        pass


def _session():
    return SimpleNamespace(
        id="SESS" + "x"*28,
        account_id=None,
        label="audit-sess",
        assessments=[],
    )
def _assessment(scale_id='phq_a', **kw):
    base = dict(scale_id=scale_id, scale_name='PHQ-A', total_score=5, severity='mild',
                crisis_level='safe', crisis_triggers=[], interpretation='轻度',
                needs_crisis_escalation=False, answers={'1':1})
    base.update(kw)
    return base


class TestAudit:
    def test_crisis_writes_json_file(self, env_setup, monkeypatch):
        d = env_setup; c = d["client"]
        # 强制审计写进临时 logs_dir（monkeypatch ensure 取目录）
        monkeypatch.setattr(audit_mod, "ensure_logs_dir", lambda: d["logs_dir"])
        before = set(glob.glob(os.path.join(d["logs_dir"], "crisis_*.json")))
        # 发危机消息
        r = c.post("/api/chat", json={"message": "我不想活了，想跳楼一死了之", "history": [], "session_id": "CRISIS_SESS_1234"})
        assert r.status_code == 200
        assert r.json()["crisis"] is True
        after = set(glob.glob(os.path.join(d["logs_dir"], "crisis_*.json")))
        new_files = after - before
        assert len(new_files) == 1, f"危机命中应新产生 1 个 json 实际新增 {len(new_files)}"
        with open(next(iter(new_files)), "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data["trigger_words"], list) and len(data["trigger_words"]) >= 1
        assert data["referred_12355_bool"] is True
        assert "跳楼" in data["user_input_raw"] or "不想活" in data["user_input_raw"]

    def test_report_generation_writes_audit_json(self, env_setup, monkeypatch):
        d = env_setup
        # patch ensure_logs_dir 让所有审计写进临时 logs_dir（与 crisis 测试一致方式，最可靠）
        monkeypatch.setattr(audit_mod, "ensure_logs_dir", lambda: d["logs_dir"])
        before = set(glob.glob(os.path.join(d["logs_dir"], "report_*.json")))
        assessments = [_assessment('phq_a', total_score=27, severity='severe', crisis_level='elevated', crisis_triggers=['第9题得分2'])]
        _long_narrative = (
            "# 综合发展建议（危机干预优先级：高）\n\n"
            "## 一、家庭端支持策略（建议近 30 天每日执行）\n"
            "1. 建立每日 30 分钟「无手机亲子陪伴时间」，以倾听为主，不评判不讲大道理，让孩子感到被理解与接纳；"
            "2. 监护人需学习识别青少年抑郁高危信号（自伤谈论、物品赠与、社交突然退缩），出现任一 24 小时内联系 12355；"
            "3. 家庭作息结构化：固定 22:30 熄灯、7:30 起床、每日户外 40 分钟以上，日照对调节节律有实证支持；"
            "4. 暂停增加课外补习强度，学业可暂时退后，孩子生命安全高于一切成绩目标。\n\n"
            "## 二、学校端协同策略（班主任 + 心理老师联动）\n"
            "1. 心理老师 3 个工作日内完成 1 对 1 危机访谈并记录，按《校园心理危机三级干预》流程登记在册；"
            "2. 班主任日常关注座位区域、课间表现，若发现独处哭泣/不交作业突变，当天与监护人电话；"
            "3. 避免当众提及心理筛查结果，可私下安排同伴互助搭档（如同桌提醒就餐）；"
            "4. 学业减轻：允许近 2 周作业减半/延期考试，给喘息空间。\n\n"
            "## 三、自我调节训练（每日练习 15 分钟 × 2 次）\n"
            "1. 4-2-6 腹式呼吸法：吸气 4 秒・屏息 2 秒・呼气 6 秒，连续 5 轮，激活副交感神经系统降低警觉性；"
            "2. 三栏记录表练习：「自动负性想法 / 支持证据 / 替代思维」三列，认知行为训练识别灾难化思维；"
            "3. 身体扫描 10 分钟：平躺或静坐，注意力从脚趾逐步扫描至头顶，觉察紧张部位并软化解；"
            "4. 行为激活最小步：每日完成 1 件「过去喜欢但现在没动力做」的小事（如听一首歌/散步 100 步），打卡记录。\n\n"
            "## 四、专业转介建议\n"
            "建议 1 周内到三甲医院精神心理科/儿童青少年心理门诊进行结构化临床评估；"
            "如出现紧急自伤计划/准备即刻拨打 12355 青少年心理热线或 120。"
        )
        with patch("app.reports.service.provider") as mp:
            mp.chat = AsyncMock(return_value=_long_narrative)
            pdf = __import__('asyncio').run(generate_report_pdf(_session(), assessments))
            assert isinstance(pdf, (bytes, bytearray)) and len(pdf) > 1000
        after = set(glob.glob(os.path.join(d["logs_dir"], "report_*.json")))
        new_files = after - before
        assert len(new_files) == 1, f"报告生成应新产生 report_*.json 1 个实际 {len(new_files)}"
        with open(next(iter(new_files)), "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["has_crisis"] is True
        assert isinstance(data["scores"]["phq_a"]["score"], int)
        assert data["narrative_len"] > 200
        assert data["file_size_bytes"] > 100_000

    def test_write_error_does_not_block_endpoint(self, env_setup, monkeypatch):
        d = env_setup; c = d["client"]
        # 让 ensure_logs_dir 返回不存在的父目录下的子路径（写失败），但接口仍 200
        fake_path = os.path.join(d["logs_dir"], "nonexistent_child_xxx", "nested2")  # 不存在且父级下还有子级 → open(path,'w') 会抛 FileNotFoundError
        monkeypatch.setattr(audit_mod, "ensure_logs_dir", lambda: fake_path)  # 不建目录
        r = c.post("/api/chat", json={"message": "我真的想死", "session_id": "NOBLOCK_SES"})
        assert r.status_code == 200, f"写日志失败也不能阻断危机响应返回 200 实际 {r.status_code}"


class TestConversationTurns:
    def test_two_rounds_insert_four_rows_alternating(self, env_setup):
        d = env_setup; c = d["client"]
        # B 二期：chat 走 LangGraph，triage+intervention 节点各自调 LLM
        # triage 固定返回"倾诉"意图，intervention 用 side_effect 给 2 轮不同回复
        with patch("app.agents.nodes.triage.provider") as mt, \
             patch("app.agents.nodes.intervention.provider") as mi, \
             patch("app.agents.nodes.intervention.rag_service") as mr:
            mt.chat = AsyncMock(return_value="倾诉")
            mr.search = AsyncMock(return_value=[])
            mi.chat = AsyncMock(side_effect=[
                "你好呀，今天感觉怎么样？",
                "听起来学习压力确实很大，可以试试 4-2-6 腹式呼吸。",
            ])
            r1 = c.post("/api/chat", json={"message": "你好，最近压力有点大", "history": []})
            r2 = c.post("/api/chat", json={"message": "是啊，作业好多", "history": [{"role":"user","content":"你好最近压力大"},{"role":"assistant","content":"xx"}]})
        assert r1.status_code == 200 and r2.status_code == 200
        TSL = d["TestingSessionLocal"]
        db = TSL()
        rows = db.query(ConversationTurn).order_by(ConversationTurn.created_at.asc()).all()
        db.close()
        assert len(rows) == 4, f"2 轮对话应有 4 行 user+assistant × 2，实际 {len(rows)}"
        roles = [r.role for r in rows]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_crisis_hit_column_true(self, env_setup, monkeypatch):
        d = env_setup; c = d["client"]
        monkeypatch.setattr(audit_mod, "ensure_logs_dir", lambda: d["logs_dir"])
        c.post("/api/chat", json={"message": "我打算今晚跳楼结束一切", "session_id": "CRISISS2"})
        TSL = d["TestingSessionLocal"]; db = TSL()
        turns = db.query(ConversationTurn).filter(ConversationTurn.crisis_hit == True).all()
        db.close()
        assert len(turns) >= 1, "危机命中应有 crisis_hit=True assistant 轮"

    def test_old_payload_no_session_account_still_200(self, env_setup):
        d = env_setup; c = d["client"]
        # 传最原始结构：只有 message, history；兼容（TR-7.3）
        # B 二期：chat 走 LangGraph，patch triage+intervention 节点
        with patch("app.agents.nodes.triage.provider") as mt, \
             patch("app.agents.nodes.intervention.provider") as mi, \
             patch("app.agents.nodes.intervention.rag_service") as mr:
            mt.chat = AsyncMock(return_value="倾诉")
            mi.chat = AsyncMock(return_value="好的我收到了")
            mr.search = AsyncMock(return_value=[])
            r = c.post("/api/chat", json={"message": "就是试试历史请求格式", "history": []})
        assert r.status_code == 200
        # turns 里两行列的 session_id/account_id 都是 NULL
        TSL = d["TestingSessionLocal"]; db = TSL()
        rows = db.query(ConversationTurn).all()
        db.close()
        assert all(row.session_id is None and row.account_id is None for row in rows[-2:])
