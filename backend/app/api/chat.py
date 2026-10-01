"""开放对话端点：LangGraph 四智能体编排（分诊→测评→干预→升级）。

POST /api/chat              非流式（向后兼容 NFR-1，旧客户端不变）
POST /api/chat/stream       SSE 流式（NFR-5 首 token 优化，边生成边推）
GET  /api/chat/history      对话历史回填（刷新页面后恢复会话）
DELETE /api/chat/history    清空指定会话的全部聊天记录
POST /api/chat/case-upload  病例科普解读（PDF/粘贴文本，multipart）

向后兼容 NFR-1：旧字段 reply/sources/crisis 不变；新增 current_agent/agent_trace/
persona_id 为可选。persona_id 仅影响干预节点人格，危机升级零 LLM 不受理格影响。
"""
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agents.graph import graph
from app.agents.nodes.assessment import assessment_node
from app.agents.nodes.escalation import escalation_node
from app.agents.nodes.intervention import (
    FALLBACK_REPLY,
    build_intervention_messages,
    stream_intervention,
)
from app.agents.nodes.case import analyze_case, case_followup
from app.agents.nodes.triage import triage_node
from app.agents.personas import get_persona
from app.api.deps import get_current_account, get_db_session
from app.api.ratelimit import rate_limit
from app.core.case_parser import (
    CaseParseError,
    clean_text,
    extract_pdf_text,
    truncate_case_text,
)
from app.core.abtest import ABTestContext, track_feedback, track_response_time
from app.core.llm import provider
from app.core.metrics import track_chat_turn, track_crisis
from app.core.safety import crisis_message
from app.models import ConversationTurn, Session as SessionModel, User

router = APIRouter(prefix="/api/chat", tags=["chat"])

logger = logging.getLogger("psycheflow.api.chat")

# 防护：单条消息最长 2000 字（防 token 滥用/超长刷接口）；上送 history 最多保留 10 轮
MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_TURNS = 20  # 10 轮 = 20 条消息
# 病例上传：PDF 体积上限 10MB
MAX_CASE_PDF_BYTES = 10 * 1024 * 1024


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(max_length=MAX_MESSAGE_CHARS)
    history: list[ChatMessage] = Field(default_factory=list)
    session_id: str | None = None
    account_id: str | None = None
    persona_id: str | None = None  # 多角色人格，不传=默认"暖暖"
    case_context: str | None = None  # 病例追问：前端持有上次上传的文书原文


def _clip_history(history: list[ChatMessage]) -> list[dict]:
    """history 截断为最近 MAX_HISTORY_TURNS 条，只保留 user/assistant 角色。"""
    clipped = [
        {"role": m.role, "content": m.content}
        for m in history
        if m.role in ("user", "assistant")
    ][-MAX_HISTORY_TURNS:]
    return clipped


def _save_turn(
    db: Session,
    session_id: str | None,
    account_id: str | None,
    role: str,
    content: str,
    sources: list | None = None,
    crisis_hit: bool = False,
    attachments: list | None = None,
) -> None:
    """统一写 ConversationTurn（失败仅警告，不阻断接口）。"""
    try:
        db.add(
            ConversationTurn(
                session_id=session_id,
                account_id=account_id,
                role=role,
                content=content,
                sources_json=sources if sources else None,
                attachments_json=attachments,
                crisis_hit=crisis_hit,
            )
        )
        db.commit()
    except Exception as e:
        logging.warning("write %s ConversationTurn failed: %s", role, e)
        db.rollback()


def _resolve_effective_ids(
    req: ChatRequest, account: User | None
) -> tuple[str | None, str | None, str]:
    """解析有效 account_id / session_id / persona_id（Bearer 账号优先于 body）。"""
    effective_account_id = (account.id if account else None) or req.account_id
    effective_session_id = req.session_id
    effective_persona_id = get_persona(req.persona_id).persona_id
    return effective_account_id, effective_session_id, effective_persona_id


def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 事件（event + data 两行，以空行结尾）。

    ensure_ascii=False 保证中文 token 不被 \\uXXXX 转义，前端可直接拼接显示。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("", dependencies=[Depends(rate_limit("chat", limit=10, window_sec=60))])
