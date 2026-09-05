# -*- coding: utf-8 -*-
"""app/api/ratelimit.py 单测：匿名 IP 限流（方案 A 加固项）。

覆盖：
1. 匿名超限 → 429
2. 窗口滑动后恢复 → 200
3. 登录用户豁免（依赖 override 返回账号）
4. X-Forwarded-For 不同 IP 各自独立计数
5. 真实应用三个匿名 LLM 端点均挂了限流依赖
"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import ratelimit
from app.api.ratelimit import rate_limit


@pytest.fixture(autouse=True)
def _clean_buckets():
    ratelimit._BUCKETS.clear()
    yield
    ratelimit._BUCKETS.clear()


def _make_app(account_override=None) -> FastAPI:
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(rate_limit("probe", limit=2, window_sec=60))])
    async def probe():
        return {"ok": True}

    if account_override is not None:
        app.dependency_overrides[ratelimit.get_current_account] = account_override
    return app


def test_anonymous_over_limit_returns_429():
    client = TestClient(_make_app())
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200
    r3 = client.get("/probe")
    assert r3.status_code == 429
    assert "频率限制" in r3.json()["detail"]


def test_window_slides_and_recovers(monkeypatch):
    client = TestClient(_make_app())
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 200
    assert client.get("/probe").status_code == 429
    now[0] += 61.0  # 窗口滑出
    assert client.get("/probe").status_code == 200


def test_authenticated_user_is_exempt():
    user = object()  # 仅作"非 None"哨兵，限流只判断账号是否解析成功
    client = TestClient(_make_app(account_override=lambda: user))
    for _ in range(6):
        assert client.get("/probe").status_code == 200


def test_forwarded_for_ips_are_isolated():
    client = TestClient(_make_app())
    assert client.get("/probe", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/probe", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/probe", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    # 另一个 IP 不受影响
    assert client.get("/probe", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_real_anonymous_llm_endpoints_carry_rate_limit():
    from fastapi.routing import APIRoute

    from app.main import app

    def _iter_api_routes(router):
        for r in router.routes:
            if isinstance(r, APIRoute):
                yield r
            elif type(r).__name__ == "_IncludedRouter":  # 新版 FastAPI 懒展开包装
                yield from _iter_api_routes(r.original_router)

    targets = {"chat", "chat_stream", "generate_report"}
    found: set[str] = set()
    for route in _iter_api_routes(app.router):
        if route.name in targets and "POST" in route.methods:
            if any(
                getattr(getattr(d, "dependency", d), "__module__", "") == "app.api.ratelimit"
                for d in route.dependencies
            ):
                found.add(route.name)
    assert found == targets
