"""百炼连通性测试端点。"""
from fastapi import APIRouter, HTTPException

from app.core.llm import provider

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.get("/ping")
async def ping(role: str = "report"):
    """真调百炼验证 Key + 模型可用。role: intake / dialog / report / embed。"""
    try:
        reply = await provider.ping(role=role)
        return {
            "ok": True,
            "role": role,
            "model": provider.model_for(role),
            "reply": reply,
        }
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"百炼调用失败: {type(e).__name__}: {e}",
        )