async def chat(
    req: ChatRequest,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    effective_account_id, effective_session_id, effective_persona_id = _resolve_effective_ids(req, account)

    # —— 步骤 1：先写 user 轮 ConversationTurn（失败不影响接口返回）——
    _save_turn(db, effective_session_id, effective_account_id, "user", req.message)

    # —— 步骤 2：LangGraph 四智能体编排（triage→assessment→intervention/escalation）——
    initial_state = {
        "session_id": effective_session_id or "",
        "account_id": effective_account_id or "",
        "user_message": req.message,
        "history": _clip_history(req.history),
        "persona_id": effective_persona_id,
        "agent_trace": [],
    }

    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as e:
        logger.exception("graph.ainvoke failed: %s", e)
        err_reply = f"[服务异常] 对话编排失败，请稍后重试。({type(e).__name__})"
        _save_turn(db, effective_session_id, effective_account_id, "assistant", err_reply)
        raise HTTPException(
            status_code=502,
            detail=f"对话编排失败: {type(e).__name__}: {e}",
        )

    reply = final_state.get("final_reply") or crisis_message()
    sources = final_state.get("sources", [])
    is_crisis = final_state.get("crisis", False)
    current_agent = final_state.get("current_agent", "")
    agent_trace = final_state.get("agent_trace", [])
    triage_intent = final_state.get("triage_intent", "")

    # —— 步骤 3：写 assistant 轮 ConversationTurn ——
    _save_turn(
        db, effective_session_id, effective_account_id,
        "assistant", reply, sources, is_crisis
    )

    # 埋点：对话轮次 + 危机
    track_chat_turn("assistant", triage_intent, is_crisis)
    if is_crisis:
        track_crisis("intervention")

    # —— 4. 返回（旧字段 reply/sources/crisis 不变；新增 current_agent/agent_trace/persona_id）——
    result: dict = {
        "reply": reply,
        "sources": sources,
        "crisis": is_crisis,
        "current_agent": current_agent,
        "agent_trace": agent_trace,
        "node_decisions": final_state.get("node_decisions", {}),
        "persona_id": effective_persona_id,
    }
    # A/B 测试：非流式也注入实验信息（供前端展示和后续反馈关联）
    if req.message:  # 简单对话才走 A/B，病例上传不走
        ab = ABTestContext(effective_account_id or "anonymous")
        result["ab_test"] = ab.to_dict()
        track_response_time(ab.experiment, ab.variant, ab.elapsed())
    return result


@router.post("/stream", dependencies=[Depends(rate_limit("chat", limit=10, window_sec=60))])
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
    effective_account_id, effective_session_id, effective_persona_id = _resolve_effective_ids(req, account)

    # 写 user 轮 ConversationTurn（失败不阻断流式）
    _save_turn(db, effective_session_id, effective_account_id, "user", req.message)

    initial_state = {
        "session_id": effective_session_id or "",
        "account_id": effective_account_id or "",
        "user_message": req.message,
        "history": _clip_history(req.history),
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
            # —— 1. triage（全规则路由，毫秒级，零 LLM）——
            yield _sse("agent", {"agent": "triage", "agent_trace": ["triage"]})
            triage_out = await triage_node(state)
            state.update(triage_out)

            # —— 2. 极速直达路径：若是寒暄（已产出 final_reply），推完整话术后 close ——
            if state.get("final_reply"):
                final_reply = state["final_reply"]
                final_agent = state.get("current_agent", "triage")
                final_trace = state.get("agent_trace", ["triage"])
                # 模拟流式效果（直接推完或推 token，此处选直接推完信号，前端 done 会补全）
                yield _sse("token", {"token": final_reply})

            # —— 2b. 病例追问路径：前端持有 case_context 时走轻量 follow-up ——
            elif req.case_context:
                yield _sse("agent", {"agent": "case", "agent_trace": ["case_followup"]})
                followup_reply = await case_followup(
                    case_text=req.case_context,
                    interpretation="",  # 历史已有解读，追问不需重传
                    question=req.message,
                )
                # 危机追问走 crisis 事件，否则走 token
                from app.core.safety import detect_crisis_with_words as _dc
                _is_crisis_q, _ = _dc(req.message)
                if _is_crisis_q:
                    is_crisis = True
                    final_agent = "escalation"
                    final_trace = ["case_followup", "escalation"]
                    yield _sse("crisis", {"reply": followup_reply, "agent_trace": final_trace, "sources": []})
                else:
                    yield _sse("token", {"token": followup_reply})
                    final_reply = followup_reply
                    final_agent = "case"
                    final_trace = ["case_followup"]

            # —— 3. 危机路径：escalation 不流式，推完整话术后 close ——
            elif state.get("is_crisis"):
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
                messages, formatted_sources, rag_srcs, _ = await build_intervention_messages(state)
                final_sources = formatted_sources
                if final_sources:
                    yield _sse("sources", {"sources": final_sources})

                # 流式 yield token（复用 prebuilt messages，避免重复 RAG 检索；
                # 传入首轮 RAG 片段供质检重试换片时排除已引用切片）
                collected: list[str] = []
                async for token in stream_intervention(
                    state, prebuilt_messages=messages, prebuilt_rag_sources=rag_srcs
                ):
                    collected.append(token)
                    yield _sse("token", {"token": token})
                final_reply = "".join(collected)
                final_agent = "intervention"
                final_trace = intervention_trace

        except Exception as e:
            logger.exception("stream: orchestration failed: %s", e)
            err_msg = f"[服务异常] 流式编排失败: {type(e).__name__}"
            # 客户端已断开时 yield 会再抛 CancelledError，由 finally 收尾
            try:
                yield _sse("error", {"message": err_msg})
            except Exception:
                pass

        finally:
            # —— 5. 写 assistant 轮 ConversationTurn（finally 保证：客户端点「停止」
            #    导致 CancelledError 时，已生成的片段也落库，刷新回填不丢内容）——
            if not final_reply:
                final_reply = FALLBACK_REPLY
                final_agent = final_agent or "intervention"
                final_trace = final_trace or (state.get("agent_trace", []) + ["intervention"])
            _save_turn(
                db, effective_session_id, effective_account_id,
                "assistant", final_reply, final_sources, is_crisis
            )

        # —— 6. done 信号（前端收到后结束读取；客户端已中断时此 yield 抛异常，无害）——
        done_payload: dict = {
            "reply": final_reply,
            "current_agent": final_agent,
            "agent_trace": final_trace,
            "node_decisions": state.get("node_decisions", {}),
            "persona_id": effective_persona_id,
            "crisis": is_crisis,
            "sources": final_sources,
        }
        # A/B 测试：流式结束也注入实验信息
        if req.message:
            ab = ABTestContext(effective_account_id or "anonymous")
            done_payload["ab_test"] = ab.to_dict()
            track_response_time(ab.experiment, ab.variant, ab.elapsed())
        yield _sse("done", done_payload)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 不缓冲（已配 proxy_buffering off，双保险）
            "Connection": "keep-alive",
        },
    )


