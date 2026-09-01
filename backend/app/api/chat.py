"""开放对话端点：LangGraph 四智能体编排（分诊→测评→干预→升级）。

POST /api/chat         非流式（向后兼容 NFR-1，旧客户端不变）
POST /api/chat/stream  SSE 流式（NFR-5 首 token 优化，边生成边推）

向后兼容 NFR-1：旧字段 reply/sources/crisis 不变；新增 current_agent/agent_trace/
persona_id 为可选。persona_id 仅影响干预节点人格，危机升级零 LLM 不受理格影响。
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.graph import graph
from app.agents.nodes.assessment import assessment_node
from app.agents.nodes.escalation import escalation_node
from app.agents.nodes.intervention import (
    FALLBACK_REPLY,
    build_intervention_messages,
    stream_intervention,
)
from app.agents.nodes.triage import triage_node
from app.agents.personas import get_persona
from app.api.deps import get_current_account, get_db_session
from app.core.llm import provider
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


def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 事件（event + data 两行，以空行结尾）。

    ensure_ascii=False 保证中文 token 不被 \\uXXXX 转义，前端可直接拼接显示。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    """SSE 流式对话端点（NFR-5 首 token 优化）。

    架构 Option C：手动跑 triage→assessment（同步等结果），再用 provider.stream()
    边生成边推 token。危机路径不流式，推完整 crisis_message 后 close。

    SSE 事件格式（event: <type>\\ndata: <json>\\n\\n）：
    - agent   {agent, agent_trace}        节点切换通知（前端更新 stepper）
    - sources {sources}                  RAG 知识卡片（intervention 前推，提前渲染）
    - token   {token}                     流式 token（仅非危机路径）
    - crisis  {reply, agent_trace}        危机完整话术（不流式）
    - error   {message}                   异常
    - done    {reply, current_agent, agent_trace, persona_id, crisis} 结束信号
    """
    effective_account_id = (account.id if account else None) or req.account_id
    effective_session_id = req.session_id
    effective_persona_id = get_persona(req.persona_id).persona_id

    # 写 user 轮 ConversationTurn（失败不阻断流式）
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
        logging.warning("stream: write user ConversationTurn failed: %s", e)
        db.rollback()

    initial_state = {
        "session_id": effective_session_id or "",
        "account_id": effective_account_id or "",
        "user_message": req.message,
        "history": [{"role": m.role, "content": m.content} for m in req.history],
        "persona_id": effective_persona_id,
        "agent_trace": [],
    }

    async def event_stream():
        state = dict(initial_state)
        final_reply = ""
        final_sources: list = []
        final_agent = ""
        final_trace: list = []
        is_crisis = False

        try:
            # —— 1. triage（同步等结果，LLM 分类 2-5s）——
            yield _sse("agent", {"agent": "triage", "agent_trace": ["triage"]})
            triage_out = await triage_node(state)
            state.update(triage_out)

            # —— 2. 危机路径：escalation 不流式，推完整话术后 close ——
            if state.get("is_crisis"):
                esc_out = await escalation_node(state)
                state.update(esc_out)
                final_reply = esc_out["final_reply"]
                final_agent = "escalation"
                final_trace = state["agent_trace"]
                is_crisis = True
                yield _sse("crisis", {
                    "reply": final_reply,
                    "agent_trace": final_trace,
                    "sources": [],
                })
            else:
                # —— 3. 非危机：assessment（DB 查询，<100ms）——
                yield _sse("agent", {"agent": "assessment", "agent_trace": state["agent_trace"]})
                assess_out = await assessment_node(state)
                state.update(assess_out)

                # —— 4. intervention 流式（用户可见 token 在此阶段产生）——
                intervention_trace = state["agent_trace"] + ["intervention"]
                yield _sse("agent", {"agent": "intervention", "agent_trace": intervention_trace})

                # 提前推 sources（让前端在 token 到来前先渲染知识卡片）
                # 同一次 build 拿到 messages，传给 stream_intervention 避免重复 RAG 检索
                messages, formatted_sources, _ = await build_intervention_messages(state)
                final_sources = formatted_sources
                if final_sources:
                    yield _sse("sources", {"sources": final_sources})

                # 流式 yield token（复用 prebuilt messages，避免重复 RAG 检索）
                collected: list[str] = []
                async for token in stream_intervention(state, prebuilt_messages=messages):
                    collected.append(token)
                    yield _sse("token", {"token": token})
                final_reply = "".join(collected)
                final_agent = "intervention"
                final_trace = intervention_trace

        except Exception as e:
            logger.exception("stream: orchestration failed: %s", e)
            err_msg = f"[服务异常] 流式编排失败: {type(e).__name__}"
            yield _sse("error", {"message": err_msg})
            # 异常时仍保证有回复（兜底话术），前端可继续展示
            if not final_reply:
                final_reply = FALLBACK_REPLY
                final_agent = final_agent or "intervention"
                final_trace = final_trace or (state.get("agent_trace", []) + ["intervention"])

        # —— 5. 写 assistant 轮 ConversationTurn（审计双写不破坏）——
        try:
            db.add(
                ConversationTurn(
                    session_id=effective_session_id,
                    account_id=effective_account_id,
                    role="assistant",
                    content=final_reply,
                    sources_json=final_sources if final_sources else None,
                    crisis_hit=is_crisis,
                )
            )
            db.commit()
        except Exception as e:
            logging.warning("stream: write assistant ConversationTurn failed: %s", e)
            db.rollback()

        # —— 6. done 信号（前端收到后 close EventSource）——
        yield _sse("done", {
            "reply": final_reply,
            "current_agent": final_agent,
            "agent_trace": final_trace,
            "persona_id": effective_persona_id,
            "crisis": is_crisis,
            "sources": final_sources,
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 不缓冲（已配 proxy_buffering off，双保险）
            "Connection": "keep-alive",
        },
    )
