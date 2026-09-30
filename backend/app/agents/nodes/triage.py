"""Triage 分诊节点（2026-09-30 起完全规则化，零 LLM）。

路由顺序（任何 LLM 调用前先跑硬编码安全规则）：
1. detect_crisis_with_words 危机词前置短路 → escalation（安全底线，不可降级）
2. detect_greeting 寒暄识别 → 静态寒暄话术直达（不加载任何模型）
3. 咨询规则：服务边界问句 / 方法问句 / 知识问句 → intent=咨询（intervention 先答问骨架 + RAG）
4. 求助渠道规则：测评/量表/咨询渠道/援助资源 → 静态渠道话术直达
5. 显式求助祈使（帮帮我/救救我）→ intent=求助（intervention 求助骨架，跳过 RAG）
6. 其余默认 → intent=倾诉（intervention 先接情绪骨架 + RAG）

历史：2026-09-27 前为 triage-lora 五分类 + 规则纠偏；为消除 8GB 显存下
triage-lora ↔ dialog-lora 双模型反复加载（单轮 56-97s），改为全规则路由，
生产路径不再调用 triage 模型。
"""
import logging
import re

from app.agents.personas import get_persona
from app.agents.state import AgentState
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


# ================= 硬编码方法问句识别（零 LLM，2026-09-08；2026-09-30 升级为一级路由） =================
# 求做法/求建议的消息按 TRIAGE_SYSTEM 定义属「咨询」，intervention 用先答问骨架 + RAG。
_METHOD_Q_RE = re.compile(
    r"(怎么|如何|怎样)[^，。！？!?；;]{0,10}(缓解|改善|克服|应对|调节|调整|治疗|解决|控制|沟通|专注|自律|帮)"
    r"|(缓解|改善|克服|应对|调节|解决)[^，。！？!?；;]{0,8}(怎么办|的方法|的办法)"
    r"|怎么办|有什么办法|有什么方法"
)


def detect_method_question(message: str) -> bool:
    """硬编码方法问句识别（零 LLM）：询问缓解/改善/应对等做法的消息。

    守卫：≤35 字（长句多为主线倾诉附带提问，走倾诉骨架处理）。
    """
    text = (message or "").strip()
    if not text or len(text) > 35:
        return False
    return bool(_METHOD_Q_RE.search(text))


# ================= 硬编码知识问句识别（零 LLM，2026-09-30） =================
# 纯知识/概念问句（什么是抑郁/正常吗/需要吃药吗/多少次）属咨询，先答问骨架。
_KNOWLEDGE_Q_RE = re.compile(
    r"(什么是|是什么|指什么|有什么区别|科学依据|正常吗|正常的吗"
    r"|需要.{0,6}吗|怎样才算|怎么才算|包括哪些|哪些类型|多少次|几个疗程)"
)


def detect_knowledge_question(message: str) -> bool:
    """硬编码知识问句识别（零 LLM）：询问概念/机制/标准/疗程等知识的消息。

    守卫：≤32 字（长句多为倾诉中附带提问，走倾诉骨架处理）。
    """
    text = (message or "").strip()
    if not text or len(text) > 32:
        return False
    return bool(_KNOWLEDGE_Q_RE.search(text))


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
    r"(心理测评|心理评估|做.{0,3}测评|做.{0,3}评估|量表|自评|抑郁自评|焦虑自评|能不能测|能.{0,2}测"
    r"|做什么检查|什么检查|怀疑自己是不是|想知道自己是不是|是不是有.{0,8}(焦虑|抑郁|心理问题))"
    r"|(心理老师|心理咨询师|心理咨询室|心理中心|心理咨询|心理援助|援助热线|援助资源|专业的人)"
    r"|(预约.{0,4}(咨询|心理)|怎么.{0,4}(约|预约)|去哪里.{0,4}(咨询|聊|测)|哪里.{0,6}(咨询|测|聊|去|预约))"
    r"|(靠谱.{0,4}(渠道|平台|心理咨询)|线上.{0,4}(渠道|平台|咨询))"
)


def detect_help_channel(message: str) -> bool:
    """硬编码求助渠道识别（零 LLM）：寻求测评/咨询渠道/量表的消息。

    注意：咨询规则（方法/服务/知识问句）在调用方先于本规则判定，
    「心理咨询一般要做多少次」等以心理咨询为话题背景的知识句不会误入。
    """
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


