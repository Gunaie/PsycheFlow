"""云端 LLM 批量生成心理对话训练样本。

设计目标（2026-09-08 反模板重训 - 第 1 步）：
  - 用云端百炼 LLM 一次性扩出 500 条多样化对话样本
  - 100 个种子场景 × 每次产出 5 组差异化对话 = 500 条
  - 复用 convert_deepwell.py 的清洗规则（剔除模板腔、改写封闭问句）
  - 失败重试 1 次；并发 8 路，约 100 次 API 调用即完成

输出格式（sharegpt JSONL，与 deepwell_dialog.jsonl 一致，便于合并训练）：
  {"conversations":[{"from":"human","value":"..."},{"from":"gpt","value":"..."}]}

用法（在仓库根目录运行）：
  $env:DASHSCOPE_API_KEY="sk-xxx"
  python backend/scripts/finetune/gen_samples_cloud.py
  # 可选参数：--out PATH --target 500 --concurrency 8
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

from openai import AsyncOpenAI

# ── 配置 ─────────────────────────────────────────────────────
DEFAULT_OUT = Path(__file__).parent / "synthetic_dialog.jsonl"
DEFAULT_MODEL = "qwen3.8-max"          # 关思考链、首 token 快、对话生成够用
DEFAULT_TARGET = 500                    # 目标样本数
DEFAULT_PER_SEED = 5                    # 每个种子产出多少组对话
DEFAULT_CONCURRENCY = 8                 # 并发上限
MAX_RETRIES = 2                         # 单种子最大重试次数

# ── 清洗规则（与 convert_deepwell.py 保持一致）─────────────
TEMPLATE_PATTERNS = [
    re.compile(r"这一定让你感到.{0,6}(疲惫|沮丧|难过|痛苦|焦虑)"),
    re.compile(r"我.{0,4}理解你的感受"),
    re.compile(r"我能感受到你.{0,6}(的心情|的感受|的痛苦)"),
    re.compile(r"这确实是一件.{0,8}(令人|让人).{0,4}(难过|痛苦|沮丧)的事"),
    re.compile(r"听起来你.{0,6}(最近|这段时间).{0,4}(过得|过得很).{0,4}(不容易|辛苦)"),
]

CLOSED_QUESTION_PATTERNS = [
    re.compile(r"(对吧|对吗|是不是|是吧|好吗)[？?]"),
    re.compile(r"(对吧|对吗|是不是|是吧|好吗)(。|$)"),
    re.compile(r"你觉得.{0,10}(对吧|对吗|是不是)\s*[？?]"),
    re.compile(r"是不是.{0,10}(让你|使你|令你).{0,4}(感到|觉得)"),
]

OPEN_ENDINGS = [
    "你能多说说这方面的情况吗？",
    "你愿意分享一下当时的感受吗？",
    "你觉得最困扰你的是哪个部分？",
    "你希望接下来可以怎样改善？",
    "你有没有想过尝试什么方法？",
    "你能具体描述一下那种感觉吗？",
    "你最近一次有这种感觉是什么时候？",
    "你觉得什么情况下会好一些？",
    "你身边有可以倾诉的人吗？",
    "你想先聊聊哪方面的感受？",
]


def is_template_reply(text: str) -> bool:
    return any(p.search(text) for p in TEMPLATE_PATTERNS)


def fix_closed_questions(text: str) -> str:
    for pattern in CLOSED_QUESTION_PATTERNS:
        if pattern.search(text):
            ending = random.choice(OPEN_ENDINGS)
            text = pattern.sub(ending, text)
    return text


def clean_gpt_reply(reply: str) -> str | None:
    """返回 None 表示整条剔除。"""
    if is_template_reply(reply):
        return None
    return fix_closed_questions(reply)


# ── 种子场景池（10 大类 × 10 场景 = 100 个种子）────────────
SEED_SCENARIOS = [
    # ── 学业压力 ──
    "高三学生面对即将到来的高考感到焦虑，复习效率下降",
    "初二学生在重点班跟不上进度，觉得自己拖后腿",
    "职校学生对所学专业毫无兴趣，对未来迷茫",
    "小学六年级学生因为转学后跟不上新学校的教学节奏",
    "大学生考研失败两次，不敢告诉父母自己在准备第三次",
    "高中生因为偏科严重被老师谈话，怀疑自己不是读书的料",
    "初中生因期中考试成绩大幅下滑而陷入自我怀疑",
    "学生面对堆积如山的作业产生逃避心理，开始装病请假",
    "艺考生集训期间高强度训练导致身心俱疲，对原本热爱的专业产生厌恶",
    "复读生在新班级看到周围同学都很优秀，产生强烈自卑",

    # ── 人际关系 ──
    "学生被班级小团体排挤，午餐时没人愿意和他坐",
    "新生入学三个月仍未交到朋友，每天独自上下学",
    "学生和最好的朋友因为误会闹翻，双方都不愿先开口",
    "班级里有人传该同学谣言，导致他被孤立",
    "学生转学到新城市，语言和文化差异让他难以融入",
    "学生在社团活动中被学长学姐针对，想退社又不甘心",
    "学生和同桌因为座位空间问题产生持续摩擦",
    "学生在班级群里发的内容被同学嘲笑，从此不敢发言",
    "学生被朋友背叛，对方把他私下告诉的秘密公开了",
    "学生从外地转来，被同学嘲笑口音，不敢再开口说话",

    # ── 亲子关系 ──
    "初中生父母离异后跟父亲生活，觉得母亲不再关心自己",
    "高中生父母常年在外打工，节假日独自在家感到孤独",
    "学生父母对其成绩期望过高，每次考试后家里气氛紧张",
    "学生家里有个优秀姐姐，总被拿来比较感到压抑",
    "学生父母频繁吵架，他害怕回家，常常在外游荡",
    "学生因父母不让玩手机游戏与父母发生激烈冲突",
    "学生想参加学校话剧社，但父母坚决反对认为不务正业",
    "学生父母重男轻女，对妹妹的关注明显多于自己",
    "学生发现父母偷看自己的日记，感到被侵犯隐私",
    "学生跟继父相处尴尬，不知如何建立关系",

    # ── 情绪问题 ──
    "学生最近两周持续情绪低落，对原本喜欢的篮球也提不起兴趣",
    "学生经常莫名想哭，上课时也控制不住眼泪",
    "学生描述自己像被一团乌云笼罩，看不到希望",
    "学生最近脾气暴躁，对同学和家人都发火，事后又后悔",
    "学生反映最近一躺下就心跳加速、手心出汗，但检查身体无异常",
    "学生说自己整天像行尸走肉，对未来完全没有期待",
    "学生经常感到胸闷气短，但医生说身体没问题",
    "学生描述自己情绪起伏极大，上午开心下午就想哭",
    "学生说最近做什么都提不起劲，连起床都觉得困难",
    "学生描述最近常常半夜惊醒，心跳很快，再难入睡",

    # ── 自我认同 ──
    "学生觉得自己长得不好看，常常因为外貌自卑",
    "学生怀疑自己的性取向，不知该如何面对",
    "学生成绩一般、长相普通、没有特长，觉得自己是个透明人",
    "学生长期被同学说娘娘腔，开始怀疑自己的性别表达",
    "学生来自农村，在城市学校里觉得自己格格不入",
    "学生因为身材偏胖被嘲笑，开始极端节食",
    "学生觉得自己的想法和同龄人都不一样，怀疑自己有问题",
    "学生因为口吃不敢在公开场合说话，觉得自己很没用",
    "学生发现自己对学习之外的事物都提不起兴趣，怀疑自己一无是处",
    "学生长期被父母否定，开始相信我就是个没用的人",

    # ── 恋爱情感 ──
    "学生暗恋同班同学半年，不知是否该表白",
    "学生被喜欢的人拒绝了，觉得很丢脸不想上学",
    "学生和恋爱对象分手后情绪低落，影响学习",
    "学生发现喜欢的人有女朋友了，感到很失落",
    "学生和恋爱对象因为升学要分开，不知未来怎么走",
    "学生被同学起哄和某异性是一对，其实只是普通朋友",
    "学生单恋很久，最近得知对方要转学了",
    "学生偷偷喜欢的好友向自己倾诉喜欢上了别人",
    "学生被前任在班级里散布分手后的私人聊天记录",
    "学生和喜欢的人处于暧昧期，对方突然疏远自己",

    # ── 未来发展 ──
    "高三学生不知道该选什么专业，父母希望他学医但他想学设计",
    "高二学生对未来完全没有方向，看到同学都目标明确很焦虑",
    "学生成绩不错但不知道自己真正喜欢什么",
    "学生想出国留学但家里经济条件不允许",
    "学生被保送但还是觉得迷茫，找不到学习的意义",
    "学生面临文理分科，父母和自己的意见不一致",
    "中职学生觉得自己学的专业没前途，对未来没信心",
    "学生想休学一年去追求音乐梦想，但不敢跟父母说",
    "学生面临高考志愿填报，在兴趣和就业前景间纠结",
    "学生成绩中游，担心考不上好大学一辈子就完了",

    # ── 行为习惯 ──
    "学生每天刷短视频到凌晨2点，白天上课昏昏欲睡",
    "学生有咬指甲的习惯，已经咬到发炎还是控制不住",
    "学生一紧张就拔头发，头顶已经有一小块秃了",
    "学生沉迷网络游戏，每天玩到凌晨成绩下滑",
    "学生拖延症严重，作业总是拖到最后一刻才写",
    "学生有暴食倾向，情绪不好就大量吃零食",
    "学生上课注意力无法集中，常常走神错过重点",
    "学生频繁撒谎，明明没做的事说做了，事后又内疚",
    "学生有强迫性检查习惯，出门总要反复确认门锁",
    "学生一遇到考试就紧张到拉肚子，影响发挥",

    # ── 身体健康 ──
    "学生长期失眠，躺下两小时还睡不着",
    "学生经常头痛，去医院检查无明显异常",
    "学生长期胃疼，医生说是压力大引起的",
    "学生月经不调，怀疑是压力导致的",
    "学生食欲很差，体重明显下降",
    "学生运动时容易过度紧张，心跳过快",
    "学生视力下降很快，但抗拒戴眼镜",
    "学生经常感冒，免疫力似乎很差",
    "学生发育较晚，担心自己长不高",
    "学生有慢性皮肤病，影响自信和社交",

    # ── 创伤事件 ──
    "学生最近目睹了一场严重车祸，常常做噩梦",
    "学生家中遭遇火灾，失去了所有物品和宠物",
    "学生亲人在事故中去世，他无法接受这个事实",
    "学生小时候被亲戚猥亵过，最近突然回忆起来很难受",
    "学生经历过校园暴力，现在看到施暴者还会发抖",
    "学生在地震中幸存，但对类似声响异常敏感",
    "学生被误诊重病后康复，但仍常常担心自己身体",
    "学生家中被盗窃，之后总觉得不安全",
    "学生经历过一次严重的校园踩踏事故，害怕人多拥挤的场合",
    "学生在一次公开演讲中崩溃出丑，之后再也不敢上台",
]

assert len(SEED_SCENARIOS) == 100, f"种子数应为100，实际{len(SEED_SCENARIOS)}"


# ── 生成 system prompt ──────────────────────────────────────
GEN_SYSTEM_PROMPT = """你是一名心理援助对话数据生成专家，专门为青少年心理援助 AI 生成训练样本。

