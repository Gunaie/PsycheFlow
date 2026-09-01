"""认证与账号端点：注册（含知情同意校验）、token 登录、label 登录。"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Pydantic 入参与出参（Pydantic v2 BaseModel）
# ---------------------------------------------------------------------------

class Consent(BaseModel):
    """四项知情同意，全部须为 True 方可注册。"""
    tool: bool
    guardian: bool
    privacy14: bool
    crisis: bool


class Profile(BaseModel, extra="allow"):
    """用户画像字段：全部可空；extra=allow 兼容前端附加字段。"""
    name: str | None = None
    student_no: str | None = None
    grade: str | None = None
    klass: str | None = None
    gender: str | None = None
    age: int | None = None
    guardian_phone: str | None = None
    school: str | None = None
    teacher_email: str | None = None


from typing import Literal


class RegisterReq(BaseModel):
    consents: Consent
    profile: Profile = Profile()
    role: Literal["student", "teacher", "parent"] = "student"


class AuthResp(BaseModel):
    account_id: str
    token: str
    label: str


class LoginByToken(BaseModel):
    token: str


class LoginByLabel(BaseModel):
    label: str


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _gen_label(max_attempts: int = 5) -> str:
    """生成形如 ceshi-<4位hex> 的匿名 label，冲突重试最多 max_attempts 次。

    固定长度 >= 6（ceshi- 为 6 前缀 + 4 位十六进制共 10 字符）。
    """
    for _ in range(max_attempts):
        suffix = uuid.uuid4().hex[:4]
        yield f"ceshi-{suffix}"
    # 兜底：加长到 8 位十六进制（冲突极低）
    yield f"ceshi-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.post("/register", response_model=AuthResp)
async def register(req: RegisterReq, db: Session = Depends(get_db)):
    # 1) 知情同意校验：缺任一 False 就返回 422
    missing = [
        x for x in ["tool", "guardian", "privacy14", "crisis"]
        if not getattr(req.consents, x)
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_consents", "missing": missing},
        )

    # 2) 生成唯一 label（冲突重试）
    label: str | None = None
    for candidate in _gen_label():
        exists = db.execute(
            select(User).where(User.label == candidate)
        ).scalar_one_or_none()
        if not exists:
            label = candidate
            break

    # 3) 账号 id / token（MVP：token 直接等于 account_id 即可，opaque 足够）
    account_id = uuid.uuid4().hex
    token = account_id

    user = User(
        id=account_id,
        label=label,
        role=req.role,
        profile=req.profile.model_dump(),
        consents=req.consents.model_dump(),
        token=token,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="账号信息冲突，请重试")
    db.refresh(user)

    return AuthResp(account_id=account_id, token=token, label=label)


@router.post("/login_by_token", response_model=AuthResp)
async def login_by_token(req: LoginByToken, db: Session = Depends(get_db)):
    stmt = select(User).where(User.token == req.token)
    user = db.execute(stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="token 无效或不存在")
    return AuthResp(account_id=user.id, token=user.token, label=user.label)


@router.post("/login_by_label", response_model=AuthResp)
async def login_by_label(req: LoginByLabel, db: Session = Depends(get_db)):
    stmt = select(User).where(User.label == req.label)
    user = db.execute(stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="label 无效或不存在")
    return AuthResp(account_id=user.id, token=user.token, label=user.label)
