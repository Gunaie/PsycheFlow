"""Triage 分诊节点：detect_crisis 硬编码短路 + 硬编码寒暄识别 + LLM 意图分类
+ 方法问句纠偏（求助→咨询，修 0.5b 意图误判触发 RAG-skip 的误伤）。

安全原则：detect_crisis_with_words 在任何 LLM 调用前执行，命中即直接设置
is_crisis=true 跳过 LLM（Escalation 节点处理），不进入意图分类 LLM 调用。
寒暄同理：纯打招呼/身份询问短句由 detect_greeting 硬编码识别（零 LLM），
规避 LLM 意图误判（实测"你好，你是谁"被误分类为「求助」，走进完整编排
链路还推送无关 RAG 知识卡片）。
"""
import logging
import re

from app.agents.personas import get_persona, build_system_prompt
from app.agents.prompts import TRIAGE_SYSTEM, TRIAGE_USER_TEMPLATE
from app.agents.state import AgentState
from app.core.llm import provider
from app.core.safety import detect_crisis_with_words

logger = logging.getLogger("psycheflow.agents.triage")

# ================= 硬编码寒暄识别（零 LLM） =================
# 白名单：打招呼 + 身份/能力询问。整句按标点/空白切分后逐段匹配，
# 段尾语气词（呀/吧/吗 等）逐个剥掉再匹配；混有其他内容不命中。
_GREETING_PHRASES = {
    # 打招呼
    "你好", "您好", "嗨", "哈喽", "哈罗", "hi", "hello", "hey",
    "早安", "早上好", "上午好", "中午好", "下午好", "晚上好", "晚安",
    "在吗", "在么", "在不在",
    # 身份/能力询问
    "你是谁", "你到底是谁", "你是什么", "你叫什么", "你叫什么名字", "你名字是什么",
    "你是机器人", "你是ai", "你是人工智能", "你是真人", "你是人", "你是人还是机器",
    "你是助手", "你是心理助手", "你是心理老师", "你是干嘛的", "你是干什么的", "你是做什么的",
    "你能做什么", "你能干什么", "你会做什么", "你会什么", "你会干嘛",
    "介绍一下你自己", "介绍一下你", "你自我介绍一下", "自我介绍",
    "who are you", "what are you",
}

# 段尾可剥的语气词（最多连剥 3 个，如"你是谁呀" → "你是谁"）
_TRAILING_PARTICLES = "的吗呢吧呀啊哦哟哈嘛喽噢喔"
# 段首可剥的客套词（"请问你是谁" → "你是谁"）
_LEADING_COURTESY = ("请问", "想请问", "想问", "请")

# 按非字母数字汉字切分（标点/空白/emoji 均为分隔符）
_SEGMENT_SPLIT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _seg_is_greeting(seg: str) -> bool:
    """单段是否为寒暄短语：剥段首客套词 + 段尾语气词后匹配白名单。"""
    for prefix in _LEADING_COURTESY:
        if seg.startswith(prefix) and len(seg) > len(prefix):
            seg = seg[len(prefix):]
            break
    for _ in range(3):
        if seg in _GREETING_PHRASES:
            return True
        if len(seg) > 1 and seg[-1] in _TRAILING_PARTICLES:
            seg = seg[:-1]
        else:
            return False
    return False


def detect_greeting(message: str) -> bool:
    """硬编码寒暄识别（零 LLM）：整句由纯打招呼/身份询问短语构成才命中。

    规则：
    - 按标点/空白切分后逐段匹配白名单，允许段尾语气词（"你是谁呀"）
    - 混有任何其他内容不命中（"你好，我最近压力大"），交回 LLM 意图分类
    - 长度 > 30 直接排除（纯寒暄短句不会太长）
    """
    text = (message or "").strip().lower()
    if not text or len(text) > 30:
        return False
    # 整句先直接匹配（覆盖 "who are you" 等含空格的英文短语，避免被切段拆散）
    if _seg_is_greeting(text):
        return True
    segments = [s for s in _SEGMENT_SPLIT_RE.split(text) if s]
    return bool(segments) and all(_seg_is_greeting(s) for s in segments)


# ================= 硬编码方法问句识别（零 LLM，2026-09-08） =================
# 实测 0.5b triage 把「怎么缓解焦虑」误判为「求助」，触发 intervention 对求助
# 意图的 RAG-skip，导致缓解方法知识卡被拦截、答非所问反问（检索实测 dist=0.45
# 内容就在库里）。方法问句（求做法/求建议）按 TRIAGE_SYSTEM 定义属「咨询」。
# 采用最小侵入纠偏：仅当 LLM 判为求助且方法问句命中时改判咨询；LLM 判倾诉/
# 咨询时保持原判（倾诉骨架同样给做法且 RAG 不跳过，无需干预）。
_METHOD_Q_RE = re.compile(
    r"(怎么|如何|怎样)[^，。！？!?；;]{0,10}(缓解|改善|克服|应对|调节|治疗|解决)"
    r"|(缓解|改善|克服|应对|调节|解决)[^，。！？!?；;]{0,8}(怎么办|的方法|的办法)"
    r"|怎么办|有什么办法|有什么方法"
)


