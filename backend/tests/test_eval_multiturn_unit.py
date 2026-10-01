"""eval_multiturn 工具函数单测（不依赖真实 LLM，零成本）。

覆盖：
- _load_baseline：合法基线文件读入、缺失/损坏返回 None
- compare_with_baseline：轮次状态迁移（fixed/regressed/both_pass/both_fail）
- quality_breakdown：逐条违规项定位（与生产 check_reply_quality 口径一致）
"""
import json
import os
import sys
import tempfile

import pytest

# eval_multiturn 路径导入
sys.path.insert(0, "/app/scripts/eval")
import eval_multiturn as em  # noqa: E402

from app.agents.nodes.intervention import _BANNED_CLOSE_Q, _REPEAT_MIN_LEN  # noqa: E402


class TestLoadBaseline:
    def test_returns_none_when_file_missing(self):
        # 临时 OUT_PATH 指向不存在的文件
        orig = em.OUT_PATH
        em.OUT_PATH = "/tmp/__no_such_multiturn_latest.json"
        try:
            assert em._load_baseline() is None
        finally:
            em.OUT_PATH = orig

    def test_returns_dict_when_valid(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({
                "meta": {"generated_at": "2026-10-01T00:00:00+00:00"},
                "summary": {"turn_total": 4, "turn_pass": 3},
                "scenarios": [
                    {"name": "S1", "turns": [{"turn": 1, "pass": True}]},
                ],
            }, f)
            path = f.name
        orig = em.OUT_PATH
        em.OUT_PATH = path
        try:
            base = em._load_baseline()
            assert isinstance(base, dict)
            assert base["summary"]["turn_pass"] == 3
        finally:
            em.OUT_PATH = orig
            os.remove(path)

    def test_returns_none_when_corrupt(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("not-json")
            path = f.name
        orig = em.OUT_PATH
        em.OUT_PATH = path
        try:
            assert em._load_baseline() is None
        finally:
            em.OUT_PATH = orig
            os.remove(path)


class TestCompareWithBaseline:
    @staticmethod
    def _make_old(pass_map: list[list[bool]]) -> dict:
        """pass_map: [场景[轮次是否通过]]"""
        scenarios = []
        for si, turns in enumerate(pass_map):
            scenarios.append({
                "name": f"S{si+1}",
                "turns": [{"turn": ti + 1, "pass": p} for ti, p in enumerate(turns)],
            })
        return {
            "meta": {"generated_at": "2026-09-30T00:00:00+00:00"},
            "summary": {"turn_total": sum(len(t) for t in pass_map), "turn_pass": sum(sum(t) for t in pass_map), "scenario_pass": 0},
            "scenarios": scenarios,
        }

    @staticmethod
    def _make_new(pass_map: list[list[bool]]) -> list[dict]:
        return [
            {
                "name": f"S{si+1}",
                "turns": [{"turn": ti + 1, "pass": p} for ti, p in enumerate(turns)],
            }
            for si, turns in enumerate(pass_map)
        ]

    def test_fixed_and_regressed(self):
        old = self._make_old([[True, False], [True, True]])
        new = self._make_new([[False, True], [True, False]])  # S1T1 reg, S1T2 fix, S2T2 reg
        summary = {"turn_pass": 2, "scenario_pass": 0}
        comp = em.compare_with_baseline(old, new, summary)
        assert comp is not None
        assert comp["regressed"] == ["S1T1", "S2T2"]
        assert comp["fixed"] == ["S1T2"]
        assert comp["both_pass"] == 1  # S2T1
        assert comp["both_fail"] == 0
        assert comp["turn_pass_delta"] == -1

    def test_first_run_no_old(self):
        assert em.compare_with_baseline(None, [], {"turn_pass": 0, "scenario_pass": 0}) is None

    def test_extra_scenario_in_new_ignored(self):
        old = self._make_old([[True]])
        new = self._make_new([[False], [True]])
        comp = em.compare_with_baseline(old, new, {"turn_pass": 1, "scenario_pass": 0})
        assert comp["regressed"] == ["S1T1"]
        assert comp["fixed"] == []


class TestQualityBreakdown:
    def test_empty_reply(self):
        assert em.quality_breakdown("", [], min_methods=1) == ["空回复"]

    def test_close_question(self):
        reply = "你这样做对吧？"
        assert "闭合问句" in em.quality_breakdown(reply, [], min_methods=1)

    def test_too_long(self):
        reply = "这是很长的一句" * 20  # >120 字
        assert "长度>120" in em.quality_breakdown(reply, [], min_methods=1)

    def test_verbatim_repeat(self):
        # 历史：15+ 字片段
        hist = [{"role": "assistant", "content": "最近学习压力确实很大，每天都很紧张焦虑。"}]
        # 回复与历史有 ≥12 字逐字重叠（共享 16 字片段）
        reply = "最近学习压力确实很大，每天都很紧张焦虑，可以试试先做几次深呼吸放松。"
        issues = em.quality_breakdown(reply, hist, min_methods=1)
        assert "≥12字逐字复读" in issues

    def test_method_categories_below(self):
        reply = "嗯，这个问题不大。"  # 没有任何做法关键词
        assert "做法类别<2" in em.quality_breakdown(reply, [], min_methods=2)

    def test_breath_formula_not_repeat(self):
        """4吸6呼公式豁免：跨轮重复不算复读，但呼应对生产代码的 _strip_breath_formula 调用。
        
        eval_multiturn 的 quality_breakdown 目前用的是干预模块自己的逐字复读检测，
        它已经内置了公式剥离（intervention.py check_reply_quality 的同级逻辑）。
        本测试确认：场景轮次中如果只有公式重叠，不会误报复读。
        """
        hist = [{"role": "assistant", "content": "紧张时试试腹式呼吸，吸气四秒、呼气六秒，做几轮。"}]
        reply = "考试前也可以先做腹式呼吸，吸气四秒、呼气六秒，让身体先稳住。"
        issues = em.quality_breakdown(reply, hist, min_methods=1)
        # 只剩呼吸公式不算复读，无其他违规即为空
        assert "≥12字逐字复读" not in issues

    def test_repeat_outside_formula_still_caught(self):
        """公式外的真实复读仍被检测到。"""
        hist = [{"role": "assistant", "content": "试试把担心的事写在纸上，写完就放一边。"}]
        reply = "试试把担心的事写在纸上，写完就放一边，然后做几轮腹式呼吸。"
        assert "≥12字逐字复读" in em.quality_breakdown(reply, hist, min_methods=1)
