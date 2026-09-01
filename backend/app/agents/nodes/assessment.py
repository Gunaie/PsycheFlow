"""Assessment 测评节点：纯 DB 查询，无 LLM 调用。

查 Session.assessments 是否已填量表，提取最近一条 AssessmentRecord 的
severity/crisis_level/total_score 作为上下文，供 Intervention 节点使用。
"""
import logging

from sqlalchemy import select

from app.agents.state import AgentState
from app.db import SessionLocal
from app.models import AssessmentRecord

logger = logging.getLogger("psycheflow.agents.assessment")


async def assessment_node(state: AgentState) -> dict:
    """测评节点：纯 DB 查询，不调 LLM。

    流程：
    1. 查 session_id 对应 Session.assessments（按 created_at DESC 取最近 1 条）
    2. 有 → has_assessment=true + assessment_context 提取
    3. 无 → has_assessment=false，assessment_context={}
    """
    sid = state.get("session_id")
    trace = state.get("agent_trace", []) + ["assessment"]

    has_assessment = False
    assessment_context: dict = {}

    if sid:
        try:
            db = SessionLocal()
            try:
                # 取最近一条 AssessmentRecord
                stmt = (
                    select(AssessmentRecord)
                    .where(AssessmentRecord.session_id == sid)
                    .order_by(AssessmentRecord.created_at.desc())
                    .limit(1)
                )
                record = db.execute(stmt).scalar_one_or_none()
                if record:
                    has_assessment = True
                    assessment_context = {
                        "scale_id": record.scale_id,
                        "scale_name": record.scale_name,
                        "severity": record.severity,
                        "crisis_level": record.crisis_level,
                        "total_score": record.total_score,
                        "crisis_triggers": record.crisis_triggers or [],
                    }
                    logger.info(
                        "assessment: found record scale=%s severity=%s",
                        record.scale_id, record.severity,
                    )
                else:
                    logger.info("assessment: no record for session %s", sid)
            finally:
                db.close()
        except Exception as e:
            logger.warning("assessment: db query failed: %s", str(e))

    return {
        "has_assessment": has_assessment,
        "assessment_context": assessment_context,
        "current_agent": "assessment",
        "agent_trace": trace,
    }