# ================= 硬编码寒暄静态话术（零 LLM，2026-09-30） =================
# 身份/能力询问短语（命中则回自我介绍话术，否则回纯打招呼话术）
_IDENTITY_Q_RE = re.compile(
    r"(你是谁|你是什么|你叫什么|你名字|你是机器人|你是ai|你是人工智能|你是真人|你是人"
    r"|你是助手|你是心理|你是干嘛|你是干什么|你是做什么|你能做什么|你能干什么"
    r"|你会做什么|你会什么|你会干嘛|介绍.{0,2}你|自我介绍|who are you|what are you)"
)


def greeting_static_reply(message: str, persona_name: str) -> str:
    """寒暄静态话术（零 LLM）：身份/能力询问→自我介绍，纯打招呼→温暖应答。

    全部为开放式陈述，不含闭合问句，长度 ≤120，满足 check_reply_quality 上限。
    """
    if _IDENTITY_Q_RE.search((message or "").lower()):
        return (
            f"你好呀，我是{persona_name}，一个陪你聊天的心理支持伙伴。"
            "学习压力、情绪困扰、睡不着，都可以和我说；"
            "想做心理评估，我也能告诉你学校和专业渠道。"
        )
    return (
        f"你好呀，我在的，我是{persona_name}。"
        "最近有什么开心或烦心的事，都可以随时和我说说，不用着急，慢慢聊就好。"
    )


def _greeting_fast_path(state: AgentState, message: str, trace: list, decisions: dict) -> dict:
    """寒暄快速通道：硬编码静态话术，产出 final_reply 直接结束编排（零 LLM、零失败回退）。"""
    persona = get_persona(state.get("persona_id"))
    return {
        "is_crisis": False,
        "triage_intent": "寒暄",
        "final_reply": greeting_static_reply(message, persona.name),
        "current_agent": "triage",
        "agent_trace": trace,
        "node_decisions": decisions,
    }


async def triage_node(state: AgentState) -> dict:
    """分诊节点（全规则，零 LLM）。

    路由：危机短路 → 寒暄静态直达 → 咨询规则（服务/方法/知识问句）
    → 求助渠道静态直达 → 求助祈使 → 默认倾诉。
    """
    message = state.get("user_message", "")
    trace = state.get("agent_trace", []) + ["triage"]
    decisions = state.get("node_decisions", {})

    # 1. 前置硬编码危机扫描
    is_crisis, detected_words = detect_crisis_with_words(message)

    if is_crisis:
        logger.info("triage: crisis hit, words=%s", detected_words)
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

    # 2. 硬编码寒暄识别 → 静态话术直达（零 LLM）
    if detect_greeting(message):
        logger.info("triage: greeting hit, static fast-path")
        decisions["triage"] = {
            "decision": "fast_path_greeting",
            "type": "keyword_match"
        }
        return _greeting_fast_path(state, message, trace, decisions)

    # 3. 咨询规则（先于求助渠道判定）：
    #    咨询服务边界（保密/能力）、方法问句（怎么缓解/怎么办）、知识问句（什么是/正常吗/多少次）
    if detect_service_question(message):
        intent, reason = "咨询", "service_question"
    elif detect_method_question(message):
        intent, reason = "咨询", "method_question"
    elif detect_knowledge_question(message):
        intent, reason = "咨询", "knowledge_question"
    elif detect_help_channel(message):
        # 4. 求助渠道：寻求测评/量表/咨询渠道/援助资源 → 静态渠道话术直达
        logger.info("triage: help-channel fast-path")
        return _help_fast_path(state, trace, decisions)
    elif detect_help_plea(message):
        # 5. 显式求助祈使（帮帮我/救救我）→ intervention 求助骨架
        intent, reason = "求助", "help_plea"
    else:
        # 6. 默认倾诉
        intent, reason = "倾诉", "default_vent"

    decisions["triage"] = {
        "decision": reason,
        "type": "keyword_match" if reason != "default_vent" else "rule",
        "intent": intent,
    }
    logger.info("triage: rule=%s intent=%s", reason, intent)
    return {
        "is_crisis": False,
        "crisis": False,
        "detected_words": [],
        "triage_intent": intent,
        "current_agent": "triage",
        "agent_trace": trace,
        "node_decisions": decisions
    }
