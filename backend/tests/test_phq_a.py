import unittest

from app.scales.phq_a import phq_a, PHQ_A_ITEMS, SUICIDE_ITEM_ID
from app.scales.base import Severity, CrisisLevel


class TestPHQA(unittest.TestCase):
    def _ans(self, value):
        return {it["id"]: value for it in PHQ_A_ITEMS}

    def test_all_zero_no_depression(self):
        r = phq_a.score(self._ans(0))
        self.assertEqual(r.total_score, 0)
        self.assertEqual(r.severity, Severity.NONE)
        self.assertEqual(r.crisis_level, CrisisLevel.SAFE)

    def test_all_three_severe(self):
        r = phq_a.score(self._ans(3))
        self.assertEqual(r.total_score, 27)
        self.assertEqual(r.severity, Severity.SEVERE)

    def test_boundary_4_vs_5(self):
        ans = self._ans(0)
        ans[1] = ans[2] = ans[3] = ans[4] = 1  # 总分 4
        self.assertEqual(phq_a.score(ans).severity, Severity.NONE)
        ans[5] = 1  # 总分 5
        self.assertEqual(phq_a.score(ans).severity, Severity.MILD)

    def test_moderate_boundary_14(self):
        ans = self._ans(0)
        ans[1] = ans[2] = ans[3] = ans[4] = 3  # 12
        ans[5] = 2  # 14
        r = phq_a.score(ans)
        self.assertEqual(r.total_score, 14)
        self.assertEqual(r.severity, Severity.MODERATE)

    def test_moderately_severe_boundary_15(self):
        ans = self._ans(0)
        ans[1] = ans[2] = ans[3] = ans[4] = ans[5] = 3  # 15
        self.assertEqual(phq_a.score(ans).severity, Severity.MODERATELY_SEVERE)

    def test_suicide_item_triggers_crisis_regardless_of_total(self):
        # 即使总分很低，第 9 题 >0 即触发危机升级
        ans = self._ans(0)
        ans[SUICIDE_ITEM_ID] = 1
        r = phq_a.score(ans)
        self.assertEqual(r.total_score, 1)
        self.assertEqual(r.severity, Severity.NONE)  # 总分低 → 无抑郁等级
        self.assertEqual(r.crisis_level, CrisisLevel.ELEVATED)  # 但触发危机
        self.assertTrue(r.needs_crisis_escalation)
        self.assertTrue(r.crisis_triggers)

    def test_suicide_item_value_3_triggers(self):
        ans = self._ans(0)
        ans[SUICIDE_ITEM_ID] = 3
        self.assertEqual(phq_a.score(ans).crisis_level, CrisisLevel.ELEVATED)

    def test_no_crisis_when_suicide_zero_even_if_severe(self):
        ans = self._ans(3)  # 满分会因第 9 题=3 触发危机
        ans[SUICIDE_ITEM_ID] = 0  # 改回 0
        r = phq_a.score(ans)
        self.assertEqual(r.total_score, 24)  # 27 - 3
        self.assertEqual(r.severity, Severity.SEVERE)
        self.assertEqual(r.crisis_level, CrisisLevel.SAFE)

    def test_missing_answer_raises(self):
        ans = self._ans(0)
        del ans[5]
        with self.assertRaises(ValueError):
            phq_a.score(ans)

    def test_invalid_value_raises(self):
        ans = self._ans(0)
        ans[1] = 4
        with self.assertRaises(ValueError):
            phq_a.score(ans)

    def test_invalid_item_id_raises(self):
        ans = self._ans(0)
        ans[99] = 0
        with self.assertRaises(ValueError):
            phq_a.score(ans)


if __name__ == "__main__":
    unittest.main()
