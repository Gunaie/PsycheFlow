"""Intervention 干预节点：RAG 检索 + LLM 共情回应。

核心对话智能体，温度 0.6（共情自然 + 缓解模板重复）。引用 RAG 知识库片段时回复末尾用
「来源：《xxx》」格式，sources 字段同时返回供前端渲染卡片。

本模块同时支持：
- 非流式：intervention_node（供 LangGraph graph.ainvoke 调用，返回 final_reply）
- 流式：stream_intervention（供 SSE 端点直接 yield token）
两者共享 build_intervention_messages 的 prompt 拼接逻辑，保证行为一致。

回复质检重试层（零 LLM 成本）：生成后用正则检测封闭式问句（对吧/对吗/是不是等）
与历史逐字重复，不合格走三级重试阶梯（hint 针对性禁令 → rag_refresh 换检索片段
→ context_trim 剔除历史 assistant 回复）；重试温度 0.6/0.7（0.35 下本地 LoRA 会
复读），禁令按本条回复的实际违规点动态生成（点名复读原句/上轮做法类别）。
流式先缓冲完整生成再质检，通过后按原始 token 粒度匀速补推（恢复打字机流式感，
避免 UI 闪烁）。
"""
import asyncio
import logging
import re
from typing import AsyncIterator

from app.agents.personas import build_system_prompt, get_persona
from app.agents.prompts import INTERVENTION_USER_TEMPLATE, get_reply_skeleton
from app.agents.state import AgentState
from app.core.config import settings
from app.core.llm import provider
from app.rag.service import rag_service

logger = logging.getLogger("psycheflow.agents.intervention")

# 三级重试阶梯（可扩展），新增模式只需修改此处即可在流式/非流式同步生效
RETRY_MODES = ("hint", "rag_refresh", "context_trim")

# LLM 失败/空回复时的硬编码兜底话术（含 12355，安全底线；热线号码走 settings 单一事实源）
FALLBACK_REPLY = (
    "我听到你的分享，谢谢你的信任。"
    "作为校园心理陪伴助手，我现在的回复能力受限，"
    "请把你正在承受的告诉信任的老师或家长，"
    f"或拨打青少年心理援助热线 {settings.crisis_hotline_12355} 寻求专业陪伴。"
)


# —— 回复质检重试层（正则零 LLM 成本，与 dialog_smoke 检查口径一致）——
# 封闭式问句：诱导「是/否」式回答，压制对话开放性（7B 模型高频坏习惯）
# 覆盖：对吧/对吗/是不是/是吧/好吗/对不对/好不好/要不要/会不会/有没有/能不能/可以吗 + 句末「吗？」「吧？」
# 口径需与 prompts.py INTERVENTION_USER_TEMPLATE 第 5 条禁令保持同步
_BANNED_CLOSE_Q = re.compile(r"(对吧|对吗|是不是|是吧|好吗|对不对|好不好|要不要|会不会|有没有|能不能|可以吗|吗[？?]|吧[？?])")
# 逐字重复判定阈值：短于该长度的分句（如「嗯」「好的」）不判重复，避免误伤常规应答
_REPEAT_MIN_LEN = 12

# 幻觉归因检测：「你说过/你之前说/你上次说/你以前说」引导的内容若不在用户历史中出现即为编造
_FABRICATED_ATTR_RE = re.compile(r"(你说过|你之前说|你上次说|你以前说)([^，。！？!?；;\n]{2,40})")

# 做法类别关键词：用于检测跨轮"同类做法重复"（腹式呼吸的不同表述都算同一类）
_METHOD_CATEGORIES = {
    "breathing": ("呼吸", "肚子", "吸气", "呼气", "腹式", "喘气", "气息"),
    "journaling": ("写", "本子", "日记", "记下来", "纸笔", "写下来"),
    "muscle_relax": ("肌肉", "紧张再放松", "渐进式", "握拳", "握紧", "绷紧"),
    "mindfulness": ("正念", "冥想", "观察", "当下", "感受身体"),
    "exercise": ("运动", "跑步", "散步", "拉伸", "走动"),
    # 任务拆解与呼吸放松同列 prompt 骨架示例做法（prompts.py REPLY_SKELETON_CONSULT），
    # QC 分类学必须与 prompt 示例一致，否则模型照骨架给任务拆解会被误判「做法类别<2」
    "planning": ("任务拆解", "拆成", "拆解", "清单", "列出来", "计划表"),
}


