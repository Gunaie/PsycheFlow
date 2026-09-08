"""Intervention 干预节点：RAG 检索 + LLM 共情回应。

核心对话智能体，温度 0.6（共情自然 + 缓解模板重复）。引用 RAG 知识库片段时回复末尾用
「来源：《xxx》」格式，sources 字段同时返回供前端渲染卡片。

本模块同时支持：
- 非流式：intervention_node（供 LangGraph graph.ainvoke 调用，返回 final_reply）
- 流式：stream_intervention（供 SSE 端点直接 yield token）
两者共享 build_intervention_messages 的 prompt 拼接逻辑，保证行为一致。

回复质检重试层（零 LLM 成本）：生成后用正则检测封闭式问句（对吧/对吗/是不是等）
与历史逐字重复，不合格附纠正提示重试 1 次；流式先缓冲完整生成再质检，
通过后按原始 token 粒度匀速补推（恢复打字机流式感，避免 UI 闪烁）。
"""
import asyncio
import logging
import re
from typing import AsyncIterator

from app.agents.personas import build_system_prompt, get_persona
from app.agents.prompts import INTERVENTION_USER_TEMPLATE, get_reply_skeleton
from app.agents.state import AgentState
from app.core.llm import provider
from app.rag.service import rag_service

logger = logging.getLogger("psycheflow.agents.intervention")

# LLM 失败/空回复时的硬编码兜底话术（含 12355，安全底线）
FALLBACK_REPLY = (
    "我听到你的分享，谢谢你的信任。"
    "作为校园心理陪伴助手，我现在的回复能力受限，"
    "请把你正在承受的告诉信任的老师或家长，"
    "或拨打青少年心理援助热线 12355 寻求专业陪伴。"
)


# —— 回复质检重试层（正则零 LLM 成本，与 dialog_smoke 检查口径一致）——
# 封闭式问句：诱导「是/否」式回答，压制对话开放性（7B 模型高频坏习惯）
_BANNED_CLOSE_Q = re.compile(r"(对吧|对吗|是不是|是吧|好吗)")
# 逐字重复判定阈值：短于该长度的分句（如「嗯」「好的」）不判重复，避免误伤常规应答
_REPEAT_MIN_LEN = 12

# 质检不合格时附加在 messages 末尾的重试纠正提示
# 注意：不引用违禁词原文（列出「对吧/对吗」等 token 反而会诱导模型复现它们）
RETRY_HINT = (
    "【重试要求】你上一次的回复不合格：用了诱导对方回答「是/否」的确认式问句，"
    "或与历史对话逐字重复了相同的句子。请重新生成回复，必须做到："
    "1）提问只用开放式邀请（如「能多说说吗」「你希望怎么改变」）；"
    "2）不要复述历史对话里已出现过的句子，换新的说法；"
    "3）给出具体可操作的建议；直接输出新回复正文，不要解释。"
)


def _normalize(text: str) -> str:
    """去空白 + 剔除《书名号》段归一化（同源多轮引用「来源：《xxx》」属合法，不算复读）。"""
    t = re.sub(r"\s+", "", text or "")
    return re.sub(r"《[^》]*》", "", t)


def check_reply_quality(reply: str, history: list[dict] | None) -> bool:
    """回复质检：True=合格；False=不合格（封闭式问句 / 与历史 assistant 回复逐字重复）。

    重复判定：新回复与历史某条 assistant 回复存在 ≥12 字的逐字公共片段
    （滑动窗口匹配，半改写也算——捕获「这一定让你感到特别疲惫」及
    「除了担心/焦虑，你有没有感觉到…」这类换头不换身的模板句跨轮复发）。
    """
    if not reply or not reply.strip():
        return False
    if _BANNED_CLOSE_Q.search(reply):
        return False
    hist_norms = [
        _normalize(h.get("content", ""))
        for h in (history or [])
        if h.get("role") == "assistant"
    ]
    norm_reply = _normalize(reply)
    for clause in re.split(r"[。！？!?\n]+", norm_reply):
        if len(clause) < _REPEAT_MIN_LEN:
            continue
        for i in range(len(clause) - _REPEAT_MIN_LEN + 1):
            window = clause[i:i + _REPEAT_MIN_LEN]
            if any(window in hn for hn in hist_norms if hn):
                return False
    return True


