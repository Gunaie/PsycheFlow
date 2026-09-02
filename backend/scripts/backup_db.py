# -*- coding: utf-8 -*-
"""SQLite 备份 + AES-256-CBC 加密（合规 c：备份文件加密）。

用法（容器内）：
  docker exec psycheflow-backend uv run python scripts/backup_db.py
  # 指定输出目录：
  docker exec psycheflow-backend uv run python scripts/backup_db.py -o /app/data/backups

流程：
  1. sqlite3.Connection.backup() 在线一致性拷贝到临时 .db（不长时间阻塞写事务）
  2. openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 加密为 .db.enc
  3. 删除明文临时 .db，输出 .db.enc 路径 + 解密命令

要求：BACKUP_PASSPHRASE 环境变量（.env），空则拒绝备份（避免写明文落盘）。
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, "/app")

from app.core.config import settings  # noqa: E402


def _run_openssl_encrypt(passphrase: str, src: str, dst: str) -> None:
    """调用 openssl enc 加密。失败抛 RuntimeError。"""
    cmd = [
        "openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "100000",
        "-pass", f"pass:{passphrase}",
        "-in", src, "-out", dst,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"openssl 加密失败: {proc.stderr.strip()}")


def backup(passphrase: str, out_dir: str, db_path: str) -> str:
    """一致性备份 + 加密，返回 .db.enc 路径。"""
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    plain = os.path.join(out_dir, f"psycheflow-{stamp}.db.tmp")
    enc = os.path.join(out_dir, f"psycheflow-{stamp}.db.enc")

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(plain)
    try:
        src.backup(dst)  # 在线一致性拷贝
    finally:
        dst.close()
        src.close()

    _run_openssl_encrypt(passphrase, plain, enc)
    os.remove(plain)  # 删明文临时文件，确保只有加密产物落盘
    return enc


def main():
    ap = argparse.ArgumentParser(description="SQLite 备份 + AES-256-CBC 加密")
    ap.add_argument(
        "-o", "--out",
        default=os.path.join(os.path.dirname(settings.sqlite_path), "backups"),
        help="输出目录（默认 <data>/backups）",
    )
    args = ap.parse_args()

    passphrase = settings.backup_passphrase
    if not passphrase:
        print("错误：BACKUP_PASSPHRASE 未配置（.env），拒绝备份以避免写明文落盘。", file=sys.stderr)
        sys.exit(2)

    db_path = settings.sqlite_path
    if not os.path.exists(db_path):
        print(f"错误：数据库不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    enc = backup(passphrase, args.out, db_path)
    print(f"备份完成（已加密）: {enc}")
    print(f"大小: {os.path.getsize(enc)} 字节")
    print(f"解密命令: openssl enc -d -aes-256-cbc -salt -pbkdf2 -iter 100000 "
          f"-pass pass:BACKUP_PASSPHRASE -in {enc} -out restored.db")


if __name__ == "__main__":
    main()
