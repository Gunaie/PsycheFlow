# -*- coding: utf-8 -*-
"""教师密码重置运维脚本（忘记密码自救）。

用法（容器内）：
  docker exec -it psycheflow-backend uv run python scripts/reset_teacher_password.py --label 张老师
  # 指定新密码（至少 6 位）：
  docker exec -it psycheflow-backend uv run python scripts/reset_teacher_password.py --label 张老师 --password newpass123
  # 不带 --password 则自动生成 12 位随机密码并打印（仅显示一次）

规则：
  - 仅允许重置 role=teacher 的账号（学生无密码登录）
  - 复用注册接口同一套 PBKDF2-SHA256 哈希格式（salt$hash_hex）
  - 教师当前登录态不受影响（token 不变），用新密码重新登录即可
"""
import argparse
import secrets
import sys

sys.path.insert(0, "/app")

from app.api.auth import _hash_password  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _gen_password(length: int = 12) -> str:
    """生成无易混淆字符（0O1Iil）的随机密码。"""
    alphabet = "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def reset_password(label: str, password: str | None) -> str:
    """重置教师密码，返回最终生效的新密码。失败抛 ValueError。"""
    if password is None:
        password = _gen_password()
    if len(password) < 6:
        raise ValueError("新密码至少 6 位")

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.label == label)).scalar_one_or_none()
        if user is None:
            raise ValueError(f"账号名不存在: {label}")
        if user.role != "teacher":
            raise ValueError(f"该账号不是教师角色（role={user.role}），无需密码重置")
        user.password_hash = _hash_password(password)
        db.commit()
        return password
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="重置教师登录密码")
    parser.add_argument("--label", required=True, help="教师账号名")
    parser.add_argument("--password", default=None, help="新密码（至少 6 位；省略则自动生成）")
    args = parser.parse_args()

    try:
        password = reset_password(args.label, args.password)
    except ValueError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    print(f"[完成] 账号 {args.label} 的密码已重置为: {password}")
    print("请提醒教师用新密码在 /admin/login 重新登录（当前登录态不受影响）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