async def build_intervention_messages(state: AgentState) -> tuple[list[dict], list, list, dict]:
    """拼接 intervention 的 LLM messages + formatted_sources + rag_sources。

    流式与非流式复用同一套 prompt 拼接逻辑，保证行为一致。
    返回 (messages, formatted_sources, rag_sources, decision)。
    """
    message = state.get("user_message", "")
    decision = {}

    # 0. 解析人格（未知/为空回退 default）
    persona = get_persona(state.get("persona_id"))
    system_prompt = build_system_prompt(persona)
    logger.info("intervention: persona=%s", persona.persona_id)
    decision["persona"] = persona.persona_id

    # 1. RAG 检索（寒暄/求助跳过：问候与身份询问/测评引导和心理知识无关，
    #    避免向量检索凑近推送无关来源卡片，如"你好，你是谁"也能召回 ≤0.70 距离片段）
    triage_intent = state.get("triage_intent", "")
    rag_sources: list = []
    if triage_intent in ("寒暄", "求助"):
        logger.info("intervention: skip rag for intent=%s", triage_intent)
        decision["rag"] = {"skipped": True, "reason": f"intent={triage_intent}"}
    else:
        try:
            rag_sources = await rag_service.search(message, top_k=3)
            logger.info("intervention: rag retrieved %d chunks", len(rag_sources))
            decision["rag"] = {
                "count": len(rag_sources),
                "sources": [s.get("source") for s in rag_sources]
            }
        except Exception as e:
            logger.warning("intervention: rag search failed: %s", str(e))
            decision["rag"] = {"error": str(e)}

    # 2. 拼接 rag_context（最多 3 段，每段 350 字截断；切片本身以句子边界收尾，
    #    截断过短会把完整句重新切成"没尾"片段误导 LLM）
    rag_parts = []
    for i, src in enumerate(rag_sources[:3], 1):
        text = (src.get("text") or "")[:350]
        source = src.get("source") or "未知来源"
        rag_parts.append(f"[{i}] 《{source}》:\n{text}")
    rag_context = "\n\n".join(rag_parts) if rag_parts else "（无相关片段）"

    # 3. 拼 messages（history 拼在 system 后保持对话上下文，向后兼容旧 chat 行为）
    user_prompt = INTERVENTION_USER_TEMPLATE.format(
        triage_intent=state.get("triage_intent", "倾诉"),
        has_assessment=state.get("has_assessment", False),
        assessment_context=state.get("assessment_context", {}),
        message=message,
        rag_context=rag_context,
        reply_skeleton=get_reply_skeleton(state.get("triage_intent", "倾诉")),
    )
    # 只保留最近 10 轮（20 条），防长对话 token 膨胀（API 层 _clip_history 已截，双保险）
    history = [
        {"role": h["role"], "content": h["content"]}
        for h in (state.get("history") or [])
        if h.get("role") in ("user", "assistant")
    ][-20:]
    messages = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": user_prompt},
    ]

    # 4. sources 格式化为前端兼容字段（text + source + chunk_id）
    formatted_sources = [
        {
            "text": s.get("text", ""),
            "source": s.get("source", ""),
            "chunk_id": s.get("chunk_id", 0),
        }
        for s in rag_sources
    ]

    return messages, formatted_sources, rag_sources, decision


# 质检缓冲后的补推节奏（秒/字符）：token 缓冲期间瞬时到达，一次性 yield 前端会
# 「整段闪现」；按字符数匀速补推恢复打字机效果（约 80 字/秒，300 字回复约 3.6 秒）。
# 注意按 token 计数不行：云端模型 token 颗粒粗（一片 5-15 字），15ms/token 会 0.4 秒推完
_REVEAL_DELAY_PER_CHAR = 0.012


async def _paced(tokens: list[str]) -> AsyncIterator[str]:
    """按字符数匀速 yield token（保持原始 token 粒度，仅加节奏）。"""
    for token in tokens:
        await asyncio.sleep(_REVEAL_DELAY_PER_CHAR * len(token))
        yield token


