"""A1 注册登录单测：4 consent 强制校验/注册成功并绑定 Bearer token 到 session。"""
import os
import tempfile
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.api.auth import router as auth_router
from app.api.sessions import router as sessions_router
from app.api.deps import get_current_account


# ---- Isolated SQLite per test (class scoped) ----
@pytest.fixture(name="isolated_client")
def _client():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    from app import models  # noqa: F401  注册表
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(sessions_router)

    def _get_db_override():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # get_current_account 走真实逻辑（Authorization Bearer 查询 users 表）
    app.dependency_overrides[get_db] = _get_db_override
    from app.api import deps as _deps
    app.dependency_overrides[_deps.get_db_session] = _get_db_override

    with TestClient(app) as c:
        yield c

    # cleanup
    engine.dispose()
    try:
        os.unlink(db_path)
    except OSError:
        pass


FULL_CONSENTS = {"tool": True, "guardian": True, "privacy14": True, "crisis": True}
PROFILE = {"name": "小明", "grade": "高一", "age": 15}


class TestAuthRegister:
    def test_missing_any_consent_rejected_422(self, isolated_client):
        # 逐个缺 1 个 consent（共4次），全部返回 422 code=missing_consents
        keys = ["tool", "guardian", "privacy14", "crisis"]
        for skip in keys:
            consents = {**FULL_CONSENTS, skip: False}
            r = isolated_client.post(
                "/api/auth/register",
                json={"consents": consents, "profile": PROFILE, "role": "student"},
            )
            assert r.status_code == 422, f"缺少 {skip} 应返回 422 实际 {r.status_code}"
            body = r.json()
            assert "missing" in str(body) or (isinstance(body, dict) and "detail" in body)

    def test_register_success_creates_user_and_token(self, isolated_client):
        r = isolated_client.post("/api/auth/register", json={"consents": FULL_CONSENTS, "profile": PROFILE})
        assert r.status_code == 200
        body = r.json()
        assert len(body["account_id"]) == 32
        assert body["token"] == body["account_id"]  # MVP 轻量 opaque token 约定
        assert isinstance(body["label"], str) and len(body["label"]) >= 6

    def test_bearer_token_links_session_to_account(self, isolated_client):
        # 注册 → token → 创建 session 带 Bearer → 返回 session 成功
        token = isolated_client.post(
            "/api/auth/register", json={"consents": FULL_CONSENTS, "profile": PROFILE}
        ).json()["token"]
        r = isolated_client.post(
            "/api/sessions",
            json={"label": "ceshi-acc"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        sid = r.json()["session_id"]
        assert len(sid) == 32

        # 用同 token 取 sessions 列表 -> 长度1 且就是这个 sid（证明 account 绑定成功）
        list_r = isolated_client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"})
        assert list_r.status_code == 200
        items = list_r.json()["items"]
        assert len(items) == 1
        assert items[0]["session_id"] == sid

    def test_anonymous_session_backward_compat(self, isolated_client):
        # 无 Authorization 头 -> 仍能创建 session（account_id NULL，向后兼容 TR-2.4）
        r = isolated_client.post("/api/sessions", json={"label": "no-login"})
        assert r.status_code == 200
        assert len(r.json()["session_id"]) == 32
