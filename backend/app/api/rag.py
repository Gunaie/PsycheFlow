"""RAG 索引构建与检索端点。"""
from fastapi import APIRouter, HTTPException, Query

from app.rag.service import rag_service

router = APIRouter(prefix="/api/rag", tags=["rag"])


@router.post("/build")
async def build_index():
    """构建知识库索引：读 data/knowledge/*.txt → 百炼 embed → 写入 Chroma。"""
    try:
        return await rag_service.build_index()
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"索引构建失败: {type(e).__name__}: {e}",
        )


@router.get("/search")
async def search(q: str = Query(..., description="检索词"), top_k: int = 3):
    """检索相关心理学知识片段。"""
    try:
        return {"query": q, "results": await rag_service.search(q, top_k=top_k)}
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"检索失败: {type(e).__name__}: {e}",
        )
