import unittest

from app.scales.scared import scared, SCARED_ITEMS, POSITIVE_CUTOFF
from app.scales.base import Severity, CrisisLevel


class TestSCARED(unittest.TestCase):
    def _ans(self, value):
        return {it["id"]: value for it in SCARED_ITEMS}

    def test_item_count_is_41(self):
        self.assertEqual(len(SCARED_ITEMS), 41)

    def test_total_zero(self):
        r = scared.score(self._ans(0))
        self.assertEqual(r.total_score, 0)
        self.assertEqual(r.severity, Severity.NONE)
        self.assertEqual(r.crisis_level, CrisisLevel.SAFE)

    def test_total_max_82(self):
        r = scared.score(self._ans(2))
        self.assertEqual(r.total_score, 82)  # 41 * 2

    def test_positive_cutoff_boundary_24_vs_25(self):
        ans = self._ans(0)
        for i in range(1, 25):  # 24 题 = 1，共 24 分
            ans[i] = 1
        r24 = scared.score(ans)
        self.assertEqual(r24.total_score, 24)
        self.assertEqual(r24.severity, Severity.NONE)  # <25 无

        ans[25] = 1  # 25 分
        r25 = scared.score(ans)
        self.assertEqual(r25.total_score, 25)
        self.assertEqual(r25.severity, Severity.MILD)  # >=25 阳性

    def test_anxiety_scale_never_triggers_crisis(self):
        # SCARED 无自杀意念单项，满分也不触发危机升级
        r = scared.score(self._ans(2))
        self.assertEqual(r.crisis_level, CrisisLevel.SAFE)
        self.assertEqual(r.crisis_triggers, [])

    def test_missing_raises(self):
        ans = self._ans(0)
        del ans[10]
        with self.assertRaises(ValueError):
            scared.score(ans)

    def test_invalid_value_raises(self):
        ans = self._ans(0)
        ans[1] = 3  # 越界（0-2）
        with self.assertRaises(ValueError):
            scared.score(ans)

    def test_invalid_item_id_raises(self):
        ans = self._ans(0)
        ans[99] = 0
        with self.assertRaises(ValueError):
            scared.score(ans)

    def test_interpretation_contains_score(self):
        r = scared.score(self._ans(0))
        self.assertIn("0/82", r.interpretation)
        self.assertIn("25", r.interpretation)


if __name__ == "__main__":
    unittest.main()
