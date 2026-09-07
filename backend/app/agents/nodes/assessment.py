"""Assessment 测评节点：纯 DB 查询，无 LLM 调用。

查最近一条 AssessmentRecord 的 severity/crisis_level/total_score 作为上下文，
供 Intervention 节点使用（SAFETY_BASELINE 第 9 条：重度用户回复更谨慎）。

查询顺序：
1. 按 session_id 查（测评 session 内对话场景）
2. 查不到且用户已登录（account_id 非空）→ 跨 session 按账号查最近一条测评
   （对话用独立 chat session，测评记录挂在测评 session 上，必须按账号回退，
   否则登录用户的测评上下文永远缺失）
"""
import logging

from sqlalchemy import select

from app.agents.state import AgentState
from app.db import SessionLocal
from app.models import AssessmentRecord, Session as SessionModel

logger = logging.getLogger("psycheflow.agents.assessment")


def _context_from_record(record: AssessmentRecord) -> dict:
    return {
        "scale_id": record.scale_id,
        "scale_name": record.scale_name,
        "severity": record.severity,
        "crisis_level": record.crisis_level,
        "total_score": record.total_score,
        "crisis_triggers": record.crisis_triggers or [],
    }


async def assessment_node(state: AgentState) -> dict:
    """测评节点：纯 DB 查询，不调 LLM。

    流程：
    1. 查 session_id 对应测评记录（按 created_at DESC 取最近 1 条）
    2. 无记录 + 已登录 → 跨 session 查该账号最近 1 条测评记录
    3. 都无 → has_assessment=false，assessment_context={}
    """
    sid = state.get("session_id")
    account_id = state.get("account_id")
    trace = state.get("agent_trace", []) + ["assessment"]
    decisions = state.get("node_decisions", {})

    has_assessment = False
    assessment_context: dict = {}

    try:
        db = SessionLocal()
        try:
            record = None
            # 1. 按当前 session 查（测评会话内对话场景）
            if sid:
                record = db.execute(
                    select(AssessmentRecord)
                    .where(AssessmentRecord.session_id == sid)
                    .order_by(AssessmentRecord.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()

            # 2. 回退：登录用户跨 session 查账号最近一条测评
            if record is None and account_id:
                record = db.execute(
                    select(AssessmentRecord)
                    .join(SessionModel, AssessmentRecord.session_id == SessionModel.id)
                    .where(SessionModel.account_id == account_id)
                    .order_by(AssessmentRecord.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if record is not None:
                    logger.info(
                        "assessment: fallback by account=%s, found scale=%s severity=%s",
                        account_id, record.scale_id, record.severity,
                    )

            if record is not None:
                has_assessment = True
                assessment_context = _context_from_record(record)
                logger.info(
                    "assessment: found record scale=%s severity=%s",
                    record.scale_id, record.severity,
                )
                decisions["assessment"] = {
                    "decision": "record_found",
                    "scale": record.scale_id,
                    "severity": record.severity,
                }
            else:
                logger.info("assessment: no record (sid=%s, account=%s)", sid, account_id)
                decisions["assessment"] = {
                    "decision": "no_record",
                    "reason": "no_assessment_in_db",
                }
        finally:
            db.close()
    except Exception as e:
        logger.warning("assessment: db query failed: %s", str(e))
        decisions["assessment"] = {
            "decision": "error",
            "reason": str(e),
        }

    return {
        "has_assessment": has_assessment,
        "assessment_context": assessment_context,
        "current_agent": "assessment",
        "agent_trace": trace,
        "node_decisions": decisions
    }
