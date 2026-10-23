import unittest
from unittest.mock import Mock

from backend import terms
from backend.models import Card, Listing


def listing(*titles, notice=None):
    return Listing(items=[Card(id=t, title=t, cover="", source="s") for t in titles], page=1, has_next=False, notice=notice)


class TermsTests(unittest.TestCase):
    def test_wording_is_swapped_both_ways_but_names_are_left_alone(self):
        self.assertEqual(terms.phrase_variants("計程車"), ["出租车"])
        self.assertEqual(terms.phrase_variants("出租车司机"), ["計程車司機"])
        self.assertEqual(terms.phrase_variants("捷運"), ["地铁"])
        self.assertEqual(terms.phrase_variants("軟體"), ["软件"])  # From OpenCC's own table.
        for unchanged in ("總裁", "中頭獎還是要上班", "SSIS-001", "中头奖还是要上班", ""):
            self.assertEqual(terms.phrase_variants(unchanged), [], unchanged)

    def test_no_results_answers_with_the_other_wording(self):
        site = Mock(search=Mock(side_effect=lambda q, page=1: listing("时光出租车", "出租屋") if q == "出租车" else listing()))
        found = terms.search(site, "計程車")
        self.assertEqual([c.title for c in found.items], ["时光出租车"])  # Loose matches dropped.
        self.assertIn("改用「出租车」", found.notice)

    def test_first_page_adds_the_other_wording_without_duplicates(self):
        def search(q, page=1):
            return listing("午夜出租車", "東京出租車") if q == "出租车" else listing("計程車司機", "午夜出租車", notice="站方提示")
        site = Mock(search=Mock(side_effect=search))
        found = terms.search(site, "計程車")
        self.assertEqual([c.title for c in found.items], ["計程車司機", "午夜出租車", "東京出租車"])
        self.assertEqual(found.notice, "站方提示 也加入「出租车」的搜尋結果。")
        # Later pages keep the source's own paging.
        site.search.reset_mock()
        terms.search(site, "計程車", 2)
        site.search.assert_called_once_with("計程車", page=2)

    def test_plain_queries_cost_one_search_and_reworded_failures_are_ignored(self):
        site = Mock(search=Mock(return_value=listing()))
        terms.search(site, "總裁")
        site.search.assert_called_once()
        site = Mock(search=Mock(side_effect=lambda q, page=1: listing("計程車") if q == "計程車" else (_ for _ in ()).throw(RuntimeError())))
        self.assertEqual([c.title for c in terms.search(site, "計程車").items], ["計程車"])


if __name__ == "__main__":
    unittest.main()
