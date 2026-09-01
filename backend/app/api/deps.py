"""通用 FastAPI 依赖：DB session 与当前账号（匿名认证，向后兼容）。"""
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User


def get_db_session():
    """DB session 依赖：直接复用 app.db.get_db（别名即可，保持 deps 统一入口）。"""
    yield from get_db()


def get_current_account(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    """
    从 Authorization: Bearer <token> 头中解析当前账号。

    NFR-1 要求完全匿名兼容：
    - 无头 / 格式不对 / token 查不到 / 任何异常 → 一律返回 None，绝不抛 HTTPException。
    """
    if not authorization:
        return None
    try:
        # 忽略前后空格，以空格切分；前缀 "bearer" 大小写不敏感
        parts = authorization.strip().split()
        if len(parts) < 2 or parts[0].lower() != "bearer":
            return None
        token = parts[1]
        stmt = select(User).where(User.token == token)
        result = db.execute(stmt).scalar_one_or_none()
        return result
    except Exception:
        # 任何异常（DB 断连、SQL 错误等）都静默返回 None
        return None
