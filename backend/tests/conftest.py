"""测试共享 fixtures：内存 SQLite + FastAPI TestClient 依赖覆盖。"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
import app.models  # noqa: F401  注册表到 metadata


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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
