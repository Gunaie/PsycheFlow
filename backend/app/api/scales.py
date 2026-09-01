"""量表发现与计分端点。

GET /api/scales            列出可用量表
GET /api/scales/{id}       返回量表元数据（题项 + 选项），供前端渲染表单
POST /api/scales/{id}/score  提交作答 {answers:{题号:分值}}，返回 ScoreResult
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.scales.registry import get_scale, list_scales

router = APIRouter(prefix="/api/scales", tags=["scales"])


class ScoreRequest(BaseModel):
    answers: dict


@router.get("")
async def list_all():
    return [
        {
            "scale_id": s.scale_id,
            "scale_name": s.scale_name,
            "description": s.description,
            "item_count": len(s.items),
        }
        for s in list_scales()
    ]


@router.get("/{scale_id}")
async def get_one(scale_id: str):
    try:
        return get_scale(scale_id).meta()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"未知量表: {scale_id}")


@router.post("/{scale_id}/score")
async def score(scale_id: str, req: ScoreRequest):
    try:
        scale = get_scale(scale_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"未知量表: {scale_id}")

    # JSON 键为字符串，量表计分按整型题号查 → 统一转 int
    try:
        answers = {int(k): v for k, v in req.answers.items()}
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="answers 的键须为题号(数字)")

    try:
        result = scale.score(answers)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return result.to_dict()