# ================================================================
# 对话历史回填：刷新页面后按 chat session 恢复对话（ConversationTurn 已双写落库）
# ================================================================
@router.get("/history")
async def chat_history(
    session_id: str,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    """返回指定会话的对话轮次（按时间正序），供前端刷新后回填。

    权限与 /api/sessions/{id} 一致：非匿名会话必须是本人；匿名会话不校验。
    """
    if not session_id:
        raise HTTPException(status_code=422, detail="缺少 session_id")

    sess = db.get(SessionModel, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if sess.account_id is not None and (account is None or account.id != sess.account_id):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})

    rows = db.execute(
        select(ConversationTurn)
        .where(ConversationTurn.session_id == session_id)
        .order_by(ConversationTurn.created_at.asc(), ConversationTurn.id.asc())
    ).scalars().all()

    items = [
        {
            "role": r.role,
            "content": r.content,
            "sources": r.sources_json or [],
            "attachments": r.attachments_json or [],
            "crisis_hit": bool(r.crisis_hit),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {"session_id": session_id, "items": items}


@router.delete("/history")
async def chat_history_delete(
    session_id: str,
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    """清空指定会话的全部对话轮次（保留会话本身，供继续对话）。

    权限同 GET /history：非匿名会话必须是本人；匿名会话不校验。
    """
    if not session_id:
        raise HTTPException(status_code=422, detail="缺少 session_id")

    sess = db.get(SessionModel, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if sess.account_id is not None and (account is None or account.id != sess.account_id):
        raise HTTPException(status_code=403, detail={"code": "forbidden"})

    result = db.execute(
        delete(ConversationTurn).where(ConversationTurn.session_id == session_id)
    )
    db.commit()
    return {"session_id": session_id, "deleted": int(result.rowcount or 0)}


# ================================================================
# 病例科普解读：上传文字版 PDF 或粘贴文本 → 危机前置扫描 → LLM 结构化解读
# 隐私：PDF 文件仅在内存解析不落盘；ConversationTurn 只存文件名+字数，不存原文
# ================================================================
@router.post("/case-upload", dependencies=[Depends(rate_limit("report", limit=3, window_sec=60))])
async def case_upload(
    file: UploadFile | None = File(None),
    text: str = Form(""),
    session_id: str = Form(""),
    persona_id: str | None = Form(None),  # 收参保持前端统一；病例解读不受理格影响
    db: Session = Depends(get_db_session),
    account: User | None = Depends(get_current_account),
):
    """病例解读（multipart）：file 与 text 至少提供一个。

    - file：仅支持文字版 .pdf（≤10MB）；扫描件无文本层返回 400 引导粘贴
    - text：直接粘贴的病例文本（≤6000 字，超长自动截断）
    """
    effective_account_id = (account.id if account else None)
    effective_session_id = session_id or None

    case_text = ""
    attachment = {"kind": "text", "name": "粘贴文本", "char_count": 0}
    user_bubble = ""

    if file is not None and file.filename:
        filename = file.filename
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="目前仅支持 PDF 文档（文字版），图片请复制文字后粘贴")
        pdf_bytes = await file.read()
        if len(pdf_bytes) > MAX_CASE_PDF_BYTES:
            raise HTTPException(status_code=400, detail="PDF 文件过大，请上传 10MB 以内的文件")
        try:
            case_text = extract_pdf_text(pdf_bytes)
        except CaseParseError as e:
            # NoTextLayerError（扫描件）/ 损坏文件：message 可直接展示
            raise HTTPException(status_code=400, detail=str(e))
        attachment = {"kind": "pdf", "name": filename, "char_count": len(case_text)}
        user_bubble = f"📎 上传了病例文件：{filename}（抽取约 {len(case_text)} 字，文件内容不存档）"
    elif text and text.strip():
        case_text, truncated = truncate_case_text(clean_text(text))
        if len(case_text) < 20:
            raise HTTPException(status_code=400, detail="粘贴的文本太短，请复制完整的病例内容后再发送")
        note = "，超长部分已省略" if truncated else ""
        attachment = {"kind": "text", "name": "粘贴文本", "char_count": len(case_text)}
        user_bubble = f"📝 粘贴了病例文本（约 {len(case_text)} 字{note}，内容不存档）"
    else:
        raise HTTPException(status_code=400, detail="请上传 PDF 文件或粘贴病例文本")

    # 写 user 轮（只存附件元数据 + 气泡描述，不存病例原文）
    _save_turn(
        db, effective_session_id, effective_account_id,
        "user", user_bubble, attachments=[attachment]
    )

    # 危机前置扫描 + RAG + LLM 解读（LLM 失败有节点级兜底话术）
    result = await analyze_case(
        case_text,
        session_id=effective_session_id or "",
        account_id=effective_account_id or "",
    )
    reply = result["reply"]
    sources = result["sources"]
    is_crisis = result["crisis"]

    # 写 assistant 轮
    _save_turn(
        db, effective_session_id, effective_account_id,
        "assistant", reply, sources, is_crisis
    )

    return {
        "reply": reply,
        "sources": sources,
        "crisis": is_crisis,
        "current_agent": result["agent"],
        "attachment": attachment,
        "case_summary": result.get("case_summary"),
        "case_text": case_text,
    }
