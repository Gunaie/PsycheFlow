"""SQLite + SQLAlchemy 引擎与 session 工厂。

幂等建表（create_all），MVP 不引入 alembic 迁移。
数据目录由 docker compose 的 ./data:/app/data 卷持久化。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# 确保数据目录存在（容器内 /app/data 已由卷挂载，此处兜底）
_db_dir = os.path.dirname(settings.sqlite_path)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.sqlite_path}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """幂等建表：导入模型以注册到 metadata，再 create_all。"""
    from app import models  # noqa: F401  仅为注册表
    Base.metadata.create_all(engine)


def get_db():
    """FastAPI 依赖：每请求一个 DB session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
