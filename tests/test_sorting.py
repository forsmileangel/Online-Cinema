import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend import sorting
from backend.models import Listing, SortOption
from backend.security import UnsafeURL, safe_sort

ORDERS = [SortOption(id="time", label="最新"), SortOption(id="hits", label="最熱")]
LINKED = [SortOption(id="hot", label="熱門", kind="hot"), SortOption(id="latest", label="最新", kind="latest")]


def site(options=None):
    fake = SimpleNamespace(browse=Mock(return_value=Listing(items=[], page=1, has_next=False)))
    if options is not None:
        fake.sorts = Mock(return_value=options)
    return fake


class SortingTests(unittest.TestCase):
    def test_sort_ids_are_short_plain_words(self):
        self.assertEqual(safe_sort(""), "")
        self.assertEqual(safe_sort(None), "")
        self.assertEqual(safe_sort(" Hits "), "hits")
        self.assertEqual(safe_sort("most-viewed_7"), "most-viewed_7")
        for bad in ("-x", "a b", "x" * 25, "../x", "x;y", "排序"):
            with self.subTest(bad=bad), self.assertRaises(UnsafeURL):
                safe_sort(bad)

    def test_an_offered_order_is_passed_to_the_source(self):
        fake = site(ORDERS)
        listing = sorting.browse(fake, "all", None, 2, "hits")
        fake.browse.assert_called_once_with("all", page=2, sort="hits")
        fake.sorts.assert_called_once_with("all", None)
        self.assertEqual((listing.sort, [o.id for o in listing.sorts]), ("hits", ["time", "hits"]))

    def test_default_or_unknown_order_asks_for_the_plain_list(self):
        for sort in ("", "time", "score", "hot"):
            with self.subTest(sort=sort):
                fake = site(ORDERS)
                listing = sorting.browse(fake, "class", "古装", 1, sort)
                fake.browse.assert_called_once_with("class", slug="古装", page=1)
                self.assertEqual(listing.sort, "time")

    def test_linked_lists_mark_the_one_shown(self):
        fake = site(LINKED)
        listing = sorting.browse(fake, "latest", None, 1, "hot")
        fake.browse.assert_called_once_with("latest", page=1)
        self.assertEqual(listing.sort, "latest")
        self.assertEqual(sorting.browse(site(LINKED), "home", None, 1).sort, "")

    def test_sources_without_sorts_are_unchanged(self):
        fake = site()
        listing = sorting.browse(fake, "latest", "x", 3, "hits")
        fake.browse.assert_called_once_with("latest", slug="x", page=3)
        self.assertEqual((listing.sorts, listing.sort), ([], ""))
        self.assertEqual(sorting.browse(site([]), "latest", None, 1, "hits").sorts, [])


if __name__ == "__main__":
    unittest.main()
