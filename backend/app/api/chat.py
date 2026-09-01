"""开放对话端点：危机前置扫描 + RAG 知识增强 + LLM 回复。

POST /api/chat  {message, history?} -> {reply, sources, crisis}
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.llm import provider
from app.core.safety import crisis_message, detect_crisis
from app.rag.service import rag_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)


SYSTEM_PROMPT = (
    "你是 PsycheFlow 校园心理陪伴助手，面向青少年学生。"
    "你温暖、共情、不评判，擅长倾听学业压力、情绪困扰、人际烦恼。"
    "你不是医生，不做诊断或开药；遇到自伤/自杀等严重议题，鼓励对方寻求信任的老师、家长或专业帮助。"
    "回答简洁亲和，可参考知识库中的心理科普片段。"
)


@router.post("")
async def chat(req: ChatRequest):
    # 1. 危机前置扫描：命中即兜底转介，不进 LLM
    if detect_crisis(req.message):
        return {"reply": crisis_message(), "sources": [], "crisis": True}

    # 2. RAG 检索相关心理知识片段（失败则降级为纯对话）
    try:
        sources = await rag_service.search(req.message, top_k=3)
    except Exception:
        sources = []

    context = ""
    if sources:
        context = "\n\n【知识参考】\n" + "\n".join(f"- {s['text']}" for s in sources)

    messages = [{"role": "system", "content": SYSTEM_PROMPT + context}]
    for m in req.history:
        if m.role in ("user", "assistant"):
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": req.message})

    try:
        reply = await provider.chat(role="dialog", messages=messages)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"对话生成失败: {type(e).__name__}: {e}",
        )

    return {
        "reply": reply,
        "sources": [{"text": s["text"], "source": s["source"]} for s in sources],
        "crisis": False,
    }
