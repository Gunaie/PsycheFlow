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
        assert len(body["token"]) == 64  # secrets.token_hex(32) = 64 chars
        assert body["token"] != body["account_id"]  # token 与 account_id 分离（安全加固）
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


class TestTeacherAuthHardening:
    """授权链复检：教师账号不得凭 label 绕过密码拿 token。"""

    def test_teacher_login_by_label_rejected_403(self, isolated_client):
        reg = isolated_client.post(
            "/api/auth/register",
            json={
                "consents": FULL_CONSENTS, "profile": {}, "role": "teacher",
                "label": "tch01", "password": "secret123",
            },
        )
        assert reg.status_code == 200, f"教师注册应成功 实际 {reg.status_code}"

        # 凭 label 直接登录应被拒（绕密漏洞修复）
        r = isolated_client.post("/api/auth/login_by_label", json={"label": "tch01"})
        assert r.status_code == 403, f"教师 label 登录应 403 实际 {r.status_code}"
        assert "teacher_requires_password" in str(r.json())

    def test_teacher_password_login_still_works(self, isolated_client):
        isolated_client.post(
            "/api/auth/register",
            json={
                "consents": FULL_CONSENTS, "profile": {}, "role": "teacher",
                "label": "tch02", "password": "secret123",
            },
        )
        r = isolated_client.post(
            "/api/auth/login_by_password",
            json={"label": "tch02", "password": "secret123"},
        )
        assert r.status_code == 200
        assert len(r.json()["token"]) == 64

    def test_student_label_login_unaffected(self, isolated_client):
        """学生 label 是匿名凭证本身，登录不受教师加固影响。"""
        sreg = isolated_client.post(
            "/api/auth/register", json={"consents": FULL_CONSENTS, "profile": PROFILE}
        )
        assert sreg.status_code == 200
        slabel = sreg.json()["label"]
        r = isolated_client.post("/api/auth/login_by_label", json={"label": slabel})
        assert r.status_code == 200, f"学生 label 登录应 200 实际 {r.status_code}"