def _detect_method_categories(text: str) -> set[str]:
    """提取回复中涉及的做法类别集合。"""
    norm = _normalize(text)
    found = set()
    for cat, keywords in _METHOD_CATEGORIES.items():
        if any(kw in norm for kw in keywords):
            found.add(cat)
    return found

# 质检不合格时附加在 messages 末尾的重试纠正提示
# 注意：不引用违禁词原文（列出「对吧/对吗」等 token 反而会诱导模型复现它们），
# 同理也不给具体做法示例——7B 模型会照抄示例句（如「把担心的事写在纸上」）
# 并补完库存句式，成为跨轮逐字复读的种子（2026-09-30 多轮评测实证）。
RETRY_HINT = (
    "【重试要求】你上一次的回复不合格。请严格按以下要求重新生成，直接输出回复正文："
    "1）整轮最多一个开放式问题（用「什么/怎么/哪些/哪里」提问，禁止用「吗/吧/会不会/有没有」收尾）；"
    "2）50-100字、不超过3句；"
    "3）如果用户问缓解/改善方法，必须给出2种不同类别的具体做法（例如一类身体放松、"
    "一类换个方式表达心情），全部用你自己组织的全新表述，禁止照搬本提示或之前任何轮次"
    "出现过的句子，两种做法用「也可以」「另外」连接；"
    "4）上轮用过的做法本轮必须换成完全不同类型；"
    "5）「你说过…」只能指用户历史中真实说过的内容，不确定就不要用这个句式。"
)


def _normalize(text: str) -> str:
    """去空白 + 剔除《书名号》段归一化（同源多轮引用「来源：《xxx》」属合法，不算复读）。"""
    t = re.sub(r"\s+", "", text or "")
    return re.sub(r"《[^》]*》", "", t)


# 呼吸参数公式（项目硬约束：基础科普层统一 4 吸/6 呼口径）。LoRA 表达呼吸法只有这一种
# 标准句式，跨轮提及呼吸法必然逐字重现该公式——参数口径属标准化表述而非复读，
# 逐字重复检测前剥离（用哨兵字符占位防止剥离后前后文拼接出虚假重叠）。
_BREATH_FORMULA_RE = re.compile(r"吸气[数一二三四五六七八九十\d]{0,2}秒[、,，]?呼气[数一二三四五六七八九十\d]{0,2}秒")
_FORMULA_SENTINEL = "·"


def _strip_breath_formula(norm_text: str) -> str:
    """在 _normalize 之后的文本上剥离呼吸参数公式（仅用于逐字重复检测）。"""
    return _BREATH_FORMULA_RE.sub(_FORMULA_SENTINEL, norm_text)


# 做法类别的中文标签（重试禁令中点名用，7B 模型对具体名词遵循度高于抽象要求）
_METHOD_CATEGORY_LABELS = {
    "breathing": "呼吸调节",
    "journaling": "书写表达（写在纸上/日记本）",
    "muscle_relax": "肌肉放松",
    "mindfulness": "正念觉察",
    "exercise": "运动",
    "planning": "任务拆解（列清单/分步骤）",
}


def _find_verbatim_overlap(reply: str, history: list[dict] | None) -> str:
    """新回复与历史 assistant 回复间最长 ≥12 字逐字重叠片段（无则空串）。

    用于质检重试时向模型点名具体复读句：从每个候选起点向右扩展，
    遍历全部起点即覆盖最大重叠。仅统计归一化文本（_normalize 口径，
    与 check_reply_quality 的逐字重复判定一致），呼吸参数公式先行剥离。
    """
    norm_reply = _strip_breath_formula(_normalize(reply))
    if len(norm_reply) < _REPEAT_MIN_LEN:
        return ""
    hist_norms = [
        _strip_breath_formula(_normalize(h.get("content", "")))
        for h in (history or [])
        if h.get("role") == "assistant"
    ]
    hist_norms = [h for h in hist_norms if h]
    if not hist_norms:
        return ""
    best = ""
    for i in range(len(norm_reply) - _REPEAT_MIN_LEN + 1):
        window = norm_reply[i:i + _REPEAT_MIN_LEN]
        if not any(window in hn for hn in hist_norms):
            continue
        cand = window
        j = i + _REPEAT_MIN_LEN
        while j < len(norm_reply) and any((cand + norm_reply[j]) in hn for hn in hist_norms):
            cand += norm_reply[j]
            j += 1
        if len(cand) > len(best):
            best = cand
    return best


