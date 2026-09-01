"""危机安全模块单测。"""
import unittest

from app.core.safety import crisis_message, detect_crisis


class TestDetectCrisis(unittest.TestCase):
    def test_hits_suicide_keyword(self):
        self.assertTrue(detect_crisis("我最近想自杀"))

    def test_hits_self_harm(self):
        self.assertTrue(detect_crisis("我想割腕"))

    def test_hits_light_life(self):
        self.assertTrue(detect_crisis("我有轻生的念头"))

    def test_safe_message(self):
        self.assertFalse(detect_crisis("我有点难过，考试没考好"))

    def test_empty_and_none(self):
        self.assertFalse(detect_crisis(""))
        self.assertFalse(detect_crisis(None))


class TestCrisisMessage(unittest.TestCase):
    def test_contains_hotline(self):
        self.assertIn("12355", crisis_message())

    def test_is_warm_and_firm(self):
        msg = crisis_message()
        # 温暖：承认痛苦；坚定：明确不能替代专业帮助
        self.assertTrue(any(w in msg for w in ["痛苦", "感受", "陪"]))
        self.assertTrue(any(w in msg for w in ["专业", "帮助", "老师"]))


if __name__ == "__main__":
    unittest.main()
