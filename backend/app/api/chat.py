"""开放对话端点：危机前置扫描 + RAG 知识增强 + LLM 回复。

POST /api/chat  {message, history?, session_id?, account_id?} -> {reply, sources, crisis}
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_account, get_db_session
from app.core.llm import provider
from app.core.safety import crisis_message, detect_crisis, detect_crisis_with_words
from app.models import ConversationTurn, User
from app.rag.service import rag_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    session_id: str | None = None
    account_id: str | None = None


SYSTEM_PROMPT = (
    "你是 PsycheFlow 校园心理陪伴助手，面向青少年学生。"
    "你温暖、共情、不评判，擅长倾听学业压力、情绪困扰、人际烦恼。"
    "你不是医生，不做诊断或开药；遇到自伤/自杀等严重议题，鼓励对方寻求信任的老师、家长或专业帮助。"
    "回答简洁亲和，可参考知识库中的心理科普片段。"
)


@router.post("")
async def chat(
    req: ChatRequest,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    # —— 有效 ID 解析：Bearer 账号 > body 账号 ——
    effective_account_id = (account.id if account else None) or req.account_id
    effective_session_id = req.session_id

    # —— 步骤 1：先写 user 轮 ConversationTurn（失败不影响接口返回）——
    try:
        db.add(
            ConversationTurn(
                session_id=effective_session_id,
                account_id=effective_account_id,
                role="user",
                content=req.message,
                sources_json=None,
                crisis_hit=False,
            )
        )
        db.commit()
    except Exception as e:
        logging.warning("write user ConversationTurn failed: %s", e)
        db.rollback()

    # 1. 危机前置扫描：命中即兜底转介，不进 LLM
    crisis_hit, trigger_words = detect_crisis_with_words(req.message)
    if crisis_hit:
        # —— 审计日志：危机命中 ——（不阻断返回，try/except 包死）
        try:
            from app.core.audit import write_crisis_audit

            reply_crisis = crisis_message()
            write_crisis_audit(
                effective_session_id,
                effective_account_id,
                trigger_words,
                req.message,
                reply_crisis,
            )
        except Exception:
            reply_crisis = crisis_message()
        # —— 步骤 3a：危机分支写 assistant 轮 ——
        try:
            db.add(
                ConversationTurn(
                    session_id=effective_session_id,
                    account_id=effective_account_id,
                    role="assistant",
                    content=reply_crisis,
                    sources_json=None,
                    crisis_hit=True,
                )
            )
            db.commit()
        except Exception as e:
            logging.warning("write crisis assistant ConversationTurn failed: %s", e)
            db.rollback()
        return {
            "reply": reply_crisis,
            "sources": [],
            "crisis": True,
        }

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
        # LLM 失败也要写 assistant 轮（记录失败），尽量留痕
        err_reply = f"[服务异常] 对话生成失败，请稍后重试。({type(e).__name__})"
        try:
            db.add(
                ConversationTurn(
                    session_id=effective_session_id,
                    account_id=effective_account_id,
                    role="assistant",
                    content=err_reply,
                    sources_json=None,
                    crisis_hit=False,
                )
            )
            db.commit()
        except Exception as we:
            logging.warning("write llm-error assistant ConversationTurn failed: %s", we)
            db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"对话生成失败: {type(e).__name__}: {e}",
        )

    formatted_sources = [{"text": s["text"], "source": s["source"]} for s in sources]

    # —— 步骤 3b：正常分支写 assistant 轮 ——
    try:
        db.add(
            ConversationTurn(
                session_id=effective_session_id,
                account_id=effective_account_id,
                role="assistant",
                content=reply,
                sources_json=formatted_sources if formatted_sources else None,
                crisis_hit=False,
            )
        )
        db.commit()
    except Exception as e:
        logging.warning("write normal assistant ConversationTurn failed: %s", e)
        db.rollback()

    return {
        "reply": reply,
        "sources": formatted_sources,
        "crisis": False,
    }
