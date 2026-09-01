"""会话与报告端点：持久化评估记录 + 生成 PDF 报告。

POST /api/sessions                          创建匿名会话
POST /api/sessions/{id}/assessments         提交作答 → 计分 + 持久化
GET  /api/sessions/{id}                     会话 + 评估列表
POST /api/sessions/{id}/report              生成 PDF（application/pdf, inline）
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AssessmentRecord, Session as SessionModel
from app.reports.service import generate_report_pdf
from app.scales.registry import get_scale

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    label: str | None = None


class AssessmentSubmit(BaseModel):
    scale_id: str
    answers: dict


def _to_int_answers(answers: dict) -> dict:
    try:
        return {int(k): v for k, v in answers.items()}
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="answers 的键须为题号(数字)")


@router.post("")
async def create_session(req: SessionCreate, db: Session = Depends(get_db)):
    s = SessionModel(label=req.label)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"session_id": s.id, "label": s.label, "created_at": s.created_at.isoformat()}


@router.post("/{session_id}/assessments")
async def submit_assessment(session_id: str, req: AssessmentSubmit, db: Session = Depends(get_db)):
    session = db.get(SessionModel, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        scale = get_scale(req.scale_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"未知量表: {req.scale_id}")

    answers = _to_int_answers(req.answers)
    try:
        result = scale.score(answers)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    rec = AssessmentRecord(
        session_id=session_id,
        scale_id=result.scale_id,
        scale_name=result.scale_name,
        total_score=result.total_score,
        severity=result.severity.value,
        crisis_level=result.crisis_level.value,
        crisis_triggers=result.crisis_triggers,
        interpretation=result.interpretation,
        answers={str(k): v for k, v in answers.items()},
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    out = result.to_dict()
    out["assessment_id"] = rec.id
    return out


@router.get("/{session_id}")
async def get_session(session_id: str, db: Session = Depends(get_db)):
    session = db.get(SessionModel, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        "session_id": session.id,
        "label": session.label,
        "created_at": session.created_at.isoformat(),
        "assessments": [
            {
                "assessment_id": a.id,
                "scale_id": a.scale_id,
                "scale_name": a.scale_name,
                "total_score": a.total_score,
                "severity": a.severity,
                "crisis_level": a.crisis_level,
                "crisis_triggers": a.crisis_triggers,
                "interpretation": a.interpretation,
                "needs_crisis_escalation": a.needs_crisis_escalation,
                "created_at": a.created_at.isoformat(),
            }
            for a in session.assessments
        ],
    }


@router.post("/{session_id}/report")
async def generate_report(session_id: str, db: Session = Depends(get_db)):
    session = db.get(SessionModel, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not session.assessments:
        raise HTTPException(status_code=400, detail="会话无评估记录，无法生成报告")

    assessments = [
        {
            "scale_id": a.scale_id,
            "scale_name": a.scale_name,
            "total_score": a.total_score,
            "severity": a.severity,
            "crisis_level": a.crisis_level,
            "crisis_triggers": a.crisis_triggers,
            "interpretation": a.interpretation,
            "needs_crisis_escalation": a.needs_crisis_escalation,
            "answers": a.answers,  # {str(qid): int(val)} 供子维度重算
        }
        for a in session.assessments
    ]
    try:
        pdf = await generate_report_pdf(session, assessments)
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"报告生成失败: {type(e).__name__}: {e}"
        )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="psycheflow_report_{session_id[:8]}.pdf"'
        },
    )
