# -*- coding: utf-8 -*-
"""遗留项验证脚本：has_assessment 链路 + triage 意图标签抽样。

在 psycheflow-backend 容器内运行：
  docker exec psycheflow-backend uv run python scripts/verify_leftovers.py

验证项：
  1. has_assessment=true 链路：创建 Session+AssessmentRecord → graph.ainvoke
     → 检查 Assessment 节点读到记录、Intervention 节点 LLM prompt 含上下文
  2. triage 意图标签抽样：多组真实学生消息 → provider.chat(role="intake")
     → 检查返回的 4 类标签分布
"""
import asyncio
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import text

from app.agents.graph import graph
from app.agents.prompts import TRIAGE_SYSTEM, TRIAGE_USER_TEMPLATE
from app.core.llm import provider
from app.db import SessionLocal, init_db
from app.models import AssessmentRecord, Session

init_db()

# ──────────────────────────────────────────────────────────────
# 验证 1：has_assessment=true 端到端链路
# ──────────────────────────────────────────────────────────────
async def verify_has_assessment():
    print("=" * 60)
    print("[验证1] has_assessment=true 端到端链路")
    print("=" * 60)

    # 1. 创建临时 Session + AssessmentRecord
    db = SessionLocal()
    try:
        sid = uuid.uuid4().hex
        db.add(Session(id=sid, label="verify-has-assessment"))
        db.commit()

        rec = AssessmentRecord(
            session_id=sid,
            scale_id="phq_a",
            scale_name="PHQ-A 青少年抑郁筛查",
            total_score=15,
            severity="moderate",
            crisis_level="none",
            crisis_triggers=[],
            interpretation="中度抑郁症状，建议寻求专业心理支持",
            answers={"1": 2, "2": 3, "3": 2, "4": 3, "5": 2, "6": 2, "7": 1, "8": 1, "9": 1, "10": 0, "11": 0},
        )
        db.add(rec)
        db.commit()
        print(f"[1] 创建 Session {sid[:8]} + AssessmentRecord(score=15, severity=moderate)")

        # 2. 捕获 Intervention 节点 LLM 收到的 messages
        original_chat = provider.chat
        captured = []

        async def capturing_chat(role, messages, **kwargs):
            if role == "dialog":
                captured.append(messages)
            return await original_chat(role, messages, **kwargs)

        provider.chat = capturing_chat

        # 3. graph.ainvoke 真实 LLM
        initial_state = {
            "session_id": sid,
            "account_id": "",
            "user_message": "我最近总是睡不好，没什么胃口",
            "history": [],
            "agent_trace": [],
        }
        print("[2] 调用 graph.ainvoke（真实 LLM）...")
        final_state = await graph.ainvoke(initial_state)
        provider.chat = original_chat  # 恢复

        # 4. 检查 Assessment 节点产出
        print("\n--- Assessment 节点产出 ---")
        has = final_state.get("has_assessment")
        ctx = final_state.get("assessment_context", {})
        print(f"  has_assessment = {has}")
        print(f"  assessment_context = {ctx}")
        assert has is True, "FAIL: has_assessment 应为 True"
        assert ctx.get("scale_id") == "phq_a", "FAIL: scale_id 应为 phq_a"
        assert ctx.get("severity") == "moderate", "FAIL: severity 应为 moderate"
        assert ctx.get("total_score") == 15, "FAIL: total_score 应为 15"
        print("  [PASS] has_assessment=true + assessment_context 字段完整")

        # 5. 检查 Intervention 节点 LLM prompt 是否含 assessment_context
        print("\n--- Intervention 节点 LLM prompt 检查 ---")
        assert captured, "FAIL: 未捕获到 Intervention LLM 调用"
        user_msg = captured[-1][-1]["content"]  # 最后一条 user message
        has_ctx_in_prompt = "moderate" in user_msg and "phq_a" in user_msg
        print(f"  prompt 含 assessment_context 关键字: {has_ctx_in_prompt}")
        # 打印 prompt 片段供人工审视
        idx = user_msg.find("has_assessment:")
        if idx >= 0:
            snippet = user_msg[idx:idx + 200]
            print(f"  prompt 片段:\n    {snippet}")
        assert has_ctx_in_prompt, "FAIL: Intervention prompt 未包含 assessment_context"
        print("  [PASS] Intervention LLM prompt 注入了 assessment_context")

        # 6. 检查 agent_trace
        trace = final_state.get("agent_trace", [])
        print(f"\n  agent_trace = {trace}")
        assert trace == ["triage", "assessment", "intervention"], f"FAIL: agent_trace 不完整: {trace}"
        print("  [PASS] agent_trace 完整 triage→assessment→intervention")

        # 7. 检查回复内容（LLM 是否引用了量表结果）
        reply = final_state.get("final_reply", "")
        print(f"\n  AI 回复（前200字）: {reply[:200]}")
        print("\n[验证1] 全部 PASS ✓")
        return True

    finally:
        # 清理临时数据
        try:
            db.execute(text(f"DELETE FROM assessment_records WHERE session_id = :sid"), {"sid": sid})
            db.execute(text(f"DELETE FROM sessions WHERE id = :sid"), {"sid": sid})
            db.commit()
        except Exception:
            db.rollback()
        db.close()


