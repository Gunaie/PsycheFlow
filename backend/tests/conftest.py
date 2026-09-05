"""测试共享 fixtures：内存 SQLite + FastAPI TestClient 依赖覆盖。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
import app.models  # noqa: F401  注册表到 metadata
import app.api.ratelimit as _ratelimit


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """限流桶是进程级全局，测试间必须清零，否则套件内匿名 chat 调用
    累计同一桶导致 audit 等模块随机撞 429。"""
    _ratelimit._BUCKETS.clear()
    yield
    _ratelimit._BUCKETS.clear()


@pytest.fixture
def client():
    """每个测试用独立的内存 SQLite（StaticPool 共享单连接，跨请求可见）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    from app.api import deps as _deps
    app.dependency_overrides[_deps.get_db_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
