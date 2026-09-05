"""认证与账号端点：注册（含知情同意校验）、token 登录、label 登录、教师密码登录。"""
import hashlib
import secrets
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
    # C 三期：教师注册需设置密码与登录账号名（label）
    password: str | None = None
    label: str | None = None


class LoginByPassword(BaseModel):
    label: str
    password: str


class AuthResp(BaseModel):
    account_id: str
    token: str
    label: str
    role: str = "student"


class LoginByToken(BaseModel):
    token: str


class LoginByLabel(BaseModel):
    label: str


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    """PBKDF2-SHA256 加盐哈希，格式 salt_hex$hash_hex（无需第三方依赖）。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 100_000)
    return f"{salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 100_000)
    return secrets.compare_digest(digest.hex(), expected)


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

    # 2) 教师角色：密码必填（>=6位），label 可自定义
    custom_label: str | None = None
    password_hash: str | None = None
    if req.role == "teacher":
        if not req.password or len(req.password) < 6:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_password", "reason": "教师注册须设置至少 6 位密码"},
            )
        password_hash = _hash_password(req.password)
        if req.label:
            candidate = req.label.strip()
            if not (3 <= len(candidate) <= 64):
                raise HTTPException(
                    status_code=422,
                    detail={"code": "invalid_label", "reason": "账号名长度须为 3-64 字符"},
                )
            exists = db.execute(
                select(User).where(User.label == candidate)
            ).scalar_one_or_none()
            if exists:
                raise HTTPException(status_code=409, detail="账号名已被使用")
            custom_label = candidate

    # 3) 生成唯一 label（冲突重试；教师自定义 label 优先）
    label: str | None = custom_label
    if label is None:
        for candidate in _gen_label():
            exists = db.execute(
                select(User).where(User.label == candidate)
            ).scalar_one_or_none()
            if not exists:
                label = candidate
                break

    # 4) 账号 id / token（token 与 account_id 分离，避免 account_id 泄露即登录）
    account_id = uuid.uuid4().hex
    token = secrets.token_hex(32)  # 256-bit 随机 token

    user = User(
        id=account_id,
        label=label,
        role=req.role,
        password_hash=password_hash,
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

    return AuthResp(account_id=account_id, token=token, label=label, role=req.role)


@router.post("/login_by_token", response_model=AuthResp)
async def login_by_token(req: LoginByToken, db: Session = Depends(get_db)):
    stmt = select(User).where(User.token == req.token)
    user = db.execute(stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="token 无效或不存在")
    return AuthResp(account_id=user.id, token=user.token, label=user.label, role=user.role)


@router.post("/login_by_label", response_model=AuthResp)
async def login_by_label(req: LoginByLabel, db: Session = Depends(get_db)):
    """匿名 label 登录（仅限学生/家长：label 本身即匿名凭证）。

    授权链加固：教师账号 password_hash 非空，必须走 /login_by_password，
    凭 label 直接拿 token 会绕过密码 → 一律 403。
    """
    stmt = select(User).where(User.label == req.label)
    user = db.execute(stmt).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="label 无效或不存在")
    if user.role == "teacher":
        raise HTTPException(
            status_code=403,
            detail={"code": "teacher_requires_password", "reason": "教师账号须使用密码登录"},
        )
    return AuthResp(account_id=user.id, token=user.token, label=user.label, role=user.role)


@router.post("/login_by_password", response_model=AuthResp)
async def login_by_password(req: LoginByPassword, db: Session = Depends(get_db)):
    """C 三期：教师密码登录（仅 role=teacher 且已设置密码的账号可用）。"""
    user = db.execute(
        select(User).where(User.label == req.label)
    ).scalar_one_or_none()
    if not user or not user.password_hash or not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号名或密码错误")
    return AuthResp(account_id=user.id, token=user.token, label=user.label, role=user.role)
