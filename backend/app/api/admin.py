"""B 端管理后台端点（仅 role=teacher，get_current_teacher 守卫）。

POST /api/admin/batches                                创建批次（CSV 名单 → 生成筛查码）
GET  /api/admin/batches                                教师批次列表（含进度）
GET  /api/admin/batches/{batch_id}                     批次详情 + 聚合统计（零 LLM）
POST /api/admin/batches/{batch_id}/close               关闭批次（筛查码失效）
GET  /api/admin/batches/{batch_id}/export              批次汇总 CSV 导出
GET  /api/admin/batches/{batch_id}/entries/{entry_id}/report   单个学生 PDF 报告
"""
import csv
import io
import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_teacher
from app.db import get_db
from app.models import BatchEntry, ScreeningBatch, Session as SessionModel, User
from app.reports.service import generate_report_pdf
from app.scales.registry import _REGISTRY as SCALES_REGISTRY

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 筛查码字符集：去掉易混淆的 0/O/1/I
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_MAX_ROSTER = 500

# CSV 表头别名 → 标准字段（中英文都支持）
_CSV_ALIASES = {
    "student_no": "student_no",
    "学号": "student_no",
    "student_name": "student_name",
    "姓名": "student_name",
    "name": "student_name",
    "grade": "grade",
    "年级": "grade",
    "klass": "klass",
    "class": "klass",
    "班级": "klass",
}


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scale_ids: list[str] = Field(min_length=1)
    roster_csv: str = Field(min_length=1)


def _parse_roster(roster_csv: str) -> list[dict]:
    """解析教师上传的名单 CSV（支持 UTF-8 BOM；表头中英文均可）。

    必填列：学号、姓名；可选列：年级、班级。返回行号错误列表 + 行字典列表。
    """
    text = roster_csv.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV 为空或无表头")

    col_map: dict[str, str] = {}
    for raw in reader.fieldnames:
        key = (raw or "").strip().lower()
        std = _CSV_ALIASES.get(key) or _CSV_ALIASES.get((raw or "").strip())
        if std:
            col_map[raw] = std

    missing_cols = [c for c in ("student_no", "student_name") if c not in col_map.values()]
    if missing_cols:
        raise HTTPException(
            status_code=422,
            detail=f"CSV 缺少必填列（学号/姓名）。识别到的表头：{reader.fieldnames}",
        )

    rows: list[dict] = []
    errors: list[str] = []
    for i, raw_row in enumerate(reader, start=2):  # 表头占第 1 行
        row = {}
        for raw_key, std_key in col_map.items():
            val = (raw_row.get(raw_key) or "").strip()
            row[std_key] = val or None
        if not row.get("student_no") or not row.get("student_name"):
            errors.append(f"第 {i} 行：学号或姓名为空")
            continue
        rows.append(row)

    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors[:10]))
    if not rows:
        raise HTTPException(status_code=422, detail="CSV 无有效数据行")
    if len(rows) > _MAX_ROSTER:
        raise HTTPException(status_code=422, detail=f"名单上限 {_MAX_ROSTER} 人，当前 {len(rows)} 人")

    # 学号去重校验
    seen: set[str] = set()
    for i, r in enumerate(rows, start=2):
        if r["student_no"] in seen:
            raise HTTPException(status_code=422, detail=f"第 {i} 行：学号 {r['student_no']} 重复")
        seen.add(r["student_no"])
    return rows


def _gen_entry_code(db: Session) -> str:
    """生成 6 位唯一筛查码，冲突重试（62^6 空间，冲突概率极低）。"""
    for _ in range(20):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        exists = db.execute(
            select(BatchEntry.id).where(BatchEntry.entry_code == code)
        ).scalar_one_or_none()
        if not exists:
            return code
    raise HTTPException(status_code=500, detail="筛查码生成失败，请重试")


def _load_own_batch(db: Session, teacher: User, batch_id: str) -> ScreeningBatch:
    """加载教师自己的批次；不存在或非本人 → 404（不泄露他人批次存在性）。"""
    batch = db.get(ScreeningBatch, batch_id)
    if not batch or batch.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


# ---------------------------------------------------------------------------
# 创建批次
# ---------------------------------------------------------------------------