# ──────────────────────────────────────────────────────────────
# 验证 2：triage 意图标签 LLM 抽样
# ──────────────────────────────────────────────────────────────
TRIAGE_SAMPLES = [
    # (期望意图, 学生消息)
    ("求助", "我想做一下心理测评"),
    ("求助", "有没有什么量表可以测测我的状态"),
    ("求助", "我觉得我需要帮助，该怎么办"),
    ("倾诉", "我最近压力好大，喘不过气"),
    ("倾诉", "今天考试又没考好，心情很差"),
    ("倾诉", "和同学吵架了，心里很难受"),
    ("咨询", "什么是抑郁症"),
    ("咨询", "CBT 认知行为疗法是什么"),
    ("咨询", "焦虑和抑郁有什么区别"),
]

async def verify_triage_sampling():
    print("\n" + "=" * 60)
    print("[验证2] triage 意图标签 LLM 抽样")
    print("=" * 60)
    print(f"样本数: {len(TRIAGE_SAMPLES)}，每组 3 条\n")

    correct = 0
    total = len(TRIAGE_SAMPLES)
    results = []

    for expected, msg in TRIAGE_SAMPLES:
        try:
            user_prompt = TRIAGE_USER_TEMPLATE.format(message=msg)
            reply = await provider.chat(
                role="intake",
                messages=[
                    {"role": "system", "content": TRIAGE_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=20,
            )
            actual = reply.strip()
            # 兜底判断：非 4 类记为 fallback
            if actual not in ("求助", "倾诉", "咨询", "危机"):
                actual = f"(fallback→倾诉)"
            ok = actual == expected
            if ok:
                correct += 1
            results.append((expected, msg, actual, ok))
            mark = "✓" if ok else "✗"
            print(f"  {mark} 期望[{expected}] 实际[{actual}] | {msg[:30]}")
        except Exception as e:
            results.append((expected, msg, f"ERROR:{e}", False))
            print(f"  ✗ 期望[{expected}] 实际[ERROR] | {msg[:30]} | {e}")

    print(f"\n准确率: {correct}/{total} ({correct/total*100:.0f}%)")
    # 允许 1 个偏差（LLM 分类有主观性，求助 vs 倾诉 边界模糊）
    if correct >= total - 1:
        print("[验证2] PASS ✓（允许 1 个边界偏差）")
        return True
    else:
        print(f"[验证2] FAIL：偏差超过 1 个")
        return False


async def main():
    ok1 = await verify_has_assessment()
    ok2 = await verify_triage_sampling()
    print("\n" + "=" * 60)
    print(f"总结: has_assessment={'PASS' if ok1 else 'FAIL'} | triage={'PASS' if ok2 else 'FAIL'}")
    print("=" * 60)
    sys.exit(0 if (ok1 and ok2) else 1)


asyncio.run(main())
