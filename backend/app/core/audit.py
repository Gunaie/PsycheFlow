"""审计日志模块：危机命中审计 + 报告生成审计，写 JSON 文件到 logs_dir。

设计原则：
- 审计写入失败必须吞掉 + warning 日志，**绝对不阻断**主业务流程返回。
- 文件名带毫秒精度，避免并发写入冲突。
"""
import os
import json
import datetime
import logging
import glob

logger = logging.getLogger("psycheflow.audit")


def ensure_logs_dir() -> str:
    """返回并兜底确保 settings.logs_dir 目录存在。

    settings 的 model_validator 已经在启动时创建过目录，这里再 makedirs 一次，
    防止热更新、目录被意外删除或 settings 尚未完全初始化时写入失败。
    """
    from app.core.config import settings

    os.makedirs(settings.logs_dir, exist_ok=True)
    return settings.logs_dir


def _get_audit_session():
    """审计 DB session 工厂：模块级，便于测试 monkeypatch 指向临时 engine。

    运行时默认走 app.db.SessionLocal（生产 SQLite）；测试中可被替换为指向
    临时 engine 的 sessionmaker，使 DB 双写可验证。
    """
    from app.db import SessionLocal

    return SessionLocal()


def _db_write_audit(event_type: str, payload: dict) -> None:
    """审计 DB 镜像写入（best-effort，失败仅 warning，绝不阻断主业务）。

    与 logs/*.json 文件双写：JSON 文件为人类可读留痕，DB 行便于查询/聚合/
    合规导出。session_id/account_id 冗余为索引列以支持按账号/会话检索。
    """
    try:
        from app.models import AuditLog

        db = _get_audit_session()
        try:
            db.add(
                AuditLog(
                    event_type=event_type,
                    session_id=payload.get("session_id"),
                    account_id=payload.get("account_id"),
                    payload=payload,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("audit DB 镜像写入失败(event=%s): %s", event_type, e)


def write_crisis_audit(
    session_id: str | None,
    account_id: str | None,
    trigger_words: list[str],
    user_input_raw: str,
    crisis_reply: str,
    assessment_context: dict | None = None,
) -> str | None:
    """写入一次危机命中的审计 JSON 文件。

    文件名格式：crisis_{sid}_{YYYYMMDD-HHMMSS-fff}.json
    返回写入的文件绝对路径；失败返回 None。
    """
    sid = session_id or "NOSESS"
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]  # 毫秒精度
    path = os.path.join(ensure_logs_dir(), f"crisis_{sid}_{ts}.json")

    payload = {
        "ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
        "session_id": session_id,
        "account_id": account_id,
        "trigger_words": trigger_words or [],
        "user_input_raw": user_input_raw,
        "crisis_reply": crisis_reply,
        "referred_12355_bool": True,
        "assessment_context": assessment_context or {},
    }

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("write_crisis_audit 文件写入失败: %s", str(e))
        return None
    # DB 镜像双写（best-effort，失败仅 warning，绝不阻断主业务）
    _db_write_audit("crisis", payload)
    return path


def write_report_audit(
    session_id: str | None,
    account_id: str | None,
    has_crisis: bool,
    scores_dict: dict,
    file_size_bytes: int,
    narrative_len: int,
    extra: dict | None = None,
) -> str | None:
    """写入一次报告生成的审计 JSON 文件。

    文件名格式：report_{sid}_{YYYYMMDD-HHMMSS-fff}.json
    返回写入的文件绝对路径；失败返回 None。
    """
    sid = session_id or "NOSESS"
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    path = os.path.join(ensure_logs_dir(), f"report_{sid}_{ts}.json")

    payload = {
        "ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
        "session_id": session_id,
        "account_id": account_id,
        "has_crisis": has_crisis,
        "scores": scores_dict,
        "file_size_bytes": file_size_bytes,
        "narrative_len": narrative_len,
    }
    if extra:
        payload.update(extra)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("write_report_audit 文件写入失败: %s", str(e))
        return None
    # DB 镜像双写（best-effort，失败仅 warning，绝不阻断主业务）
    _db_write_audit("report", payload)
    return path
