"""FastAPI 入口。

当前接入：健康检查（步骤1）+ 百炼连通测试（步骤3）+ RAG（步骤4）+
量表/对话（步骤5）+ 会话持久化与 PDF 报告（步骤6）。
"""
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.llm import router as llm_router
from app.api.rag import router as rag_router
from app.api.scales import router as scales_router
from app.api.sessions import router as sessions_router
from app.api.admin import router as admin_router
from app.api.personas import router as personas_router
from app.api.screening import router as screening_router
from app.api.voice import router as voice_router
from app.core.config import settings
from app.db import init_db

logger = logging.getLogger("psycheflow.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()  # 幂等建表

    # ---------- Task 8 新增：RAG 知识库自动 ingest ----------
    # 如果当前知识库文档数 < 50，尝试自动批量导入 markdown 语料；
    # 任何异常只记 warning，不抛异常阻断服务启动。
    from app.rag.cli_ingest import do_ingest
    from app.rag.store import rag_store

    DEFAULT_NAMESPACE = "psycheflow_knowledge"
    try:
        if rag_store.count_docs(DEFAULT_NAMESPACE) < 50:
            logger.info("RAG 知识库文档数不足 50，触发自动 ingest...")
            await do_ingest(namespace=DEFAULT_NAMESPACE)
    except Exception as _e:
        logger.warning(
            "RAG 自动 ingest 失败: %s\n%s",
            str(_e),
            traceback.format_exc(),
        )
    # --------------------------------------------------------

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

app.include_router(auth_router)
app.include_router(llm_router)
app.include_router(rag_router)
app.include_router(scales_router)
app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(admin_router)
app.include_router(personas_router)
app.include_router(screening_router)
app.include_router(voice_router)


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