def build_retry_hint(reply: str, history: list[dict] | None) -> str:
    """质检重试用的纠正提示：RETRY_HINT + 按本条回复实际违规点追加针对性禁令。

    泛化要求（「换成不同类型」）7B 模型遵循度差，复读发生时点名具体复读句
    与上轮做法类别（如「书写表达（写在纸上/日记本）」）能显著提升换法成功率。
    """
    bans: list[str] = []
    overlap = _find_verbatim_overlap(reply, history)
    if overlap:
        shown = overlap[:60]
        bans.append(f"禁止重复你之前轮次说过的句子（尤其「{shown}」），必须用全新的表述")
    cur_cats = _detect_method_categories(reply)
    last_assistant = next(
        (h for h in reversed(history or []) if h.get("role") == "assistant"),
        None,
    )
    if cur_cats and last_assistant:
        prev_cats = cur_cats & _detect_method_categories(last_assistant.get("content", ""))
        if prev_cats:
            labels = "、".join(_METHOD_CATEGORY_LABELS[c] for c in sorted(prev_cats))
            bans.append(
                f"上一轮你已建议过{labels}，本轮禁止再提这类做法，必须换成完全不同的类型"
            )
    if not bans:
        return RETRY_HINT
    return RETRY_HINT + "【针对本条回复的禁令】" + "；".join(bans) + "。"


async def _refresh_rag_sources(state: AgentState, cited_sources: list) -> list:
    """重试换片：检索 top_k=6 并排除首轮回复已引用的切片，返回新片段（≤3 条）。

    多轮对话下复读的主因是同一 RAG 片段每轮被召回并被逐字照抄；重试时换一批
    切片比仅换措辞更根治。检索失败或新片段不足时按剩余数量返回
    （空列表 = 无 RAG 上下文兜底，模型改用自己的知识组织回复）。
    """
    cited_ids = {s.get("chunk_id") for s in (cited_sources or [])}
    try:
        hits = await rag_service.search(
            state.get("user_message", ""),
            top_k=6,
            intent=state.get("triage_intent", ""),
            caller="intervention",
        )
    except Exception as e:
        logger.warning("intervention: rag refresh search failed: %s", str(e))
        return []
    fresh = [s for s in hits if s.get("chunk_id") not in cited_ids]
    logger.info("intervention: rag refresh got %d fresh chunks (excluded %d cited)", len(fresh[:3]), len(cited_ids))
    return fresh[:3]


def check_reply_quality(
    reply: str,
    history: list[dict] | None,
    min_method_categories: int = 1,
    min_len: int = 0,
) -> bool:
    """回复质检：True=合格；False=不合格。

    检查项：
    - 封闭式问句：对吧/对吗/是不是/是吧/好吗/对不对/好不好/会不会/有没有/能不能 + 句末「吗？」「吧？」
    - 整轮最多一个问题：问号（?/？）超过 1 个即不合格
    - 长度下限：min_len>0 时，短于 min_len 字不合格（prompt 要求 50-100，防止敷衍式短回复）
    - 长度上限：超过 120 字不合格（prompt 要求 50-100，留 20 字弹性）
    - 逐字重复：新回复与历史某条 assistant 回复存在 ≥12 字的逐字公共片段
    - 幻觉归因：「你说过…」引导的内容若未在用户历史消息中出现，即为编造
    - 同类做法重复：本轮做法类别与上一轮 assistant 回复有交集即不合格
    - 做法多样性：min_method_categories>1 时，回复须包含至少该数量的不同做法类别
      （求做法问题要求 ≥2 种，避免只给腹式呼吸一种）

    注：寒暄快速通道调用时 min_len 保持 0（寒暄回复要求 50 字以内，与干预回复的 50-100 要求不同）。
    """
    if not reply or not reply.strip():
        return False
    stripped = reply.strip()
    if _BANNED_CLOSE_Q.search(reply):
        return False
    # 整轮最多一个问题：问号计数（中英文问号）
    q_count = reply.count("?") + reply.count("？")
    if q_count > 1:
        return False
    # 长度下限：min_len>0 时生效（干预回复要求 50-100，寒暄回复不设下限）
    if min_len > 0 and len(stripped) < min_len:
        return False
    # 长度上限：120 字（含标点，留 20 字弹性避免误伤正常表达）
    if len(stripped) > 120:
        return False
    # 做法多样性检测：求做法问题要求 ≥2 种不同类别
    cur_cats = _detect_method_categories(reply)
    if min_method_categories > 1 and len(cur_cats) < min_method_categories:
        return False
    # 幻觉归因检测：「你说过X」中的 X 须在用户历史中真实出现
    user_hist_text = "".join(
        _normalize(h.get("content", ""))
        for h in (history or [])
        if h.get("role") == "user"
    )
    for m in _FABRICATED_ATTR_RE.finditer(reply):
        attr_content = _normalize(m.group(2))
        if attr_content and attr_content not in user_hist_text:
            # 宽松匹配：归因内容的任一 4 字以上片段在用户历史中出现即算合法引用
            has_overlap = any(
                attr_content[i:i+4] in user_hist_text
                for i in range(len(attr_content) - 3)
            ) if len(attr_content) >= 4 else False
            if not has_overlap:
                return False
    # 同类做法重复检测：本轮做法类别与上一轮 assistant 回复有交集即不合格
    # （腹式呼吸的不同表述都算 breathing 类，避免"数数呼吸"和"手放肚子上呼吸"跨轮重复）
    if cur_cats:
        last_assistant = next(
            (h for h in reversed(history or []) if h.get("role") == "assistant"),
            None,
        )
        if last_assistant:
            prev_cats = _detect_method_categories(last_assistant.get("content", ""))
            if cur_cats & prev_cats:
                return False
    hist_norms = [
        _strip_breath_formula(_normalize(h.get("content", "")))
        for h in (history or [])
        if h.get("role") == "assistant"
    ]
    norm_reply = _strip_breath_formula(_normalize(reply))
    for clause in re.split(r"[。！？!?\n]+", norm_reply):
        if len(clause) < _REPEAT_MIN_LEN:
            continue
        for i in range(len(clause) - _REPEAT_MIN_LEN + 1):
            window = clause[i:i + _REPEAT_MIN_LEN]
            if any(window in hn for hn in hist_norms if hn):
                return False
    return True


