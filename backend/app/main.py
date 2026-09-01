"""FastAPI 入口。

当前接入：健康检查（步骤1）+ 百炼连通测试（步骤3）+ RAG（步骤4）+
量表/对话（步骤5）+ 会话持久化与 PDF 报告（步骤6）。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.llm import router as llm_router
from app.api.rag import router as rag_router
from app.api.scales import router as scales_router
from app.api.sessions import router as sessions_router
from app.core.config import settings
from app.db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()  # 幂等建表
    yield


app = FastAPI(
    title="PsycheFlow",
    version="0.1.0",
    description="智能心理评估系统（青少年校园心理筛查辅助）",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(llm_router)
app.include_router(rag_router)
app.include_router(scales_router)
app.include_router(chat_router)
app.include_router(sessions_router)


@app.get("/api/health")
async def health() -> dict:
    """健康检查 + 配置自检（不泄露密钥）。"""
    return {
        "status": "ok",
        "service": "PsycheFlow",
        "version": "0.1.0",
        "bailian_configured": settings.dashscope_api_key not in ("", "your-api-key-here") and len(settings.dashscope_api_key) >= 20,
        "models": {
            "intake": settings.model_intake,
            "dialog": settings.model_dialog,
            "report": settings.model_report,
            "embed": settings.model_embed,
        },
    }
