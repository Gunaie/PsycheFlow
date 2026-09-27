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
from app.agents.nodes.intervention import check_reply_quality
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


# ================= 硬编码显式求助祈使句识别（零 LLM，2026-09-27） =================
# 实测 triage LoRA 把「被起外号…帮帮我」明确求助判为倾诉：训练集求助类全是
# 「想做测评/哪里可以约/有什么量表」渠道型表述，模型没见过「帮帮我」式直接求助。
# 规则：含强求助祈使词 → 视为求助（仅在 LLM 判倾诉时纠偏，咨询/求助原判不动）。
_HELP_PLEA_RE = re.compile(
    r"(帮帮我|救救我|救命|谁来帮帮|谁能帮帮|求你帮|求你们帮|谁来救我|帮我一把|请帮帮我)"
)


def detect_help_plea(message: str) -> bool:
    """硬编码显式求助祈使句识别（零 LLM）：含「帮帮我/救救我」等直接求助词。

    不设长度守卫（求助句可长可短）；与 detect_method_question 互斥由调用顺序保证
    （先把倾诉改判求助，再把方法/服务问句从求助改判咨询）。
    """
    return bool(_HELP_PLEA_RE.search(message or ""))


# ================= 硬编码咨询服务边界问句识别（零 LLM，2026-09-27） =================
# 实测 triage LoRA 把「心理咨询师会告诉学校家长吗」「你这里都能做什么」
# 这类询问咨询服务本身（保密/能力/政策）的问题判为求助。按 TRIAGE_SYSTEM，
# 询问信息属咨询（非寻求测评渠道）。规则：咨询服务边界问句 → 改判咨询。
_SERVICE_Q_RE = re.compile(
    r"(保密|隐私|泄密|泄露)"
    r"|会不会[^，。！？!?；;]{0,6}(告诉|说出去|泄露)"
    r"|告诉[^，。！？!?；;]{0,4}(学校|家长|老师|父母|别人|爸妈)"
    r"|(你这里|你们这里|你这|你们这)[^，。！？!?；;]{0,8}(能做什么|提供什么|有什么服务|能帮什么|能干嘛|有什么用)"
)


def detect_service_question(message: str) -> bool:
    """硬编码咨询服务边界问句识别（零 LLM）：询问保密/能力/政策等服务本身的问题。"""
    return bool(_SERVICE_Q_RE.search(message or ""))


# ================= 硬编码求助渠道识别（零 LLM，2026-09-27） =================
# 完全规则化分诊后，求助类（寻求测评/咨询渠道）不再走 triage-lora，改为硬编码
# 渠道推荐话术直达。覆盖：测评/量表、心理老师/咨询室、援助资源、预约/渠道/平台。
_HELP_CHANNEL_RE = re.compile(
    r"(心理测评|心理评估|做.{0,3}测评|做.{0,3}评估|量表|自评|抑郁自评|焦虑自评|能不能测|能.{0,2}测)"
    r"|(心理老师|心理咨询师|心理咨询室|心理中心|心理咨询|心理援助|援助热线|援助资源)"
    r"|(预约.{0,4}(咨询|心理)|怎么.{0,4}(约|预约)|去哪里.{0,4}(咨询|聊|测)|哪里.{0,4}(咨询|测|聊))"
    r"|(靠谱.{0,4}(渠道|平台|心理咨询)|线上.{0,4}(渠道|平台|咨询))"
)


def detect_help_channel(message: str) -> bool:
    """硬编码求助渠道识别（零 LLM）：寻求测评/咨询渠道/量表的消息。"""
    return bool(_HELP_CHANNEL_RE.search(message or ""))


# 求助渠道快速通道话术（硬编码，零 LLM）
_HELP_CHANNEL_REPLY = (
    "如果你想做正式的心理评估或寻求专业帮助，可以试试这些渠道："
    "①学校心理咨询中心（通常免费、保密）；"
    "②当地精神卫生中心或三甲医院心理科；"
    "③正规线上平台如简单心理、壹点灵。"
    "先从学校心理中心开始通常最方便，预约时直接说想聊聊最近的情绪状态就好。"
)