async def build_intervention_messages(
    state: AgentState, rag_override: list | None = None
) -> tuple[list[dict], list, list, dict]:
    """拼接 intervention 的 LLM messages + formatted_sources + rag_sources。

    流式与非流式复用同一套 prompt 拼接逻辑，保证行为一致。
    返回 (messages, formatted_sources, rag_sources, decision)。

    rag_override：质检重试换片路径传入已检索好的片段（可为空列表=无 RAG 上下文），
    传 None（默认）时按原逻辑执行检索。
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
    if rag_override is not None:
        rag_sources = list(rag_override)
        decision["rag"] = {"count": len(rag_sources), "override": True}
        logger.info("intervention: using %d overridden rag chunks", len(rag_sources))
    elif triage_intent in ("寒暄", "求助"):
        logger.info("intervention: skip rag for intent=%s", triage_intent)
        decision["rag"] = {"skipped": True, "reason": f"intent={triage_intent}"}
    else:
        try:
            rag_sources = await rag_service.search(
                message, top_k=3, intent=triage_intent, caller="intervention"
            )
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
    prebuilt_rag_sources: list | None = None,
) -> AsyncIterator[str]:
    """流式干预：先缓冲完整生成 → 质检 → 不合格最多重试 3 次（三级阶梯）→ 按原始 token 粒度匀速补推。

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
    prebuilt_rag_sources：与 prebuilt_messages 配套的首轮 RAG 片段，
    供质检重试换片时排除已引用切片；prebuilt_messages 为 None 时内部构建自动获得。
    """
    if prebuilt_messages is not None:
        messages = prebuilt_messages
        first_rag = list(prebuilt_rag_sources or [])
    else:
        messages, _, first_rag, _ = await build_intervention_messages(state)
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

    # 求做法问题要求 ≥2 种不同做法类别
    from app.agents.nodes.triage import detect_method_question
    user_msg = state.get("user_message", "")
    min_methods = 2 if detect_method_question(user_msg) else 1
    # 质检不合格 → 重试阶梯（与非流式 intervention_node 同口径）：全部不合格则沿用首次回复
    max_retries = len(RETRY_MODES)
    alt_chunks: list | None = None
    for attempt in range(1, max_retries + 1):
        if check_reply_quality(text, history, min_method_categories=min_methods, min_len=50):
            break
        logger.info("intervention: quality check failed, retry %d/%d (stream)", attempt, max_retries)
        mode = RETRY_MODES[attempt - 1]
        if mode == "context_trim":
            trim_state = {
                **state,
                "history": [h for h in (state.get("history") or []) if h.get("role") == "user"],
            }
            if alt_chunks is None:
                alt_chunks = await _refresh_rag_sources(state, first_rag)
            retry_base, _, _, _ = await build_intervention_messages(trim_state, rag_override=alt_chunks)
        elif mode == "rag_refresh":
            if alt_chunks is None:
                alt_chunks = await _refresh_rag_sources(state, first_rag)
            retry_base, _, _, _ = await build_intervention_messages(state, rag_override=alt_chunks)
        else:
            retry_base = messages
        retry_tokens, retry_failed = await _collect(
            [*retry_base, {"role": "system", "content": build_retry_hint(text, history)}],
            temperature=0.6 if mode == "hint" else 0.7,
        )
        retry_text = "".join(retry_tokens)
        if (
            not retry_failed
            and retry_text.strip()
            and check_reply_quality(retry_text, history, min_method_categories=min_methods, min_len=50)
        ):
            tokens, text = retry_tokens, retry_text
            break
    # 所有重试均不合格：沿用首次回复（tokens/text 未被任何子轮覆盖，无需回退操作）

    async for token in _paced(tokens):
        yield token


async def intervention_node(state: AgentState) -> dict:
    """干预节点（非流式，供 LangGraph graph.ainvoke 调用）。

    流程：
    1. build_intervention_messages 拼 prompt
    2. provider.chat(role="dialog", temp=0.6) 生成共情回应
    3. 回复质检（封闭式问句/逐字重复）→ 不合格走三级重试阶梯（hint/rag_refresh/context_trim）
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
        # 质检不合格 → 附纠正提示重试最多 3 次（三级阶梯）；重试异常或仍不合格则保留首次回复
        history = [
            {"role": h["role"], "content": h["content"]}
            for h in (state.get("history") or [])
            if h.get("role") in ("user", "assistant")
        ][-20:]
        # 求做法问题要求 ≥2 种不同做法类别，避免只给腹式呼吸一种
        from app.agents.nodes.triage import detect_method_question
        user_msg = state.get("user_message", "")
        min_methods = 2 if detect_method_question(user_msg) else 1
        first_reply = reply
        # 质检重试阶梯（最多 3 次，采用首个合格回复；全部不合格保留首次回复，不拿更差的覆盖）：
        # ① hint：同 prompt + 针对性禁令（temp 0.6，0.35 下本地 LoRA 复读加剧）；
        # ② rag_refresh：换 RAG 片段重拼 prompt（排除首轮已引用切片）——多轮复读主因之一
        #    是同一 RAG 片段被逐字照抄，换片段比换措辞更根治；
        # ③ context_trim：终极重试，history 剔除 assistant 历史回复（保留用户消息保连贯）——
        #    实测复读主因是照抄自己此前的回复（历史锚点），剔除后模型无从照抄；
        #    质检仍按真实 history 校验（复读/幻觉归因/闭合问句），不合格照样回退首次回复。
        max_retries = 3
        retry_modes: list[str] = []
        alt_chunks: list | None = None
        for attempt in range(1, max_retries + 1):
            if check_reply_quality(reply, history, min_method_categories=min_methods, min_len=50):
                break
            logger.info("intervention: quality check failed, retry %d/%d", attempt, max_retries)
            decision["llm"]["quality_retry"] = True
            mode = ("hint", "rag_refresh", "context_trim")[attempt - 1]
            retry_modes.append(mode)
            try:
                if mode == "context_trim":
                    trim_state = {
                        **state,
                        "history": [h for h in (state.get("history") or []) if h.get("role") == "user"],
                    }
                    if alt_chunks is None:
                        alt_chunks = await _refresh_rag_sources(state, rag_sources)
                    retry_base, _, _, _ = await build_intervention_messages(trim_state, rag_override=alt_chunks)
                elif mode == "rag_refresh":
                    if alt_chunks is None:
                        alt_chunks = await _refresh_rag_sources(state, rag_sources)
                    retry_base, _, _, _ = await build_intervention_messages(state, rag_override=alt_chunks)
                else:
                    retry_base = messages
                retry = await provider.chat(
                    role="dialog",
                    messages=[
                        *retry_base,
                        {"role": "system", "content": build_retry_hint(first_reply, history)},
                    ],
                    temperature=0.6 if mode == "hint" else 0.7,
                    max_tokens=3000,
                )
                if retry and retry.strip() and check_reply_quality(
                    retry, history, min_method_categories=min_methods, min_len=50
                ):
                    reply = retry
                    break
            except Exception as retry_err:
                logger.warning(
                    "intervention: quality retry %d failed: %s, keep first reply", attempt, str(retry_err)
                )
                break
        decision["llm"]["retry_modes"] = retry_modes
        # 所有重试均不合格：回退首次回复（模型的原始输出，不拿不合格重试覆盖）
        if not check_reply_quality(reply, history, min_method_categories=min_methods, min_len=50):
            reply = first_reply
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
