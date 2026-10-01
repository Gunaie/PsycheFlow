"""SQLAlchemy 引擎与 session 工厂（SQLite 本地开发 / PostgreSQL 生产双驱动）。

- 默认 SQLite（本地开发零配置，数据目录由 docker compose 的 ./data:/app/data 卷持久化）
- 生产通过 DATABASE_URL 切换 PostgreSQL（docker-compose 内置 postgres:16-alpine 服务）
- PostgreSQL 模式引入 alembic 做 schema 迁移；SQLite 模式保留幂等 create_all + 最小化 ALTER TABLE
"""
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# 数据库 URL 解析：优先 DATABASE_URL（PostgreSQL），回退 SQLite
if settings.database_url:
    # PostgreSQL / MySQL 等外部数据库
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,   # 连接前 ping，防断连
        pool_recycle=3600,    # 1h 回收连接，防 MySQL 8h 断连
    )
else:
    # SQLite 本地开发
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

    PostgreSQL 模式：create_all 仅用于首次建表，后续 schema 变更走 alembic 迁移。
    SQLite 模式：保留最小化 ALTER TABLE 迁移（列不存在才加，幂等）。
    """
    from app import models  # noqa: F401  仅为注册表
    Base.metadata.create_all(engine)

    if not settings.database_url:
        # —— SQLite 专属：列迁移（pragma 表结构无列则 ALTER TABLE ADD，幂等）
        _migrate_sqlite_columns(engine)

    # —— 合规：SQLite 文件权限收紧为 0600（Linux 生产生效，Windows no-op）
    restrict_db_file_perms()


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

    try:
        # users.password_hash：C 三期教师密码登录新增
        if not _col_exists(engine, "users", "password_hash"):
            with engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN password_hash VARCHAR(128) NULL"
                ))
                conn.commit()
    except Exception:
        import logging
        logging.getLogger("psycheflow.db").warning("users.password_hash 迁移失败，忽略", exc_info=True)

    try:
        # conversation_turns.attachments_json：病例解读附件元数据新增
        if not _col_exists(engine, "conversation_turns", "attachments_json"):
            with engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE conversation_turns ADD COLUMN attachments_json JSON NULL"
                ))
                conn.commit()
    except Exception:
        import logging
        logging.getLogger("psycheflow.db").warning(
            "conversation_turns.attachments_json 迁移失败，忽略", exc_info=True
        )


def restrict_db_file_perms(db_path: str | None = None) -> None:
    """合规：SQLite 文件权限收紧为 0600（仅属主可读写）。

    Windows bind mount 下 chmod 为 no-op（权限由 NTFS ACL 管控），Linux 生产环境生效。
    文件不存在或无属主权限时静默跳过，绝不阻断启动。
    """
    path = db_path or settings.sqlite_path
    try:
        if path and os.path.exists(path):
            os.chmod(path, 0o600)
    except OSError:
        # Windows / 无属主权限时忽略，不阻断主流程
        pass


def get_db():
    """FastAPI 依赖：每请求一个 DB session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
