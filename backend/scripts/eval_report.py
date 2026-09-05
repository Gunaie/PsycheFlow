# -*- coding: utf-8 -*-
"""报告生成结构合规评测脚本（P2 LLM 输出评估体系）。

对 5 个合成测评场景（PHQ-A / SCARED / SDQ / MHT / PHQ-A+SCARED 合并）走真实报告链路：
  计分引擎 → 子维度 → 真实 LLM 发展建议 → Jinja2 渲染 → WeasyPrint 出 PDF
对每份报告做结构断言（六章节/个人信息/测评用时/雷达图/发展建议非空/PDF 完整性），
输出通过率报告。

用法（容器内，需 .env 有真实 DASHSCOPE_API_KEY）：
  docker exec psycheflow-backend uv run python scripts/eval_report.py
  docker exec psycheflow-backend uv run python scripts/eval_report.py --only phq_a  # 单场景调试

说明：
  - 评测数据全部为合成数据（评估同学/E0001），入库用独立 label 前缀，跑完自动清理
  - 刻意绕过 generate_report_pdf 的审计分支（评测流量不写 AuditLog），渲染/LLM 链路与生产一致
  - 结果写入 scripts/eval/results/report_eval_<时间戳>.json 并同步覆盖 report_eval_latest.json
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

from app.core.config import settings  # noqa: E402
from app.models import AssessmentRecord, Session as SessionModel, User  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.reports.service import (  # noqa: E402
    _build_narrative,
    _compute_subdims,
    render_report_html,
)
from app.scales import get_scale  # noqa: E402

RESULTS_DIR = "/app/scripts/eval/results"

# 六章节标题（与 report.html 模板一致，MHT 参考结构）
CHAPTER_MARKERS = [
    "1. 测评工具介绍",
    "2. 测评结果解读注意事项",
    "3. 测评人员信息",
    "4. 测评结果",
    "5. 测评结果剖析",
    "6. 发展建议",
]

PROFILE = {
    "name": "评估同学",
    "gender": "女",
    "age": 14,
    "student_no": "E0001",
    "grade": "初三",
    "school": "评估中学",
}

# 场景定义：phq_a 特意把第 9 题置 0（非危机路径），其余量表全 1
SCENARIOS = [
    {"key": "phq_a", "scales": ["phq_a"]},
    {"key": "scared", "scales": ["scared"]},
    {"key": "sdq", "scales": ["sdq"]},
    {"key": "mht", "scales": ["mht"]},
    {"key": "combined", "scales": ["phq_a", "scared"]},
]

DURATION_SEC = 185  # 会话建到交卷的间隔（报告应显示 3分5秒）


def _answers_for(scale_id: str) -> dict[int, int]:
    scale = get_scale(scale_id)
    answers = {it["id"]: 1 for it in scale.items}
    if scale_id == "phq_a":
        answers[9] = 0  # 第 9 题自杀意念置 0 → 非危机路径
    return answers


def _first_content_snippet(narrative_md: str, length: int = 12) -> str:
    """从 markdown 叙事中取第一段正文内容（跳过标题行与列表序号），供 HTML 包含性断言。

    markdown 渲染会把 #/**/列表序号转成标签，直接比对原文必然失配。
    """
    for line in narrative_md.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cleaned = re.sub(r"^(\d+[.、]|[-*])\s*", "", line)  # 去列表序号
        cleaned = cleaned.replace("*", "").replace("`", "")
        if len(cleaned) >= length:
            return cleaned[:length]
    return ""


def _build_scenario(scales: list[str]) -> list[dict]:
    """按生产 submit 链路构造报告入参 dict（计分引擎真实计分）。"""
    out = []
    for sid in scales:
        scale = get_scale(sid)
        answers_int = _answers_for(sid)
        result = scale.score(answers_int)
        out.append(
            {
                "scale_id": result.scale_id,
                "scale_name": result.scale_name,
                "total_score": result.total_score,
                "severity": result.severity.value if hasattr(result.severity, "value") else str(result.severity),
                "crisis_level": (
                    result.crisis_level.value if hasattr(result.crisis_level, "value") else str(result.crisis_level)
                ),
                "crisis_triggers": result.crisis_triggers,
                "interpretation": result.interpretation,
                "needs_crisis_escalation": result.crisis_level.value == "elevated",
                "answers": {str(k): v for k, v in answers_int.items()},  # 生产 JSON 形状：str 键
            }
        )
    return out


async def run(only: str | None, verbose: bool) -> dict:
    db = SessionLocal()
    created_session_ids: list[str] = []
    user: User | None = None
    report_results: list[dict] = []
    t0 = time.time()

    try:
        user = User(
            label=f"eval_report_{time.strftime('%Y%m%d%H%M%S')}",
            role="student",
            token=os.urandom(32).hex(),
            profile=PROFILE,
        )
        db.add(user)
        db.commit()

        scenarios = [s for s in SCENARIOS if only is None or s["key"] == only]

        for scen in scenarios:
            assessments = _build_scenario(scen["scales"])

            session = SessionModel(
                label=f"eval_{scen['key']}",
                account_id=user.id,
                created_at=datetime.utcnow() - timedelta(seconds=DURATION_SEC),
            )
            db.add(session)
            db.flush()
            for a in assessments:
                db.add(
                    AssessmentRecord(
                        session_id=session.id,
                        scale_id=a["scale_id"],
                        scale_name=a["scale_name"],
                        total_score=a["total_score"],
                        severity=a["severity"],
                        crisis_level=a["crisis_level"],
                        crisis_triggers=a["crisis_triggers"],
                        interpretation=a["interpretation"],
                        answers=a["answers"],
                        created_at=datetime.utcnow(),
                    )
                )
            db.commit()
            created_session_ids.append(session.id)
            # render_report_html 依赖 session.assessments（测评用时）与 session.account，重载保证关系生效
            session = db.get(SessionModel, session.id)

            all_dims = []
            for a in assessments:
                all_dims.extend(_compute_subdims(a))
            narrative_md = await _build_narrative(assessments, all_dims)  # 真实 LLM
            html = render_report_html(session, assessments, narrative_md)
            from weasyprint import HTML

            pdf_bytes = HTML(string=html).write_pdf()

            checks = {
                "章节_1_测评工具介绍": CHAPTER_MARKERS[0] in html,
                "章节_2_解读注意事项": CHAPTER_MARKERS[1] in html,
                "章节_3_测评人员信息": CHAPTER_MARKERS[2] in html,
                "章节_4_测评结果": CHAPTER_MARKERS[3] in html,
                "章节_5_测评结果剖析": CHAPTER_MARKERS[4] in html,
                "章节_6_发展建议": CHAPTER_MARKERS[5] in html,
                "姓名来自profile": PROFILE["name"] in html,
                "学号来自profile": PROFILE["student_no"] in html,
                "测评用时非空": "3分" in html,
                "发展建议非空": len(narrative_md) >= 100 and "建议" in narrative_md,
                "建议渲染进报告": _first_content_snippet(narrative_md) in html,
                "雷达图SVG": "<svg" in html and html.count("<polygon") >= 2,
                "PDF完整": pdf_bytes[:4] == b"%PDF" and len(pdf_bytes) >= 30_000,
            }
            # 危机红框双向断言：预期危机（如 MHT 85/97 命中）必须有框+热线；预期安全必须无框
            expected_crisis = any(a["needs_crisis_escalation"] for a in assessments)
            crisis_box = '<div class="crisis">' in html
            checks["危机红框符合预期"] = (crisis_box == expected_crisis) and (
                "12355" in html if expected_crisis else True
            )
            # 质量信号：安全场景的 LLM 建议文本不应携带危机话术（红框文案仅允许出现在危机框内）
            checks["安全场景建议无危机话术"] = expected_crisis or ("需要立即寻求帮助" not in narrative_md)
            if scen["key"] == "combined":
                checks["双量表均在报告中"] = all(
                    get_scale(s).scale_name in html for s in scen["scales"]
                )

            passed = sum(checks.values())
            total = len(checks)
            item = {
                "scenario": scen["key"],
                "passed": passed,
                "total": total,
                "narrative_len": len(narrative_md),
                "pdf_bytes": len(pdf_bytes),
                "checks": checks,
                "failed": [k for k, v in checks.items() if not v],
            }
            report_results.append(item)
            mark = "OK  " if passed == total else "MISS"
            print(f"[{mark}] {scen['key']}: {passed}/{total} | 建议 {item['narrative_len']} 字 | PDF {item['pdf_bytes']} B")
            if verbose and item["failed"]:
                print(f"       失败项: {item['failed']}")
    finally:
        # 清理合成数据（session 级联删 assessments）
        try:
            for sid in created_session_ids:
                row = db.get(SessionModel, sid)
                if row:
                    db.delete(row)
            if user is not None:
                row = db.get(User, user.id)
                if row:
                    db.delete(row)
            db.commit()
        except Exception:
            db.rollback()
        db.close()

    total_checks = sum(r["total"] for r in report_results)
    passed_checks = sum(r["passed"] for r in report_results)
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model_report": settings.model_report,
        "scenarios": report_results,
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "pass_rate": round(passed_checks / total_checks, 4) if total_checks else 0,
        "elapsed_sec": round(time.time() - t0, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="报告生成结构合规评测")
    parser.add_argument("--only", default=None, help="只跑指定场景（phq_a/scared/sdq/mht/combined）")
    parser.add_argument("--verbose", action="store_true", help="打印失败项明细")
    args = parser.parse_args()

    if not settings.dashscope_api_key:
        print("[中止] 未配置 DASHSCOPE_API_KEY：本评测调用真实 LLM，请在有凭据的环境（容器）运行", file=sys.stderr)
        return 2

    summary = asyncio.run(run(args.only, args.verbose))

    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    latest_path = os.path.join(RESULTS_DIR, "report_eval_latest.json")
    with open(os.path.join(RESULTS_DIR, f"report_eval_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n===== 报告结构合规评测结果（report 模型 {summary['model_report']}）=====")
    print(f"总体: {summary['passed_checks']}/{summary['total_checks']} = {summary['pass_rate']:.1%}（耗时 {summary['elapsed_sec']}s）")
    for r in summary["scenarios"]:
        print(f"  {r['scenario']}: {r['passed']}/{r['total']}" + (f" 失败:{r['failed']}" if r["failed"] else ""))
    print(f"结果已写入: {latest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
