"""通用 FastAPI 依赖：DB session 与当前账号（匿名认证，向后兼容）。"""
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
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
    - 无头 / 格式不对 / token 查不到 → 返回 None，端点自行判断匿名/登录态。
    - DB 系统级异常（断连等）→ 不静默吞掉，上抛让 FastAPI 返回 503，避免在数据库
      不可用时把已登录用户误判为匿名（可能绕过限流/权限）。
    """
    if not authorization:
        return None
    # 忽略前后空格，以空格切分；前缀 "bearer" 大小写不敏感
    parts = authorization.strip().split()
    if len(parts) < 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1]
    try:
        stmt = select(User).where(User.token == token)
        result = db.execute(stmt).scalar_one_or_none()
        return result
    except SQLAlchemyError:
        # DB 级异常（断连/超时）不上抛会掩盖基础设施故障，导致权限降级
        raise
    except Exception:
        # 其他异常（如解析错误）静默返回 None，保持向后兼容
        return None


def get_current_teacher(
    account: User | None = Depends(get_current_account),
) -> User:
    """C 三期：B 端管理后台权限依赖。非教师登录一律 403。"""
    from fastapi import HTTPException

    if account is None:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
    if account.role != "teacher":
        raise HTTPException(status_code=403, detail={"code": "forbidden", "reason": "仅教师账号可访问"})
    return account
