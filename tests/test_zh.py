import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from backend import zh


class ChineseVariantTests(unittest.TestCase):
    def test_conversions_and_original_first_unique_variants(self):
        self.assertEqual(zh.to_simplified("總裁"), "总裁")
        self.assertEqual(zh.to_traditional("总裁"), "總裁")
        self.assertEqual(zh.variants("總裁"), ["總裁", "总裁"])
        self.assertEqual(zh.variants("总裁"), ["总裁", "總裁"])
        self.assertEqual(zh.variants("ABC-123"), ["ABC-123"])

    def test_taiwan_forms_convert_both_ways(self):
        self.assertEqual(zh.to_simplified("著迷"), "着迷")
        self.assertEqual(zh.to_simplified("裡面"), "里面")
        self.assertEqual(zh.to_traditional("受众"), "受眾")
        self.assertEqual(zh.to_traditional("里面"), "裡面")

    def test_normalize_traditional_folds_variants_and_simplified_input(self):
        self.assertEqual(zh.normalize_traditional("鬥羅大陸"), "斗羅大陸")
        self.assertEqual(zh.normalize_traditional("裏面"), "裡面")
        self.assertEqual(zh.normalize_traditional("庆余年"), "慶餘年")
        self.assertEqual(zh.normalize_traditional("ABC-123"), "ABC-123")

    def test_search_key_ignores_script_variants_and_case(self):
        for left, right in [("鬥羅大陸", "斗罗大陆"), ("裡", "裏"), ("长相思", "長相思"), ("ABC-123", "abc-123")]:
            with self.subTest(left=left, right=right):
                self.assertEqual(zh.search_key(left), zh.search_key(right))

    def test_lazy_converters_are_reused_safely_across_threads(self):
        from opencc import OpenCC
        with patch.object(zh, "_converters", {}), patch("opencc.OpenCC", wraps=OpenCC) as factory:
            self.assertEqual(factory.call_count, 0)
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(zh.variants, ["總裁"] * 32))
            self.assertTrue(all(result == ["總裁", "总裁"] for result in results))
            self.assertEqual(factory.call_count, 2)


if __name__ == "__main__":
    unittest.main()
