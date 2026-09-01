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
        return path
    except Exception as e:
        logger.warning("write_crisis_audit 失败: %s", str(e))
        return None


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
        return path
    except Exception as e:
        logger.warning("write_report_audit 失败: %s", str(e))
        return None
