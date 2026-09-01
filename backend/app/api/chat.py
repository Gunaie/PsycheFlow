"""开放对话端点：LangGraph 四智能体编排（分诊→测评→干预→升级）。

POST /api/chat  {message, history?, session_id?, account_id?, persona_id?}
-> {reply, sources, crisis, current_agent, agent_trace, persona_id}

向后兼容 NFR-1：旧字段 reply/sources/crisis 不变；新增 current_agent/agent_trace/
persona_id 为可选。persona_id 仅影响干预节点人格，危机升级零 LLM 不受理格影响。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.graph import graph
from app.agents.personas import get_persona
from app.api.deps import get_current_account, get_db_session
from app.core.safety import crisis_message
from app.models import ConversationTurn, User

router = APIRouter(prefix="/api/chat", tags=["chat"])

logger = logging.getLogger("psycheflow.api.chat")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    session_id: str | None = None
    account_id: str | None = None
    persona_id: str | None = None  # 多角色人格，不传=默认"暖暖"


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

    # —— 步骤 2：LangGraph 四智能体编排（triage→assessment→intervention/escalation）——
    # persona 请求级解析：未知 id 回退 default（回传 canonical id 供前端校正）
    effective_persona_id = get_persona(req.persona_id).persona_id
    initial_state = {
        "session_id": effective_session_id or "",
        "account_id": effective_account_id or "",
        "user_message": req.message,
        "history": [{"role": m.role, "content": m.content} for m in req.history],
        "persona_id": effective_persona_id,
        "agent_trace": [],
    }

    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        logger.exception("graph.ainvoke failed: %s", e)
        # 兜底：返回错误提示，仍写 assistant 轮
        err_reply = f"[服务异常] 对话编排失败，请稍后重试。({type(e).__name__})"
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
            logging.warning("write graph-error assistant ConversationTurn failed: %s", we)
            db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f"对话编排失败: {type(e).__name__}: {e}",
        )

    reply = final_state.get("final_reply") or crisis_message()
    sources = final_state.get("sources", [])
    is_crisis = final_state.get("crisis", False)
    current_agent = final_state.get("current_agent", "")
    agent_trace = final_state.get("agent_trace", [])

    # —— 步骤 3：写 assistant 轮 ConversationTurn（保留旧 crisis_hit 字段）——
    try:
        db.add(
            ConversationTurn(
                session_id=effective_session_id,
                account_id=effective_account_id,
                role="assistant",
                content=reply,
                sources_json=sources if sources else None,
                crisis_hit=is_crisis,
            )
        )
        db.commit()
    except Exception as e:
        logging.warning("write assistant ConversationTurn failed: %s", e)
        db.rollback()

    # —— 步骤 4：返回（旧字段 reply/sources/crisis 不变；新增 current_agent/agent_trace/persona_id）——
    return {
        "reply": reply,
        "sources": sources,
        "crisis": is_crisis,
        "current_agent": current_agent,
        "agent_trace": agent_trace,
        "persona_id": effective_persona_id,
    }
