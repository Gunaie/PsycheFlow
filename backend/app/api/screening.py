"""学生筛查入口端点（C 端，凭 6 位筛查码匿名进入，零登录）。

GET  /api/screening/{code}            筛查码校验 → 返回批次与量表信息
POST /api/screening/{code}/submit     提交全部量表作答 → 计分 + 持久化 + 条目完成

计分复用自研规则引擎（与 /api/scales/{id}/score 同源），危机判定为规则产物，
全程零 LLM。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AssessmentRecord, BatchEntry, ScreeningBatch, Session as SessionModel
from app.scales.registry import _REGISTRY as SCALES_REGISTRY

router = APIRouter(prefix="/api/screening", tags=["screening"])


def _load_entry(db: Session, code: str) -> tuple[BatchEntry, ScreeningBatch]:
    """按筛查码加载条目；无效码 404 / 批次关闭 410 / 已完成 409。"""
    code = (code or "").strip().upper()
    entry = db.execute(
        select(BatchEntry).where(BatchEntry.entry_code == code)
    ).scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="筛查码无效")
    batch = db.get(ScreeningBatch, entry.batch_id)
    if not batch or batch.status != "active":
        raise HTTPException(status_code=410, detail="该筛查批次已关闭")
    return entry, batch


class ScreeningInfo(BaseModel):
    batch_name: str
    scale_ids: list[str]
    status: str


class ScreeningSubmit(BaseModel):
    """一次提交本批次全部量表的作答：{scale_id: {题号: 分值}}。"""
    answers: dict[str, dict] = Field(min_length=1)


@router.get("/{code}")
async def screening_info(code: str, db: Session = Depends(get_db)):
    entry, batch = _load_entry(db, code)
    return {
        "batch_name": batch.name,
        "scale_ids": batch.scale_ids,
        "status": entry.status,
        "completed_at": entry.completed_at.isoformat() if entry.completed_at else None,
    }


@router.post("/{code}/submit")
async def screening_submit(code: str, req: ScreeningSubmit, db: Session = Depends(get_db)):
    entry, batch = _load_entry(db, code)

    # 已完成不可重复提交（幂等防刷）
    if entry.status == "completed":
        raise HTTPException(status_code=409, detail="该筛查码已完成测评，请勿重复提交")

    # 校验：本批次全部量表都必须提交
    submitted = set(req.answers.keys())
    expected = set(batch.scale_ids)
    if submitted != expected:
        missing = expected - submitted
        extra = submitted - expected
        detail = []
        if missing:
            detail.append(f"缺少量表: {sorted(missing)}")
        if extra:
            detail.append(f"未知量表: {sorted(extra)}")
        raise HTTPException(status_code=422, detail="；".join(detail))

    # 逐量表规则计分（任何异常 → 422，不落库）
    scored = []
    for sid in batch.scale_ids:
        scale = SCALES_REGISTRY[sid]
        raw = req.answers[sid]
        try:
            answers = {int(k): v for k, v in raw.items()}
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail=f"{sid} 的答案键须为题号(数字)")
        try:
            result = scale.score(answers)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"{scale.scale_name}: {e}")
        scored.append((result, answers))

    # 持久化：1 个会话 + N 条评估记录 + 条目完成标记（同事务）
    session = SessionModel(label=f"批量筛查-{batch.name}")
    db.add(session)
    db.flush()

    for result, answers in scored:
        db.add(AssessmentRecord(
            session_id=session.id,
            scale_id=result.scale_id,
            scale_name=result.scale_name,
            total_score=result.total_score,
            severity=result.severity.value,
            crisis_level=result.crisis_level.value,
            crisis_triggers=result.crisis_triggers,
            interpretation=result.interpretation,
            answers={str(k): v for k, v in answers.items()},
        ))

    entry.session_id = session.id
    entry.status = "completed"
    entry.completed_at = datetime.utcnow()
    db.commit()

    return {
        "results": [result.to_dict() for result, _ in scored],
        "has_crisis": any(r.crisis_level.value == "elevated" for r, _ in scored),
    }
