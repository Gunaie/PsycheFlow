"""会话与报告端点：持久化评估记录 + 生成 PDF 报告。

POST /api/sessions                          创建匿名会话
POST /api/sessions/{id}/assessments         提交作答 → 计分 + 持久化
GET  /api/sessions/{id}                     会话 + 评估列表
POST /api/sessions/{id}/report              生成 PDF（application/pdf, inline）
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_account
from app.api.ratelimit import rate_limit
from app.db import get_db
from app.models import AssessmentRecord, Session as SessionModel, User
from app.reports.service import generate_report_pdf
from app.scales.registry import get_scale

# ===== 统一新增 import =====
from app.api.deps import get_db_session
from app.scales.registry import _REGISTRY as SCALES_REGISTRY
from fastapi.responses import StreamingResponse
from datetime import datetime
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

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
async def create_session(
    req: SessionCreate,
    db: Session = Depends(get_db),
    account: User | None = Depends(get_current_account),
):
    s = SessionModel(
        label=req.label,
        account_id=account.id if account else None,
    )
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


# ===== 原有 get_session 已移除：与新增端点 2 GET /{session_id} 路径冲突，
#       且不在 create_session / submit_assessment / generate_report 受保护列表中，
#       功能由下方 get_session_detail 覆盖。=====


@router.post("/{session_id}/report", dependencies=[Depends(rate_limit("report", limit=3, window_sec=60))])
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


# ================================================================
# 新增端点（全部追加到文件末尾）
# ================================================================

# ----------------------------------------------------------------
# 端点 1：GET /api/sessions  列表（分页倒序）
# ----------------------------------------------------------------
@router.get("", response_model_exclude_none=True)
async def list_sessions(
    cursor: str | None = None,
    page_size: int = 20,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    """会话分页列表：按 created_at 倒序，cursor 为上一页末条的 created_at ISO 字符串。"""
    # page_size 范围校验：[1, 100]，超上限 cap 到 100
    if page_size < 1:
        page_size = 1
    elif page_size > 100:
        page_size = 100

    # 匿名用户限制最多 50 条
    if account is None:
        page_size = min(page_size, 50)

    # 基础查询：倒序 + eager load assessments
    # 只返回有 assessment 记录的 session（排除纯对话 session，避免混入测评历史）
    stmt = (
        select(SessionModel)
        .order_by(desc(SessionModel.created_at))
        .options(selectinload(SessionModel.assessments))
        .where(SessionModel.assessments.any())
    )

    # 权限过滤
    if account is not None:
        # 登录用户：只能看自己的
        stmt = stmt.where(SessionModel.account_id == account.id)
    else:
        # 匿名：按 spec，返回 account_id IS NULL 的记录（即匿名会话）
        stmt = stmt.where(SessionModel.account_id.is_(None))

    # cursor 过滤（ISO timestamp → 比 cursor 更早的记录 = 下一页）
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="cursor 须为 ISO 格式时间戳")
        stmt = stmt.where(SessionModel.created_at < cursor_dt)

    # 多取一条用于判断是否存在下一页
    rows = db.execute(stmt.limit(page_size + 1)).scalars().all()

    next_cursor = None
    if len(rows) > page_size:
        # 倒序分页：cursor = 本页最后一条的 created_at（下一页 WHERE < cursor 取更早部分）
        next_cursor = rows[page_size - 1].created_at.isoformat()
        rows = rows[:page_size]

    items = []
    for s in rows:
        items.append({
            "session_id": s.id,
            "created_at": s.created_at.isoformat(),
            "label": s.label or '',
            "has_crisis": any(a.crisis_level == 'elevated' for a in s.assessments),
            "assessments": [
                {
                    "scale_id": a.scale_id,
                    "scale_name": a.scale_name,
                    "score": a.total_score,
                    "max_score": (
                        getattr(SCALES_REGISTRY[a.scale_id], 'max_score', 0)
                        if a.scale_id in SCALES_REGISTRY
                        else 0
                    ),
                    "severity": a.severity,
                }
                for a in s.assessments
            ],
        })

    return {"items": items, "next_cursor": next_cursor or None}


# ----------------------------------------------------------------
# 端点 2：GET /api/sessions/{session_id}  详情（含 answers + 权限校验）
# ----------------------------------------------------------------
@router.get("/{session_id}")
async def get_session_detail(
    session_id: str,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    """会话详情：加载 assessments + answers，附带子维度（可选）。"""
    s = db.get(SessionModel, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 权限校验：非匿名会话 → 必须是本人
    if s.account_id is not None and (account is None or account.id != s.account_id):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})

    # 构造 assessment 输出（含 answers）
    assessments_out = []
    for a in s.assessments:
        assessments_out.append({
            "id": a.id,
            "scale_id": a.scale_id,
            "scale_name": a.scale_name,
            "score": a.total_score,
            "severity": a.severity,
            "crisis_level": a.crisis_level,
            "crisis_triggers": a.crisis_triggers,
            "interpretation": a.interpretation,
            "needs_crisis_escalation": a.needs_crisis_escalation,
            "answers": a.answers,
            "created_at": a.created_at.isoformat(),
        })

    # 可选：子维度（按需调用 _compute_subdims）
    subdims = []
    try:
        from app.reports.service import _compute_subdims
        for a in assessments_out:
            subdims.append(_compute_subdims(a))
    except Exception:
        subdims = []

    return {
        "session_id": s.id,
        "created_at": s.created_at.isoformat(),
        "label": s.label or '',
        "has_crisis": any(a.crisis_level == 'elevated' for a in s.assessments),
        "account_id": s.account_id,
        "assessments": assessments_out,
        "subdims": subdims or None,
    }


# ----------------------------------------------------------------
# 端点 3：GET /api/sessions/{session_id}/report  下载 PDF（附件）
# ----------------------------------------------------------------
@router.get("/{session_id}/report")
async def download_session_report(
    session_id: str,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    """下载会话报告 PDF（attachment，非 inline）。"""
    s = db.get(SessionModel, session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 权限校验：与端点 2 一致
    if s.account_id is not None and (account is None or account.id != s.account_id):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})

    if not s.assessments:
        raise HTTPException(status_code=400, detail="会话无评估记录，无法生成报告")

    # 按 generate_report_pdf(session, assessments: list) 的既有签名组装参数
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
            "answers": a.answers,
        }
        for a in s.assessments
    ]

    try:
        pdf_bytes = await generate_report_pdf(s, assessments)
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"报告生成失败: {type(e).__name__}: {e}"
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="psycheflow-report-{session_id}.pdf"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