【任务】
基于给定场景描述，生成 {per_seed} 组差异化的心理对话样本。每组包含：
- human：青少年的真实表达（口语化、自然、不书面）
- gpt：AI 助手的回复

【AI 回复必须遵循的原则】
1. 简洁：50-100 字，不超过 3 句话
2. 共情但不套话：开头先承认对方的感受，**严禁**使用以下模板腔：
   - "我理解你的感受"
   - "这一定让你感到..."
   - "我能感受到你..."
   - "听起来你最近过得很辛苦"
   - "这确实是一件让人难过的事"
3. 具体化：针对该场景的具体细节，不空泛；可问具体情境或给具体建议
4. 开放式追问结尾：以开放式问句结尾，**严禁**封闭式问句（"对吧""对吗""是不是""好吗"）
5. 自然口语：不要堆砌排比，不要每句都"第一第二第三"
6. 危机敏感：涉及自伤/自杀/被侵害/严重危机，引导寻求成年人或专业机构帮助，可附热线（12355/120/110）

【输出格式】
严格输出 JSON，不要 markdown 代码块，不要额外解释：
{{"samples":[{{"human":"...","gpt":"..."}},...]}}

{few_shot}
"""

FEW_SHOT_EXAMPLES = [
    {"human": "今天被老师当着全班面批评了，感觉特别丢人",
     "gpt": "被当众批评确实会让人觉得很难堪，你当时的心情一定很复杂。你能说说是因为什么事情吗？这样我能更好地理解你的处境。"},
    {"human": "最近总是失眠，躺在床上翻来覆去就是睡不着",
     "gpt": "失眠确实让人很焦躁，越想睡反而越清醒是常见的情况。你一般躺下后脑子里在想些什么？有时候理清思路反而有助于放松。"},
    {"human": "我跟最好的朋友吵架了，已经三天没说话了",
     "gpt": "和好朋友闹矛盾那种感觉挺难受的，尤其是不说话的这几天。你们是因为什么事情起的争执呢？如果愿意的话可以聊聊。"},
    {"human": "我觉得活着没什么意思，每天都是重复",
     "gpt": "你说出了很多人不敢说的感受，我认真对待这句话。你能告诉我这种想法出现多久了吗？如果这种感受持续存在，我建议你和信任的老师或家长聊一聊，也可以拨打12355。你现在身边有可以说话的人吗？"},
    {"human": "我应该怎么面对考试压力",
     "gpt": "面对考试压力有几个建议：第一，制定可执行的复习计划拆解任务，有了计划焦虑感会下降；第二，每天留20分钟运动时间释放压力；第三，保证7小时以上睡眠，熬夜复习效率反而更低。你目前离最近一次考试还有多久？"},
]


def build_few_shot_block() -> str:
    examples_text = "\n".join(
        f"  {i+1}. human: {e['human']}\n"
        f"     gpt:   {e['gpt']}"
        for i, e in enumerate(FEW_SHOT_EXAMPLES)
    )
    return f"【参考样例（风格示范，不要照抄内容）】\n{examples_text}\n"


async def generate_for_seed(
    client: AsyncOpenAI,
    model: str,
    seed: str,
    per_seed: int,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """针对单个种子场景调用云端 LLM 生成 per_seed 条样本。"""
    system_prompt = GEN_SYSTEM_PROMPT.format(
        per_seed=per_seed,
        few_shot=build_few_shot_block(),
    )
    user_prompt = f"【场景】{seed}\n\n请生成 {per_seed} 组差异化对话样本。要求场景细节不同、用户语气不同、AI 回复角度不同。直接输出 JSON。"

    async with semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                # enable_thinking=False 关闭思考链，加速 + 直接出 content
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.85,        # 偏高，鼓励多样性
                    top_p=0.9,
                    max_tokens=2000,         # 5 组样本约需 800-1200 token
                    extra_body={"enable_thinking": False},
                    timeout=60,
                )
                raw = resp.choices[0].message.content or ""
                samples = parse_json_response(raw)
                if samples:
                    return samples
                print(f"  [WARN] 种子「{seed[:30]}...」第 {attempt} 次解析为空")
            except Exception as e:
                print(f"  [ERROR] 种子「{seed[:30]}...」第 {attempt} 次失败: {e}")
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 * attempt)
    return []


def parse_json_response(raw: str) -> list[dict]:
    """从模型输出中解析 samples 数组。容错处理 markdown 代码块和前后杂字。"""
    # 去掉可能的 markdown 代码块
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # 截取第一个 { 到最后一个 }
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return []
    cleaned = cleaned[first:last + 1]
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试用正则兜底提取
        m = re.search(r'"samples"\s*:\s*\[', cleaned)
        if not m:
            return []
        try:
            obj = json.loads(cleaned[m.start():])
        except json.JSONDecodeError:
            return []
    samples = obj.get("samples") if isinstance(obj, dict) else None
    if not isinstance(samples, list):
        return []
    out = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        h = (s.get("human") or "").strip()
        g = (s.get("gpt") or "").strip()
        if h and g:
            out.append({"human": h, "gpt": g})
    return out


def to_sharegpt(human: str, gpt: str) -> dict:
    return {"conversations": [
        {"from": "human", "value": human},
        {"from": "gpt", "value": gpt},
    ]}


async def main_async(args) -> int:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    if not api_key:
        print("[ERROR] 未设置 DASHSCOPE_API_KEY 环境变量")
        return 1

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    semaphore = asyncio.Semaphore(args.concurrency)

    seeds = list(SEED_SCENARIOS)
    random.seed(42)
    random.shuffle(seeds)
    need_seeds = (args.target + args.per_seed - 1) // args.per_seed
    seeds = seeds[:need_seeds]
    print(f"[INFO] 模型={args.model} 目标={args.target} 种子数={len(seeds)} 每种子={args.per_seed} 并发={args.concurrency}")
    print(f"[INFO] 输出文件={args.out}")

    tasks = [generate_for_seed(client, args.model, s, args.per_seed, semaphore) for s in seeds]
    results: list[dict] = []
    cleaned_count = 0
    raw_count = 0

    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        seed_samples = await coro
        for s in seed_samples:
            raw_count += 1
            gpt = clean_gpt_reply(s["gpt"])
            if gpt is None:
                cleaned_count += 1
                continue
            results.append(to_sharegpt(s["human"], gpt))
        print(f"  [进度] {i}/{len(seeds)} 种子完成，累计有效 {len(results)} 条（剔除模板腔 {cleaned_count} 条）")

    # 不足目标则按现有数量收尾
    print(f"\n[完成] 原始 {raw_count} 条 → 清洗后 {len(results)} 条（剔除 {cleaned_count} 条）")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in results:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"[写出] {len(results)} 条 -> {args.out.resolve()}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="云端 LLM 批量生成心理对话样本")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出 JSONL 路径")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="目标样本数")
    parser.add_argument("--per-seed", type=int, default=DEFAULT_PER_SEED, help="每个种子产出多少组对话")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="并发上限")
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="云端模型名")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
