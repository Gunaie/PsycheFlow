"""D1 量表库扩展测试：SDQ 长处和困难问卷 + MHT 心理健康诊断测验。"""
import pytest

from app.scales.mht import MHT_ITEMS, mht, VALIDITY_IDS
from app.scales.registry import _REGISTRY
from app.scales.sdq import sdq


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

def test_registry_contains_new_scales():
    assert {"phq_a", "scared", "sdq", "mht"} <= set(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# SDQ 计分
# ---------------------------------------------------------------------------

def _sdq_answers(overrides: dict | None = None):
    base = {i: 0 for i in range(1, 26)}
    if overrides:
        base.update(overrides)
    return base


class TestSDQ:
    def test_all_not_true(self):
        """全选 0（不符合）：反向题 7/11/14/21/25 各得 2 分。"""
        r = sdq.score(_sdq_answers())
        # 情绪 0；品行 = 0+2+0+0+0 = 2；多动 = 0+0+0+2+2 = 4；同伴 = 0+2+2+0+0 = 4
        assert r.factor_scores["情绪症状"] == 0
        assert r.factor_scores["品行问题"] == 2
        assert r.factor_scores["多动/注意缺陷"] == 4
        assert r.factor_scores["同伴交往问题"] == 4
        assert r.factor_scores["亲社会行为"] == 0  # 亲社会无反向，全 0 即 0 分
        assert r.total_score == 10  # 总困难 = 0+2+4+4
        assert r.severity.value == "none"
        assert not r.needs_crisis_escalation
        assert "内化分(情绪+同伴)" in r.factor_scores
        assert "外化分(品行+多动)" in r.factor_scores

    def test_max_difficulties(self):
        """困难条目全 2、反向条目全 0 → 总困难 40，亲社会 0 触发低分提示。"""
        answers = _sdq_answers({i: 2 for i in range(1, 26)})
        for q in (7, 11, 14, 21, 25):  # 反向题答 0 才能拿满困难分
            answers[q] = 0
        for q in (1, 4, 9, 17, 20):  # 亲社会答 0 → 低分提示
            answers[q] = 0
        r = sdq.score(answers)
        assert r.total_score == 40
        assert r.severity.value == "moderate"
        assert "亲社会行为得分偏低" in r.interpretation

    def test_borderline_16_mild(self):
        """总困难 16 → 边缘（MILD）。情绪 3 项=2（6 分）+ 基线反向贡献 10。"""
        r = sdq.score(_sdq_answers({3: 2, 8: 2, 13: 2}))
        assert r.total_score == 16  # 6 + 2 + 4 + 4
        assert r.severity.value == "mild"

    def test_factor_high_note(self):
        """情绪症状 7 分（超正常界值 5）→ 解读含因子偏高提示。"""
        r = sdq.score(_sdq_answers({3: 2, 8: 2, 13: 2, 16: 1}))  # 情绪 = 7
        assert r.factor_scores["情绪症状"] == 7
        assert "情绪症状得分偏高" in r.interpretation
        # 总困难 = 7+2+4+4 = 17 → mild
        assert r.severity.value == "mild"

    def test_missing_item_raises(self):
        with pytest.raises(ValueError, match="缺失答题"):
            sdq.score({i: 0 for i in range(1, 25)})

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match="非法分值"):
            sdq.score(_sdq_answers({1: 3}))

    def test_invalid_qid_raises(self):
        with pytest.raises(ValueError, match="非法题号"):
            sdq.score(_sdq_answers({26: 0}))


# ---------------------------------------------------------------------------
# MHT 计分
# ---------------------------------------------------------------------------

def _mht_answers(overrides: dict | None = None):
    base = {i: 0 for i in range(1, 101)}
    if overrides:
        base.update(overrides)
    return base


class TestMHT:
    def test_structure(self):
        assert len(MHT_ITEMS) == 100
        assert len(VALIDITY_IDS) == 10

    def test_all_no(self):
        r = mht.score(_mht_answers())
        assert r.total_score == 0
        assert r.factor_scores["效度量表"] == 0
        assert r.severity.value == "none"
        assert not r.needs_crisis_escalation
        assert "存疑" not in r.interpretation

    def test_crisis_item_85(self):
        r = mht.score(_mht_answers({85: 1}))
        assert r.needs_crisis_escalation
        assert any("85" in t for t in r.crisis_triggers)

    def test_crisis_item_97(self):
        r = mht.score(_mht_answers({97: 1}))
        assert r.needs_crisis_escalation
        assert any("97" in t for t in r.crisis_triggers)

    def test_validity_items_not_in_total(self):
        """效度题全答“是”只影响效度分，不计入总分/因子分。"""
        r = mht.score(_mht_answers({q: 1 for q in VALIDITY_IDS}))
        assert r.total_score == 0
        assert r.factor_scores["效度量表"] == 10
        assert "存疑" in r.interpretation

    def test_total_65_severe(self):
        """全部内容题答“是”→ 总分 90 ≥65 → SEVERE。"""
        answers = _mht_answers({i: 1 for i in range(1, 101)})
        for q in VALIDITY_IDS:
            answers[q] = 0
        r = mht.score(answers)
        assert r.total_score == 90
        assert r.severity.value == "severe"
        assert "65" in r.interpretation

    def test_factor_high_moderate(self):
        """学习焦虑 15 题=1 → 因子 15 ≥8 → MODERATE（总分 15 <65）。"""
        r = mht.score(_mht_answers({i: 1 for i in range(1, 16)}))
        assert r.factor_scores["学习焦虑"] == 15
        assert r.total_score == 15
        assert r.severity.value == "moderate"
        assert "学习焦虑（15）" in r.interpretation

    def test_factor_mid_mild(self):
        """对人焦虑 4 题=1（因子 4-7 中等）→ MILD。"""
        r = mht.score(_mht_answers({16: 1, 17: 1, 18: 1, 19: 1}))
        assert r.factor_scores["对人焦虑"] == 4
        assert r.severity.value == "mild"

    def test_missing_item_raises(self):
        with pytest.raises(ValueError, match="缺失答题"):
            mht.score({i: 0 for i in range(1, 100)})

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match="非法分值"):
            mht.score(_mht_answers({1: 2}))

    def test_meta_for_frontend(self):
        """meta() 自描述：100 题、二选项，可供前端渲染。"""
        meta = mht.meta()
        assert len(meta["items"]) == 100
        assert meta["options"] == {"1": "是", "0": "不是"} or meta["options"] == {1: "是", 0: "不是"}
