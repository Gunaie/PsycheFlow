"""人格列表端点：供前端渲染角色选择器。

GET /api/personas -> [{persona_id, name, avatar, description}]
"""
from fastapi import APIRouter

from app.agents.personas import list_personas

router = APIRouter(prefix="/api/personas", tags=["personas"])


@router.get("")
async def get_personas() -> list[dict]:
    """列出全部可用人格（default/sister/senior/listener）。"""
    return list_personas()
