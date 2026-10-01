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

    def test_negation_not_hit(self):
        """否定前缀包围的危机词不应命中（避免误报）。"""
        self.assertFalse(detect_crisis("我不想死，我想好好活着"))
        self.assertFalse(detect_crisis("他没有自杀的念头"))
        self.assertFalse(detect_crisis("我不会轻生，只是压力大"))
        self.assertFalse(detect_crisis("不要割腕，要爱惜自己"))

    def test_negation_mixed_hit(self):
        """同一句中既有否定又有肯定时，肯定部分仍命中。"""
        self.assertTrue(detect_crisis("我不想死，但我有过自杀的念头"))

    def test_negation_window_boundary(self):
        """否定词允许常见修饰字插入（一点都不想死 / 根本没有轻生），超出窗口仍命中。"""
        self.assertFalse(detect_crisis("我一点都不想死"))  # 「不想」+ 修饰字，不命中
        self.assertFalse(detect_crisis("根本没有轻生念头"))  # 「没有」+ 修饰字，不命中
        # 否定词与关键词之间插入非修饰字（名词），不构成否定修饰，仍命中
        self.assertTrue(detect_crisis("不想吃饭，只想自杀"))  # 「不想」修饰「吃饭」非「自杀」
        self.assertTrue(detect_crisis("我以前从未想过自杀"))  # 「未」不在否定表，仍命中


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