def _help_fast_path(state: AgentState, trace: list, decisions: dict) -> dict:
    """求助渠道快速通道：硬编码渠道推荐话术，产出 final_reply 直接结束编排（零 LLM）。"""
    decisions["triage"] = {
        "decision": "fast_path_help_channel",
        "type": "keyword_match",
    }
    return {
        "is_crisis": False,
        "triage_intent": "求助",
        "final_reply": _HELP_CHANNEL_REPLY,
        "current_agent": "triage",
        "agent_trace": trace,
        "node_decisions": decisions,
    }


async def _greeting_fast_path(
    state: AgentState, message: str, trace: list, decisions: dict
) -> dict:
    """寒暄快速通道：用轻量 triage 模型生成简短问候回复，产出 final_reply 直接结束编排。

    调用方需自行 try/except：生成失败时回退到正常意图分类/倾诉流程。
    质检：生成后复用 intervention 的 check_reply_quality（闭合问句/多问题/超长），
    不合格附纠正提示重试 1 次，避免寒暄回复漏出"有什么想说的吗？"类闭合问句。
    """
    persona = get_persona(state.get("persona_id"))
    system = (
        build_system_prompt(persona)
        + "\n请简洁地回应用户的打招呼或询问（50字以内），不要开启 RAG 或深度对话。"
        + "\n禁止使用闭合问句（如'有什么想说的吗''你好吗'），用开放式表达收尾，如直接说'我在听'或'随时可以和我说说'。"
    )
    greeting_reply = await provider.chat(
        role="triage",  # 复用轻量模型
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ],
        temperature=0.7,
        max_tokens=100,
    )
    # 质检：闭合问句/多问题/超长 → 重试 1 次（降温提遵循率）
    if not check_reply_quality(greeting_reply, history=None):
        logger.info("triage: greeting quality check failed, retrying once")
        try:
            retry = await provider.chat(
                role="triage",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                    {"role": "system", "content": (
                        "【重试要求】上条回复不合格：可能用了'吗/吧'收尾的闭合问句，"
                        "或提了多个问题，或超过50字。请重新生成：50字以内、最多一个问题、"
                        "禁止闭合问句（不用'吗''吧'收尾），用开放式表达。直接输出回复正文。"
                    )},
                ],
                temperature=0.35,
                max_tokens=100,
            )
            if retry and retry.strip() and check_reply_quality(retry, history=None):
                greeting_reply = retry
        except Exception as e:
            logger.warning("triage: greeting retry failed: %s", str(e))
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

    # 2c. 求助渠道快速通道（零 LLM）：寻求测评/咨询渠道 → 硬编码渠道话术直达。
    # 排除咨询服务边界问句（保密/能力等属咨询信息，非求助渠道）。
    if not detect_service_question(message) and detect_help_channel(message):
        logger.info("triage: help-channel fast-path")
        return _help_fast_path(state, trace, decisions)

    # 2d. 默认倾诉（不再调 triage-lora 意图分类）：
    # 危机/寒暄/求助渠道已硬编码处理，其余一律走倾诉 → intervention(dialog-lora)。
    # 彻底避免加载 triage-lora，首轮只需加载 dialog-lora（4.7GB，可装入 8GB 显存）。
    intent = "倾诉"
    decisions["triage"] = {
        "decision": "default_vent",
        "type": "rule",
        "intent": intent,
    }
    logger.info("triage: default 倾诉 (skip LLM triage)")

    # 2e. 显式求助祈使句纠偏（零 LLM）：「帮帮我」式直接求助 → 求助。
    if detect_help_plea(message):
        logger.info("triage: help-plea override 倾诉→求助")
        decisions["triage"] = {
            "decision": "help_plea_override",
            "type": "keyword_match",
            "original_intent": "倾诉"
        }
        intent = "求助"

    # 2f. 方法问句纠偏（零 LLM）：求助 + 方法问句 → 咨询（求做法非求渠道）。
    if intent == "求助" and detect_method_question(message):
        logger.info("triage: method-question override 求助→咨询")
        decisions["triage"] = {
            "decision": "method_question_override",
            "type": "keyword_match",
            "original_intent": "求助"
        }
        intent = "咨询"

    # 3. 返回分诊结果（危机/寒暄/求助渠道已在上面直达返回，此处为倾诉/求助/咨询）
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