def detect_method_question(message: str) -> bool:
    """硬编码方法问句识别（零 LLM）：询问缓解/改善/应对等做法的消息。

    守卫：≤30 字（长句多为主线倾诉附带提问，交回 LLM 与倾诉骨架处理）。
    """
    text = (message or "").strip()
    if not text or len(text) > 30:
        return False
    return bool(_METHOD_Q_RE.search(text))


async def _greeting_fast_path(
    state: AgentState, message: str, trace: list, decisions: dict
) -> dict:
    """寒暄快速通道：用轻量 triage 模型生成简短问候回复，产出 final_reply 直接结束编排。

    调用方需自行 try/except：生成失败时回退到正常意图分类/倾诉流程。
    """
    persona = get_persona(state.get("persona_id"))
    greeting_reply = await provider.chat(
        role="triage",  # 复用轻量模型
        messages=[
            {"role": "system", "content": build_system_prompt(persona) + "\n请简洁地回应用户的打招呼或询问，不要开启 RAG 或深度对话。"},
            {"role": "user", "content": message},
        ],
        temperature=0.7,
        max_tokens=100,
    )
    return {
        "is_crisis": False,
        "triage_intent": "寒暄",
        "final_reply": greeting_reply,
        "current_agent": "triage",
        "agent_trace": trace,
        "node_decisions": decisions
    }


async def triage_node(state: AgentState) -> dict:
    """分诊节点。

    流程：
    1. detect_crisis_with_words 硬编码前置扫描（零 LLM），命中 → is_crisis=true
    2. detect_greeting 硬编码寒暄识别（零 LLM），命中 → 快速通道直达回复
    3. 未命中 → 调 provider.chat(role="triage", temp=0.1) 分类意图
    4. LLM 分类为寒暄 → 同样走快速通道
    """
    message = state.get("user_message", "")
    trace = state.get("agent_trace", []) + ["triage"]
    decisions = state.get("node_decisions", {})

    # 1. 前置硬编码危机扫描
    is_crisis, detected_words = detect_crisis_with_words(message)

    if is_crisis:
        logger.info("triage: crisis hit, words=%s, skip LLM", detected_words)
        decisions["triage"] = {
            "decision": "crisis_detected",
            "type": "keyword_match",
            "detected_words": detected_words
        }
        return {
            "is_crisis": True,
            "crisis": True,
            "detected_words": detected_words,
            "triage_intent": "危机",
            "current_agent": "triage",
            "agent_trace": trace,
            "node_decisions": decisions
        }

    # 2. 硬编码寒暄识别（零 LLM）：纯寒暄短句直接走快速通道，规避 LLM 意图误判
    if detect_greeting(message):
        logger.info("triage: greeting hit by hardcoded rule, fast-path")
        decisions["triage"] = {
            "decision": "fast_path_greeting",
            "type": "keyword_match"
        }
        try:
            return await _greeting_fast_path(state, message, trace, decisions)
        except Exception as e:
            logger.warning("triage: greeting fast-path failed: %s, fallback to LLM classification", e)
            decisions["triage"] = {
                "decision": "greeting_fast_path_fallback",
                "reason": str(e)
            }

    # 3. LLM 意图分类
    intent = ""
    try:
        user_prompt = TRIAGE_USER_TEMPLATE.format(message=message)
        reply = await provider.chat(
            role="triage",
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=50,
        )
        intent = reply.strip()
        # 兜底：LLM 幻觉出非 5 类标签 → 默认走倾诉
        if intent not in ("寒暄", "求助", "倾诉", "咨询", "危机"):
            logger.warning("triage: unexpected intent %r, fallback to 倾诉", intent)
            intent = "倾诉"
        if "triage" not in decisions:
            decisions["triage"] = {
                "decision": "intent_classified",
                "intent": intent
            }
    except Exception as e:
        logger.warning("triage: LLM failed %s, fallback to 倾诉", str(e))
        intent = "倾诉"
        decisions["triage"] = {
            "decision": "fallback",
            "reason": str(e),
            "intent": intent
        }

    # 3.5 方法问句纠偏（零 LLM 二次校验）：LLM 判求助且方法问句命中 → 改判咨询。
    # 修 0.5b 把「怎么缓解焦虑」误判求助触发 RAG-skip 的误伤；判倾诉/咨询保持原判。
    if intent == "求助" and detect_method_question(message):
        logger.info("triage: method-question override 求助→咨询")
        decisions["triage"] = {
            "decision": "method_question_override",
            "type": "keyword_match",
            "original_intent": "求助"
        }
        intent = "咨询"

    # 4. 极速直达路径：LLM 分类为寒暄 → 快速通道（跳过后续节点）
    if intent == "寒暄":
        logger.info("triage: greeting detected by LLM, using fast-path")
        decisions["triage"] = {
            "decision": "fast_path_greeting",
            "type": "llm_classified",
            "intent": intent,
            "model": "qwen2.5:0.5b"
        }
        try:
            return await _greeting_fast_path(state, message, trace, decisions)
        except Exception as e:
            logger.warning("triage: greeting reply failed: %s, fallback to 倾诉", e)
            intent = "倾诉"
            decisions["triage"] = {
                "decision": "fallback",
                "reason": str(e),
                "intent": intent
            }

    logger.info("triage: intent=%s", intent)
    return {
        "is_crisis": False,
        "crisis": False,
        "detected_words": [],
        "triage_intent": intent,
        "current_agent": "triage",
        "agent_trace": trace,
        "node_decisions": decisions
    }
