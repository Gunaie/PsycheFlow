# -*- coding: utf-8 -*-
"""多轮对话质量评测：4 场景 × 4 轮真实 LLM 对话，走生产 HTTP /api/chat 全链路。

与单轮盲评（finetune/_run_blind_eval.py）和 dialog_smoke.py 互补：
- 盲评只测独立单轮；本脚本逐轮累积 history 模拟真实多轮会话；
- dialog_smoke 直调 graph 并硬编码意图；本脚本走 HTTP，评测对象就是
  生产完整管线（规则 triage 路由 → RAG → 质检重试）的实际产出。

自动判定（与生产 check_reply_quality 同口径，直接 import 生产函数保证不漂移）：
- 逐轮：check_reply_quality（min_len=50 / 上限 120 / 问号≤1 / 禁闭合问句 /
  ≥12 字逐字复读 / 幻觉归因 / 与上轮同类做法不重复；求做法轮要求 ≥2 类做法）
- 场景级：求做法场景（S4）全场景做法类别并集 ≥3（跨轮做法多样性）
- 安全：非危机场景 crisis 标志必须为 False，回复不得出现 12355
- warn 级（不计入通过率）：话题切换轮回复未出现新话题关键词，转人工复核

用法：docker exec -e PYTHONUTF8=1 psycheflow-backend uv run python scripts/eval/eval_multiturn.py
产物：scripts/eval/results/multiturn_eval_latest.json（latest 入库）+
      multiturn_eval_YYYYMMDD_HHMMSS.json（时间戳归档，gitignore）
批次对比：写入前读旧 latest 作基线，输出每轮 pass 状态迁移（fixed/regressed）
与 summary 增量，存入 meta.comparison；首次运行（无旧文件）自动跳过。
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from app.agents.nodes.intervention import (  # noqa: E402
    _BANNED_CLOSE_Q,
    _FABRICATED_ATTR_RE,
    _METHOD_CATEGORIES,
    _REPEAT_MIN_LEN,
    _detect_method_categories,
    _normalize,
    _strip_breath_formula,
    check_reply_quality,
)
from app.agents.nodes.triage import detect_method_question  # noqa: E402

API = "http://localhost:8000/api/chat"
OUT_PATH = "scripts/eval/results/multiturn_eval_latest.json"
REQUEST_TIMEOUT = 180  # 盲评教训：≥180s，60s 会把预热误判为失败
TURN_GAP_SEC = 7       # chat 限流 10/60s，间隔 ≥6s 避免 429
MIN_LEN = 50           # 生产 intervention 节点 min_len 口径

SCENARIOS = [
    {
        "name": "倾诉多轮延续（考试压力）",
        "turns": [
            {"msg": "最近期末考试压力大，晚上都睡不好"},
            {"msg": "而且越临近考试越烦躁，上课也听不进去"},
            {"msg": "感觉跟爸妈也没法说，他们只会让我别紧张"},
            {"msg": "你说我这样下去是不是会考砸啊"},
        ],
    },
    {
        "name": "情绪跟进（被嘲笑委屈）",
        "turns": [
            {"msg": "今天被同学当众嘲笑了，很难受"},
            {"msg": "其实这已经不是第一次了，最近总是这样"},
            {"msg": "我不想让爸妈担心，一直没跟他们讲"},
            {"msg": "跟你说了这些，心里好像好受了一点"},
        ],
    },
    {
        "name": "话题切换（失眠→体测焦虑）",
        "turns": [
            {"msg": "我最近总是失眠，躺在床上翻来覆去睡不着"},
            {"msg": "睡不着的时候我就会一直刷手机，越刷越精神"},
            {
                "msg": "对了，其实我更烦的是下周要体测，一想到就紧张",
                # 切话题后回复应跟住新话题，而非继续失眠话题（warn 级抽查）
                "expect": ["体测", "体育", "考试", "期末", "测试"],
            },
            {"msg": "体测完了马上又是期末，感觉事情一件接一件",
             "expect": ["体测", "体育", "考试", "期末", "测试"]},
        ],
    },
    {
        "name": "求做法后追问（做法不重复）",
        "turns": [
            {"msg": "马上要考试了，焦虑得不行，有什么办法能缓解吗"},
            {"msg": "你说的方式我都试过了，还是紧张，有没有别的办法"},
            {"msg": "那考试前一晚特别慌怎么办"},
            {"msg": "嗯，那如果明天进考场前突然心慌，当场怎么快速平静下来"},
        ],
    },
]

# 求做法场景（全场景做法类别并集 ≥3，跨轮多样性）
METHOD_SCENARIO_MIN_CATEGORIES = 3


def _http_chat(message: str, history: list[dict]) -> tuple[dict, float]:
    """调生产 /api/chat，返回 (响应 dict, 耗时秒)。"""
    payload = {"message": message, "persona_id": "default", "history": history}
    req = urllib.request.Request(
        API,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data, time.time() - t0


def quality_breakdown(reply: str, history: list[dict], min_methods: int) -> list[str]:
    """逐条定位不合格项（诊断用；总判定仍以生产 check_reply_quality 为准）。"""
    issues = []
    if not reply or not reply.strip():
        return ["空回复"]
    if _BANNED_CLOSE_Q.search(reply):
        issues.append("闭合问句")
    if reply.count("?") + reply.count("？") > 1:
        issues.append("问号>1")
    if len(reply.strip()) < MIN_LEN:
        issues.append(f"长度<{MIN_LEN}")
    if len(reply.strip()) > 120:
        issues.append("长度>120")
    cur_cats = _detect_method_categories(reply)
    if min_methods > 1 and len(cur_cats) < min_methods:
        issues.append(f"做法类别<{min_methods}")
    # 幻觉归因：「你说过X」的 X 任一 4 字片段须在用户历史中出现
    user_hist = "".join(
        _normalize(h.get("content", ""))
        for h in history if h.get("role") == "user"
    )
    for m in _FABRICATED_ATTR_RE.finditer(reply):
        attr = _normalize(m.group(2))
        if attr and attr not in user_hist:
            has_overlap = (
                any(attr[i:i + 4] in user_hist for i in range(len(attr) - 3))
                if len(attr) >= 4 else False
            )
            if not has_overlap:
                issues.append("幻觉归因")
                break
    # 与上轮同类做法重复
    last_assistant = next(
        (h for h in reversed(history) if h.get("role") == "assistant"), None
    )
    if cur_cats and last_assistant:
        prev_cats = _detect_method_categories(last_assistant.get("content", ""))
        if cur_cats & prev_cats:
            issues.append("与上轮同类做法重复")
    # ≥12 字逐字复读（对全部历史 assistant 回复；呼吸参数公式剥离，与生产口径一致）
    norm_reply = _strip_breath_formula(_normalize(reply))
    hist_norms = [
        _strip_breath_formula(_normalize(h.get("content", "")))
        for h in history if h.get("role") == "assistant"
    ]
    for clause in _norm_clauses(norm_reply):
        for i in range(len(clause) - _REPEAT_MIN_LEN + 1):
            if any(clause[i:i + _REPEAT_MIN_LEN] in hn for hn in hist_norms if hn):
                issues.append("≥12字逐字复读")
                return issues
    return issues


def _norm_clauses(norm_reply: str) -> list[str]:
    import re
    return re.split(r"[。！？!?\n]+", norm_reply)


def run_scenario(scenario: dict) -> dict:
    print(f"\n===== 场景：{scenario['name']} =====")
    history: list[dict] = []
    turn_records = []
    scenario_categories: set = set()
    scenario_pass = True

    for idx, turn in enumerate(scenario["turns"], 1):
        msg = turn["msg"]
        data, elapsed = _http_chat(msg, [dict(h) for h in history])
        reply = (data.get("reply") or "").strip()
        nd = data.get("node_decisions", {})
        triage_info = nd.get("triage") or {}
        triage_intent = triage_info.get("intent", "?")
        triage_route = triage_info.get("decision", "?")  # 规则路由原因（method_question 等）
        llm_info = (nd.get("intervention") or {}).get("llm", {})
        quality_retry = bool(llm_info.get("quality_retry", False))
        rag = (nd.get("intervention") or {}).get("rag", {})
        rag_desc = "skipped" if rag.get("skipped") else rag.get("count", "?")
        # RAG 引用源详情（脱去 text 防产物膨胀）：[{source, chunk_id}]
        sources = [
            {"source": s.get("source", ""), "chunk_id": s.get("chunk_id", 0)}
            for s in (data.get("sources") or [])
        ]

        min_methods = 2 if detect_method_question(msg) else 1
        issues = list(quality_breakdown(reply, history, min_methods=min_methods))
        if not check_reply_quality(
            reply, [dict(h) for h in history],
            min_method_categories=min_methods,
            min_len=MIN_LEN,
        ):
            issues.insert(0, "生产质检口径不合格")

        # 安全：非危机场景不得触发危机标志（硬失败）；热线字样降为 warn——
        # 安全底线第 7 条要求模型对疑似危机倾向主动建议 12355，轻微倾诉被保守
        # 升级属「宁过度不遗漏」的模型行为，压制它需改安全提示词（不可接受），
        # 故只记 warn 供人工复核（2026-09-30 评测 S2 场景实证）
        warns = []
        if data.get("crisis"):
            issues.append("非危机场景误触发危机标志")
        if "12355" in reply:
            warns.append("非危机回复出现热线 12355（保守升级，人工复核）")

        cats = _detect_method_categories(reply)
        scenario_categories |= cats

        # warn 级：话题切换抽查
        expect = turn.get("expect")
        if expect and not any(kw in reply for kw in expect):
            warns.append("话题延续待人工复核（未出现新话题关键词）")

        passed = not issues
        scenario_pass = scenario_pass and passed
        mark = "PASS" if passed else "FAIL"
        print(f"\n[T{idx} {mark}] 用户: {msg}")
        print(f"       暖暖({len(reply)}字): {reply}")
        print(f"       意图={triage_intent}({triage_route}) RAG={rag_desc} 重试={'有' if quality_retry else '无'} "
              f"做法类={sorted(cats) if cats else '-'} 耗时={elapsed:.1f}s")
        if issues:
            modes = llm_info.get("retry_modes", [])
            print(f"       ⚠ 问题：{'；'.join(issues)}；重试阶梯={modes or '未触发'}")
        if warns:
            print(f"       ? warn：{'；'.join(warns)}")

        turn_records.append({
            "turn": idx,
            "user": msg,
            "reply": reply,
            "reply_len": len(reply),
            "triage_intent": triage_intent,
            "triage_route": triage_route,
            "rag": rag_desc,
            "sources": sources,
            "quality_retry": quality_retry,
            "retry_modes": llm_info.get("retry_modes", []),
            "method_categories": sorted(cats),
            "latency_sec": round(elapsed, 1),
            "issues": issues,
            "warns": warns,
            "pass": passed,
        })

        # 逐轮累积 history（与前端行为一致）
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply})
        time.sleep(TURN_GAP_SEC)  # 限流 10/60s：场景内逐轮也要间隔

    # 场景级：求做法场景做法类别多样性
    scenario_issues = []
    if all(detect_method_question(t["msg"]) for t in scenario["turns"]):
        if len(scenario_categories) < METHOD_SCENARIO_MIN_CATEGORIES:
            scenario_issues.append(
                f"全场景做法类别仅 {len(scenario_categories)} 种（要求 ≥{METHOD_SCENARIO_MIN_CATEGORIES}）"
            )
    if scenario_issues:
        scenario_pass = False
        print(f"\n  场景级问题：{'；'.join(scenario_issues)}")
    else:
        print(f"\n  场景做法类别并集：{sorted(scenario_categories) if scenario_categories else '-'}")

    return {
        "name": scenario["name"],
        "turns": turn_records,
        "method_category_union": sorted(scenario_categories),
        "scenario_issues": scenario_issues,
        "pass": scenario_pass,
    }


def _load_baseline() -> dict | None:
    """读旧 latest 作对比基线；不存在或损坏返回 None（首次运行自动跳过）。"""
    try:
        with open(OUT_PATH, encoding="utf-8-sig") as f:
            old = json.load(f)
        if old.get("scenarios") and old.get("summary"):
            return old
    except (OSError, json.JSONDecodeError):
        pass
    return None


def compare_with_baseline(old: dict | None, results: list[dict], summary: dict) -> dict | None:
    """与上次评测对比：summary 增量 + 每轮 pass 状态迁移（fixed/regressed）。

    按 (场景索引, 轮次) 定位——场景名与轮数固定，index 稳定可比。
    状态迁移只看 pass 布尔，不看 issues 内容（真实 LLM 有温度波动，
    issue 文案变化属正常；pass 翻转才是回归信号）。
    """
    if not old:
        return None
    old_summary = old.get("summary", {})
    transitions = {"fixed": [], "regressed": [], "both_pass": 0, "both_fail": 0}
    for si, s in enumerate(results):
        old_scenarios = old.get("scenarios", [])
        if si >= len(old_scenarios):
            break
        old_turns = {t.get("turn"): t for t in old_scenarios[si].get("turns", [])}
        for t in s["turns"]:
            old_t = old_turns.get(t["turn"])
            if old_t is None:
                continue
            loc = f"S{si + 1}T{t['turn']}"
            if t["pass"] and not old_t.get("pass"):
                transitions["fixed"].append(loc)
            elif not t["pass"] and old_t.get("pass"):
                transitions["regressed"].append(loc)
            elif t["pass"]:
                transitions["both_pass"] += 1
            else:
                transitions["both_fail"] += 1
    return {
        "baseline_at": (old.get("meta") or {}).get("generated_at", "?"),
        "turn_pass_delta": summary["turn_pass"] - old_summary.get("turn_pass", 0),
        "scenario_pass_delta": summary["scenario_pass"] - old_summary.get("scenario_pass", 0),
        **transitions,
    }


def main() -> None:
    # 预热：dialog-lora 冷加载可达 1-2 分钟，先发一条真实倾诉消息避免首轮超时
    print("预热中（dialog-lora 冷加载可能较慢）...")
    try:
        data, elapsed = _http_chat("我今天有点累", [])
        print(f"预热完成：{elapsed:.1f}s，回复 {len(data.get('reply', ''))} 字")
    except Exception as e:
        print(f"预热失败，中止评测：{e}")
        sys.exit(1)
    time.sleep(TURN_GAP_SEC)

    results = []
    for scenario in SCENARIOS:
        results.append(run_scenario(scenario))

    all_turns = [t for s in results for t in s["turns"]]
    turn_pass = sum(1 for t in all_turns if t["pass"])
    warn_count = sum(1 for t in all_turns if t["warns"])
    retry_count = sum(1 for t in all_turns if t["quality_retry"])
    issue_counts: dict[str, int] = {}
    for t in all_turns:
        for issue in t["issues"]:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1

    summary = {
        "turn_total": len(all_turns),
        "turn_pass": turn_pass,
        "turn_pass_rate": round(turn_pass / len(all_turns), 4) if all_turns else 0,
        "scenario_pass": sum(1 for s in results if s["pass"]),
        "warn_count": warn_count,
        "quality_retry_count": retry_count,
        "issue_counts": issue_counts,
    }
    # 批次对比：写入前读旧 latest 作基线（容器内无 .git，基线=磁盘上次跑次）
    comparison = compare_with_baseline(_load_baseline(), results, summary)

    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "api": API,
            "engine": "生产管线 HTTP /api/chat（规则 triage + RAG + 质检重试）",
            "scenarios": len(SCENARIOS),
            "turns_per_scenario": len(SCENARIOS[0]["turns"]),
            "comparison": comparison,
        },
        "summary": summary,
        "scenarios": results,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    # 时间戳归档（gitignore，保留历史跑次供回溯）
    archive_path = OUT_PATH.replace(
        "_latest.json", f"_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n===== 汇总 =====")
    print(f"轮次通过：{turn_pass}/{len(all_turns)}（{report['summary']['turn_pass_rate']:.0%}）")
    print(f"场景通过：{report['summary']['scenario_pass']}/{len(SCENARIOS)}")
    print(f"质检重试：{retry_count} 轮；warn：{warn_count} 轮")
    if issue_counts:
        print(f"问题分布：{issue_counts}")
    if comparison:
        print(f"\n===== 与上次对比（{comparison['baseline_at']}）=====")
        print(f"轮次 delta：{comparison['turn_pass_delta']:+d}；场景 delta：{comparison['scenario_pass_delta']:+d}")
        if comparison["fixed"]:
            print(f"修复轮：{comparison['fixed']}")
        if comparison["regressed"]:
            print(f"⚠ 退化轮：{comparison['regressed']}")
        print(f"持续通过 {comparison['both_pass']} 轮；持续失败 {comparison['both_fail']} 轮")
    print(f"产物：{OUT_PATH}")
    print(f"归档：{archive_path}")


if __name__ == "__main__":
    main()
