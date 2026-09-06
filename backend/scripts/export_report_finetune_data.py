"""导出 report 角色微调数据：量表数据(instruction) + 发展建议(output)。

从 SQLite 读取所有测评记录，调用项目现有报告生成逻辑（_compute_subdims + _build_narrative）
生成"发展建议"，配对成 LLaMA-Factory 可直接使用的 JSONL 训练样本。

数据流向：
  assessment_records（量表数据） ──┐
                                    ├──> instruction（量表数据+子维度）
  _compute_subdims（子维度）────────┘        │
                                             ├──> 配对 ──> JSONL
  _build_narrative（LLM 生成发展建议）──────> output

用法：
  # 完整导出（调用 LLM 生成发展建议，需配置百炼/Ollama）
  python scripts/export_report_finetune_data.py --output data/finetune_report.jsonl

  # 只导前 50 条（快速验证）
  python scripts/export_report_finetune_data.py --limit 50

  # 跳过 LLM（仅导出量表数据，output 留空，供后续批量生成）
  python scripts/export_report_finetune_data.py --skip-llm
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# 确保 backend 目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import AssessmentRecord, Session as SessionModel
from app.reports.service import _build_narrative, _compute_subdims, _scale_max_score
from app.scales.registry import get_scale

# 严重度中文标签（与 service.py 一致）
SEVERITY_LABEL = {
    "none": "无明显症状",
    "mild": "轻度",
    "moderate": "中度",
    "moderately_severe": "中重度",
    "severe": "重度",
}


def _assessment_to_dict(a: AssessmentRecord) -> dict:
    """把 ORM 对象转成 service 层函数期望的 dict 格式。"""
    return {
        "scale_id": a.scale_id,
        "scale_name": a.scale_name,
        "total_score": a.total_score,
        "severity": a.severity,
        "crisis_level": a.crisis_level,
        "needs_crisis_escalation": a.crisis_level == "elevated",
        "answers": a.answers or {},
    }


def _build_instruction(assessments: list[dict], all_dims: list[dict]) -> str:
    """构造 instruction：量表总分 + 子维度剖析（与 _build_narrative 的 user_msg 格式对齐）。"""
    parts = []
    for a in assessments:
        max_score = _scale_max_score(a["scale_id"]) or "?"
        parts.append(
            f"- {a['scale_name']}：总分 {a['total_score']}/{max_score}，"
            f"严重度「{SEVERITY_LABEL.get(a['severity'], a['severity'])}」"
            + ("，触发危机升级（自杀意念/自伤）" if a.get("needs_crisis_escalation") else "")
        )
    dims_txt = "\n".join(
        f"  · {d['scale_name']}/{d['name']}：{d['raw_score']}/{d['max_score']}，"
        f"等级「{d['severity_label']}」"
        for d in all_dims
    )
    return f"本次评估结果：\n" + "\n".join(parts) + f"\n子维度剖析：\n{dims_txt}\n\n请撰写发展建议。"


async def _generate_narrative(assessments: list[dict], all_dims: list[dict]) -> str:
    """调用项目现有 LLM 逻辑生成发展建议。"""
    return await _build_narrative(assessments, all_dims)


def _desensitize(text: str) -> str:
    """脱敏：发展建议本身不含个人信息，这里做保守清理（去掉可能的姓名/学号模式）。"""
    import re
    # 去掉可能的真实姓名（2-4 字中文，紧跟"同学"等称呼的）— 发展建议通常不含，保险起见
    text = re.sub(r"[张王李赵刘陈杨黄周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤]\w{1,3}(同学|同学你)", "[同学]", text)
    # 去掉学号（纯数字串 ≥6 位）
    text = re.sub(r"\b\d{6,}\b", "[学号]", text)
    return text


def main():
    parser = argparse.ArgumentParser(description="导出 report 角色微调数据")
    parser.add_argument(
        "--output",
        default="data/finetune/finetune_report.jsonl",
        help="输出 JSONL 文件路径（默认 data/finetune/finetune_report.jsonl）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多导出条数（0=全部）",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="跳过 LLM 生成发展建议（output 留空，仅导出量表数据）",
    )
    parser.add_argument(
        "--scale",
        default="",
        help="只导出指定量表（如 phq_a），空=全部",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        # 查所有有 assessment 的 session
        stmt = (
            select(SessionModel)
            .options(selectinload(SessionModel.assessments))
            .where(SessionModel.assessments.any())
            .order_by(SessionModel.created_at)
        )
        sessions = db.execute(stmt).scalars().all()

        total_sessions = len(sessions)
        exported = 0
        skipped = 0

        with open(output_path, "w", encoding="utf-8") as f:
            for sess in sessions:
                if args.limit and exported >= args.limit:
                    break

                assessments = [_assessment_to_dict(a) for a in sess.assessments]

                # 量表过滤
                if args.scale:
                    assessments = [a for a in assessments if a["scale_id"] == args.scale]
                if not assessments:
                    skipped += 1
                    continue

                # 计算子维度
                all_dims = []
                for a in assessments:
                    all_dims.extend(_compute_subdims(a))

                # 构造 instruction
                instruction = _build_instruction(assessments, all_dims)

                # 生成 output（发展建议）
                if args.skip_llm:
                    output = ""
                else:
                    try:
                        output = asyncio.run(_generate_narrative(assessments, all_dims))
                    except Exception as e:
                        print(f"  [WARN] session {sess.id} 生成发展建议失败: {e}，跳过")
                        skipped += 1
                        continue

                # 脱敏
                output = _desensitize(output)

                # 写入 JSONL
                sample = {"instruction": instruction, "output": output}
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                exported += 1

                if exported % 10 == 0:
                    print(f"  已导出 {exported}/{total_sessions}...")

        print(f"\n{'='*50}")
        print(f"导出完成")
        print(f"  总会话数: {total_sessions}")
        print(f"  成功导出: {exported}")
        print(f"  跳过: {skipped}")
        print(f"  输出文件: {output_path.resolve()}")
        if exported > 0:
            size_kb = output_path.stat().st_size / 1024
            print(f"  文件大小: {size_kb:.1f} KB")

    finally:
        db.close()


if __name__ == "__main__":
    main()
