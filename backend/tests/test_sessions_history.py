"""A2 历史报告单测：倒序 + account 过滤 + 分页 + report PDF下载。"""
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker
from unittest.mock import AsyncMock, patch

from app.db import Base, get_db
from app.models import AssessmentRecord, Session as SessionModel, User
from app.api.sessions import router as sessions_router
from app.api.auth import router as auth_router


@pytest.fixture(name="cli_fixture")
def _fixture():
    fd, db_path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    TSL = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(auth_router); app.include_router(sessions_router)
    def _override(): db = TSL(); yield db; db.close()
    app.dependency_overrides[get_db] = _override
    from app.api import deps as _deps
    app.dependency_overrides[_deps.get_db_session] = _override

    with TestClient(app) as c:
        # seed 2 users: A (3 sessions) + B (2 sessions)，时间严格递增方便倒序断言
        uA = User(id='a'*32, label='uA-label', token='tA'*16, role='student', consents={'tool':True,'guardian':True,'privacy14':True,'crisis':True}, profile={})
        uB = User(id='b'*32, label='uB-label', token='tB'*16, role='student', consents={'tool':True,'guardian':True,'privacy14':True,'crisis':True}, profile={})
        db = TSL(); db.add_all([uA, uB]); db.commit()

        base_ts = datetime(2026, 9, 1, 9, 0, 0)
        def make(user, i, crisis=False):
            s = SessionModel(
                id=uuid4_like(user.id[-4:] + str(i)),
                label=f"{user.label}-s{i}",
                account_id=user.id,
                created_at=base_ts + timedelta(minutes=i),
            )
            a = AssessmentRecord(
                id=uuid4_like("a"+user.id[-3:]+str(i)),
                session_id=s.id,
                scale_id="phq_a",
                scale_name="PHQ-A",
                total_score=5 + i,
                severity="mild",
                crisis_level="elevated" if crisis else "safe",
                crisis_triggers=["第9题 得分 2"] if crisis else [],
                interpretation="轻度",
                answers={"1": 1},
            )
            return s, a
        import uuid
        def uuid4_like(prefix): return (prefix + uuid.uuid4().hex)[:32]
        pieces = []
        pieces += [*make(uA, 1), *make(uA, 2), *make(uA, 3, crisis=True)]
        pieces += [*make(uB, 11), *make(uB, 12)]
        db.add_all(pieces); db.commit()
        # commit 后、close 前提取纯字符串，避免 DetachedInstanceError
        uA_token, uA_label = uA.token, uA.label
        uB_token, uB_label = uB.token, uB.label
        db.close()
        yield c, uA_token, uB_token, uA_label, uB_label
    engine.dispose()
    try: os.unlink(db_path)
    except OSError: pass


class TestSessionsList:
    def test_list_desc_order_and_account_filtered(self, cli_fixture):
        c, uA_token, uB_token, uA_label, uB_label = cli_fixture
        r = c.get("/api/sessions", headers={"Authorization": f"Bearer {uA_token}"})
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 3, f"uA 应有 3 条实际 {len(items)}"
        # 断言 B 的 label 不在结果里
        for it in items:
            assert uB_label not in it["label"], "B 的 session 不应出现在 A 列表"
        # 断言倒序：items[0].created_at > items[2].created_at
        t0 = datetime.fromisoformat(items[0]["created_at"])
        t2 = datetime.fromisoformat(items[-1]["created_at"])
        assert t0 > t2, "应为 created_at DESC 倒序"

    def test_pagination_page_size_2(self, cli_fixture):
        c, uA_token, uB_token, uA_label, uB_label = cli_fixture
        r1 = c.get("/api/sessions?page_size=2", headers={"Authorization": f"Bearer {uA_token}"})
        assert r1.status_code == 200
        j = r1.json()
        assert len(j["items"]) == 2
        assert j["next_cursor"] is not None, "3 条取 2 条应有 next_cursor"

        r2 = c.get(f"/api/sessions?page_size=2&cursor={j['next_cursor']}", headers={"Authorization": f"Bearer {uA_token}"})
        j2 = r2.json()
        assert len(j2["items"]) == 1, f"第二页应为 1 条实际 {len(j2['items'])}"
        assert j2["next_cursor"] is None, "第二页（末页）next_cursor 应为 None"

    def test_download_report_pdf_ok_size(self, cli_fixture):
        c, uA_token, uB_token, uA_label, uB_label = cli_fixture
        items = c.get("/api/sessions?page_size=1", headers={"Authorization": f"Bearer {uA_token}"}).json()["items"]
        sid = items[0]["session_id"]

        _long = "【家庭端】每日 30 分钟倾听陪伴，识别自伤谈论与物品赠与信号；作息 22:30/7:30 固定，户外 40 分钟。" * 8
        with patch("app.reports.service.provider") as mock_p:
            mock_p.chat = AsyncMock(return_value=_long)
            r = c.get(f"/api/sessions/{sid}/report", headers={"Authorization": f"Bearer {uA_token}"})
        assert r.status_code == 200, f"下载 PDF 应 200 实际 {r.status_code}"
        assert r.headers["content-type"].lower().startswith("application/pdf")
        # 单量表报告因无 SCARED 子维度，模板渲染量较小，100KB 足以证明 PDF 非空且完整
        assert len(r.content) >= 100_000, f"PDF 应 ≥ 100KB 实际 {len(r.content)}"