async def stream_intervention(
    state: AgentState,
    prebuilt_messages: list[dict] | None = None,
) -> AsyncIterator[str]:
    """流式干预：先缓冲完整生成 → 质检 → 不合格重试 1 次 → 按原始 token 粒度匀速补推。

    为什么先缓冲：token 一旦推给前端就无法撤回，流式质检后重试会造成
    「回复被替换」的 UI 闪烁；故先收集完整回复，质检通过后再推出
    （保持 SSE token 事件语义与拼接结果不变）。

    为什么补推要匀速：缓冲期间 token 是瞬时到达的，一次性 yield 前端会
    「整段闪现」丢失流式感；按 _REVEAL_DELAY_PER_TOKEN 小间隔补推恢复
    打字机效果，总补推时长约 2-4 秒，远小于生成耗时。

    LLM 失败或空回复时 yield FALLBACK_REPLY（保证用户一定能收到回复）。
    最终完整回复由调用方累积（"".join(tokens)）。

    prebuilt_messages：若 SSE 端点已调 build_intervention_messages 拿到 messages
    和 sources（避免 RAG 重复检索），可直接传入复用；None 则内部构建。
    """
    messages = prebuilt_messages or (await build_intervention_messages(state))[0]
    # 质检用历史（与 build_intervention_messages 同口径：最近 20 条 user/assistant）
    history = [
        {"role": h["role"], "content": h["content"]}
        for h in (state.get("history") or [])
        if h.get("role") in ("user", "assistant")
    ][-20:]

    async def _collect(msgs: list[dict], temperature: float = 0.6) -> tuple[list[str], bool]:
        """完整收集一次流式生成的全部 token；返回 (tokens, 是否异常)。"""
        collected: list[str] = []
        try:
            # 流式用 dialog_stream 角色（qwen3.8-max 关思考链，首 token 快）；非流式 intervention_node 仍用 dialog（deepseek 高质量）
            async for token in provider.stream(
                role="dialog_stream",
                messages=msgs,
                temperature=temperature,
                max_tokens=3000,
            ):
                collected.append(token)
            return collected, False
        except Exception as e:
            logger.warning("intervention: stream LLM failed: %s", str(e))
            return collected, True

    tokens, failed = await _collect(messages)
    text = "".join(tokens)

    # 流中断但已有部分内容：推出已有部分（不重试，流已不可靠，且重试可能丢失上下文）
    if failed and text.strip():
        async for token in _paced(tokens):
            yield token
        return

    # 空字符串/纯空白不抛异常，须显式检查触发 fallback（同 reports 教训）
    if not text.strip():
        logger.warning("intervention: LLM returned empty stream, yielding fallback")
        yield FALLBACK_REPLY
        return

    # 质检不合格 → 附纠正提示重试 1 次（降温提高指令遵循）；重试异常或仍不合格则沿用首次回复
    if not check_reply_quality(text, history):
        logger.info("intervention: quality check failed, retrying once (stream)")
        retry_tokens, retry_failed = await _collect(
            [*messages, {"role": "system", "content": RETRY_HINT}],
            temperature=0.35,
        )
        retry_text = "".join(retry_tokens)
        if not retry_failed and retry_text.strip() and check_reply_quality(retry_text, history):
            tokens, text = retry_tokens, retry_text

    async for token in _paced(tokens):
        yield token


async def intervention_node(state: AgentState) -> dict:
    """干预节点（非流式，供 LangGraph graph.ainvoke 调用）。

    流程：
    1. build_intervention_messages 拼 prompt
    2. provider.chat(role="dialog", temp=0.6) 生成共情回应
    3. 回复质检（封闭式问句/逐字重复）→ 不合格附纠正提示重试 1 次
    4. 空回复/异常 → FALLBACK_REPLY
    5. sources 字段返回供前端渲染
    """
    trace = state.get("agent_trace", []) + ["intervention"]
    decisions = state.get("node_decisions", {})
    messages, formatted_sources, rag_sources, decision = await build_intervention_messages(state)

    try:
        reply = await provider.chat(
            role="dialog",
            messages=messages,
            temperature=0.6,
            max_tokens=3000,  # deepseek-v4 有 reasoning_content 思考链，600 不够
        )
        # 空字符串/纯空白不抛异常，须显式检查触发 fallback（同 reports 教训）
        if not reply or not reply.strip():
            logger.warning("intervention: LLM returned empty reply, triggering fallback")
            raise ValueError("empty reply from LLM")
        decision["llm"] = {"status": "success", "reply_len": len(reply)}
        # 质检不合格 → 附纠正提示重试 1 次；重试异常或仍不合格则保留首次回复
        history = [
            {"role": h["role"], "content": h["content"]}
            for h in (state.get("history") or [])
            if h.get("role") in ("user", "assistant")
        ][-20:]
        if not check_reply_quality(reply, history):
            logger.info("intervention: quality check failed, retrying once")
            decision["llm"]["quality_retry"] = True
            try:
                retry = await provider.chat(
                    role="dialog",
                    messages=[*messages, {"role": "system", "content": RETRY_HINT}],
                    temperature=0.35,  # 重试降温：约束类指令低温下遵循率更高
                    max_tokens=3000,
                )
                if retry and retry.strip() and check_reply_quality(retry, history):
                    reply = retry
            except Exception as retry_err:
                logger.warning(
                    "intervention: quality retry failed: %s, keep first reply", str(retry_err)
                )
        logger.info("intervention: reply len=%d", len(reply))
    except Exception as e:
        logger.warning("intervention: LLM failed: %s", str(e))
        reply = FALLBACK_REPLY
        decision["llm"] = {"status": "fallback", "reason": str(e)}

    decisions["intervention"] = decision

    return {
        "final_reply": reply,
        "sources": formatted_sources,
        "rag_sources": rag_sources,
        "current_agent": "intervention",
        "agent_trace": trace,
        "node_decisions": decisions
    }