@router.post("/batches")
async def create_batch(
    req: BatchCreate,
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    # 量表合法性校验
    for sid in req.scale_ids:
        if sid not in SCALES_REGISTRY:
            raise HTTPException(status_code=422, detail=f"未知量表: {sid}")

    rows = _parse_roster(req.roster_csv)

    batch = ScreeningBatch(name=req.name.strip(), teacher_id=teacher.id, scale_ids=req.scale_ids)
    db.add(batch)
    db.flush()  # 拿到 batch.id

    entries = []
    for r in rows:
        e = BatchEntry(
            batch_id=batch.id,
            student_no=r["student_no"],
            student_name=r["student_name"],
            grade=r.get("grade"),
            klass=r.get("klass"),
            entry_code=_gen_entry_code(db),
        )
        db.add(e)
        entries.append(e)
    db.commit()
    db.refresh(batch)

    return {
        "batch_id": batch.id,
        "name": batch.name,
        "scale_ids": batch.scale_ids,
        "total": len(entries),
        "entries": [
            {
                "entry_id": e.id,
                "student_no": e.student_no,
                "student_name": e.student_name,
                "grade": e.grade,
                "klass": e.klass,
                "entry_code": e.entry_code,
                "status": e.status,
            }
            for e in entries
        ],
    }


# ---------------------------------------------------------------------------
# 批次列表（含进度）
# ---------------------------------------------------------------------------

@router.get("/batches")
async def list_batches(
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    stmt = (
        select(ScreeningBatch)
        .where(ScreeningBatch.teacher_id == teacher.id)
        .options(selectinload(ScreeningBatch.entries))
        .order_by(ScreeningBatch.created_at.desc())
    )
    batches = db.execute(stmt).scalars().all()
    return {
        "items": [
            {
                "batch_id": b.id,
                "name": b.name,
                "scale_ids": b.scale_ids,
                "status": b.status,
                "total": len(b.entries),
                "completed": sum(1 for e in b.entries if e.status == "completed"),
                "created_at": b.created_at.isoformat(),
            }
            for b in batches
        ]
    }


# ---------------------------------------------------------------------------
# 聚合统计（纯 SQL/Python，零 LLM）
# ---------------------------------------------------------------------------

def _batch_stats(batch: ScreeningBatch) -> dict:
    entries = batch.entries
    total = len(entries)
    completed_entries = [e for e in entries if e.status == "completed"]

    # 每量表 severity 分布 + 危机名单：基于批次内全部 AssessmentRecord
    severity_dist: dict[str, dict[str, int]] = {sid: {} for sid in batch.scale_ids}
    crisis_list: list[dict] = []
    entry_by_session = {e.session_id: e for e in completed_entries if e.session_id}

    for e in completed_entries:
        if not e.session:
            continue
        for a in e.session.assessments:
            dist = severity_dist.setdefault(a.scale_id, {})
            dist[a.severity] = dist.get(a.severity, 0) + 1
            if a.crisis_level == "elevated":
                crisis_list.append({
                    "entry_id": e.id,
                    "student_no": e.student_no,
                    "student_name": e.student_name,
                    "grade": e.grade,
                    "klass": e.klass,
                    "scale_id": a.scale_id,
                    "scale_name": a.scale_name,
                    "total_score": a.total_score,
                    "severity": a.severity,
                    "crisis_triggers": a.crisis_triggers,
                    "session_id": a.session_id,
                })

    # 按班级分组进度（grade+klass 组合）
    by_class: dict[str, dict[str, int]] = {}
    for e in entries:
        key = " / ".join(x for x in [e.grade or "", e.klass or ""] if x) or "未分组"
        g = by_class.setdefault(key, {"total": 0, "completed": 0})
        g["total"] += 1
        if e.status == "completed":
            g["completed"] += 1

    return {
        "total": total,
        "completed": len(completed_entries),
        "pending": total - len(completed_entries),
        "severity_distribution": severity_dist,
        "crisis_count": len(crisis_list),
        "crisis_list": crisis_list,
        "by_class": by_class,
    }


@router.get("/batches/{batch_id}")
async def batch_detail(
    batch_id: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    batch = _load_own_batch(db, teacher, batch_id)
    # eager load entries + sessions + assessments（一条主查询 + 关系加载）
    db.refresh(batch)
    entries = (
        db.execute(
            select(BatchEntry)
            .where(BatchEntry.batch_id == batch.id)
            .options(selectinload(BatchEntry.session).selectinload(SessionModel.assessments))
            .order_by(BatchEntry.student_no)
        )
        .scalars()
        .all()
    )

    stats = _batch_stats(batch)

    return {
        "batch_id": batch.id,
        "name": batch.name,
        "scale_ids": batch.scale_ids,
        "status": batch.status,
        "created_at": batch.created_at.isoformat(),
        **stats,
        "entries": [
            {
                "entry_id": e.id,
                "student_no": e.student_no,
                "student_name": e.student_name,
                "grade": e.grade,
                "klass": e.klass,
                "entry_code": e.entry_code,
                "status": e.status,
                "completed_at": e.completed_at.isoformat() if e.completed_at else None,
                "assessments": [
                    {
                        "scale_id": a.scale_id,
                        "scale_name": a.scale_name,
                        "total_score": a.total_score,
                        "severity": a.severity,
                        "crisis_level": a.crisis_level,
                    }
                    for a in (e.session.assessments if e.session else [])
                ],
            }
            for e in entries
        ],
    }


# ---------------------------------------------------------------------------
# 关闭批次
# ---------------------------------------------------------------------------

@router.post("/batches/{batch_id}/close")
async def close_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    batch = _load_own_batch(db, teacher, batch_id)
    batch.status = "closed"
    db.commit()
    return {"batch_id": batch.id, "status": "closed"}


# ---------------------------------------------------------------------------
# 重新打开批次（与 close 对称：关闭后可再激活，筛查码恢复有效）
# ---------------------------------------------------------------------------

@router.post("/batches/{batch_id}/reopen")
async def reopen_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    batch = _load_own_batch(db, teacher, batch_id)
    if batch.status != "closed":
        raise HTTPException(status_code=400, detail="批次当前非关闭状态，无需重新打开")
    batch.status = "active"
    db.commit()
    return {"batch_id": batch.id, "status": "active"}


# ---------------------------------------------------------------------------
# 重命名批次（PATCH name）
# ---------------------------------------------------------------------------

class BatchUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


@router.patch("/batches/{batch_id}")
async def update_batch(
    batch_id: str,
    req: BatchUpdate,
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    batch = _load_own_batch(db, teacher, batch_id)
    batch.name = req.name.strip()
    db.commit()
    return {"batch_id": batch.id, "name": batch.name}


# ---------------------------------------------------------------------------
# 删除批次（级联删除学生条目；BatchEntry.session_id 为 SET NULL，
# 学生已完成的测评 session 不会被删，但会脱离批次关联）
# ---------------------------------------------------------------------------

@router.delete("/batches/{batch_id}")
async def delete_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    batch = _load_own_batch(db, teacher, batch_id)
    entries_count = len(batch.entries)
    db.delete(batch)  # cascade="all, delete-orphan" 自动删 entries
    db.commit()
    return {"batch_id": batch_id, "deleted_entries": entries_count}


# ---------------------------------------------------------------------------
# 批次汇总 CSV 导出
# ---------------------------------------------------------------------------

_SEVERITY_ZH = {
    "none": "无", "minimal": " minimal", "mild": "轻度", "moderate": "中度",
    "moderately_severe": "中重度", "severe": "重度",
}


@router.get("/batches/{batch_id}/export")
async def export_batch_csv(
    batch_id: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    batch = _load_own_batch(db, teacher, batch_id)
    entries = (
        db.execute(
            select(BatchEntry)
            .where(BatchEntry.batch_id == batch.id)
            .options(selectinload(BatchEntry.session).selectinload(SessionModel.assessments))
            .order_by(BatchEntry.student_no)
        )
        .scalars()
        .all()
    )

    # 动态表头：基础列 + 每量表（总分/严重度）
    header = ["学号", "姓名", "年级", "班级", "筛查码", "状态", "完成时间"]
    scale_ids = batch.scale_ids
    for sid in scale_ids:
        sname = SCALES_REGISTRY[sid].scale_name if sid in SCALES_REGISTRY else sid
        header.append(f"{sname}总分")
        header.append(f"{sname}严重度")
    header.append("危机标记")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for e in entries:
        row = [
            e.student_no, e.student_name, e.grade or "", e.klass or "",
            e.entry_code,
            "已完成" if e.status == "completed" else "未完成",
            e.completed_at.strftime("%Y-%m-%d %H:%M") if e.completed_at else "",
        ]
        score_map = {a.scale_id: a for a in (e.session.assessments if e.session else [])}
        for sid in scale_ids:
            a = score_map.get(sid)
            row.append(a.total_score if a else "")
            row.append(_SEVERITY_ZH.get(a.severity, a.severity) if a else "")
        row.append("是" if any(a.crisis_level == "elevated" for a in score_map.values()) else "否")
        writer.writerow(row)

    # UTF-8 BOM：Excel 直接打开不乱码
    content = "\ufeff" + buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="batch_{batch.id[:8]}_summary.csv"',
        },
    )


# ---------------------------------------------------------------------------
# 单个学生 PDF 报告（复用 WeasyPrint 生成链路）
# ---------------------------------------------------------------------------

@router.get("/batches/{batch_id}/entries/{entry_id}/report")
async def entry_report(
    batch_id: str,
    entry_id: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(get_current_teacher),
):
    batch = _load_own_batch(db, teacher, batch_id)
    entry = db.get(BatchEntry, entry_id)
    if not entry or entry.batch_id != batch.id:
        raise HTTPException(status_code=404, detail="学生条目不存在")
    if not entry.session or not entry.session.assessments:
        raise HTTPException(status_code=400, detail="该学生尚未完成测评，无法生成报告")

    session = entry.session
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
        for a in session.assessments
    ]
    try:
        pdf = await generate_report_pdf(session, assessments)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"报告生成失败: {type(e).__name__}: {e}")

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="report_{entry.student_no}.pdf"',
        },
    )
