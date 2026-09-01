"""SQLite + SQLAlchemy 引擎与 session 工厂。

幂等建表（create_all），MVP 不引入 alembic 迁移。
数据目录由 docker compose 的 ./data:/app/data 卷持久化。
"""
import os

from sqlalchemy import create_engine, text
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
    """幂等建表：导入模型以注册到 metadata，再 create_all。
    另外：对 SQLite 旧库做最小化 ALTER TABLE 迁移（MVP 不引入 alembic）：
      - sessions 表补 account_id VARCHAR(32) NULLABLE FK 列（Task 1 新增字段）
    """
    from app import models  # noqa: F401  仅为注册表
    Base.metadata.create_all(engine)

    # —— SQLite 列迁移：pragma 表结构无列则 ALTER TABLE ADD（列不存在才加，幂等）
    _migrate_sqlite_columns(engine)


def _col_exists(engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info('{table}')")).mappings().all()
    return any(r["name"] == column for r in rows)


def _migrate_sqlite_columns(engine) -> None:
    """SQLite 专属：给旧表补列。"""

    try:
        # sessions.account_id：Task 1 新增
        if not _col_exists(engine, "sessions", "account_id"):
            with engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE sessions ADD COLUMN account_id VARCHAR(32) NULL "
                    "REFERENCES users(id) ON DELETE SET NULL"
                ))
                conn.commit()
    except Exception:
        # 不阻断服务启动（即使失败，create_all 已保证新表 OK）
        import logging
        logging.getLogger("psycheflow.db").warning("sessions.account_id 迁移失败，忽略", exc_info=True)


def get_db():
    """FastAPI 依赖：每请求一个 DB session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
