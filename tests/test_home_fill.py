import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import home_fill
from backend.models import Card, HomeRow, Listing, PickChip, PickGroup


def cards(prefix: str, n: int = 6) -> list[Card]:
    return [Card(id=f"{prefix}-{i}", title=f"{prefix} {i}", cover="", source="neutral") for i in range(n)]


class FakeSite:
    def __init__(self, sizes=None, delay=0.0, fail=()):
        self.calls = []
        self.sizes = sizes or {}
        self.delay = delay
        self.fail = set(fail)
        self.lock = threading.Lock()

    def browse(self, kind, slug=None, page=1):
        with self.lock:
            self.calls.append((kind, slug, page))
        if self.delay:
            time.sleep(self.delay)
        if kind in self.fail:
            raise RuntimeError("source down")
        return Listing(items=cards(f"{kind}{slug or ''}", self.sizes.get(kind, 30)), page=page, has_next=True)


def row(kind: str, slug: str = "", n: int = 6) -> HomeRow:
    return HomeRow(kind=kind, slug=slug, title=kind, items=cards(kind, n))


PICKS = [
    PickGroup(title="排序", items=[PickChip(name="全部", kind="all"), PickChip(name="熱門", kind="popular"),
                                 PickChip(name="搜尋", kind="kw", slug="neutral")]),
    PickGroup(title="分類", items=[PickChip(name="分類一", kind="category", slug="one"),
                                 PickChip(name="分類二", kind="category", slug="two")]),
]


class HomeFillTests(unittest.TestCase):
    def setUp(self):
        home_fill.reset()
        self.addCleanup(home_fill.reset)

    def test_sparse_home_gets_the_first_picks_not_shown_yet(self):
        site = FakeSite()
        rows = home_fill.fill("neutral", site, [row("all")], PICKS)
        self.assertEqual([(r.kind, r.slug, r.title) for r in rows],
                         [("all", "", "all"), ("popular", "", "熱門"), ("category", "one", "分類一"),
                          ("category", "two", "分類二")])
        self.assertEqual(sorted(site.calls), [("category", "one", 1), ("category", "two", 1), ("popular", None, 1)])
        self.assertTrue(all(len(r.items) <= home_fill.MAX_ITEMS for r in rows))
        self.assertTrue(all(r.more for r in rows))

    def test_home_with_enough_rows_or_no_picks_is_left_alone(self):
        site = FakeSite()
        full = [row(k) for k in ("a", "b", "c", "d")]
        self.assertEqual(home_fill.fill("neutral", site, full, PICKS), full)
        self.assertEqual(home_fill.fill("neutral", site, [row("a")], []), [row("a")])
        self.assertEqual(site.calls, [])

    def test_source_can_switch_filling_off_or_skip_lists(self):
        site = FakeSite()
        site.HOME_FILL = False
        self.assertEqual(len(home_fill.fill("neutral", site, [row("all")], PICKS)), 1)
        site = FakeSite()
        site.HOME_FILL_SKIP = ("popular",)
        rows = home_fill.fill("neutral", site, [row("all")], PICKS)
        self.assertEqual([r.kind for r in rows], ["all", "category", "category"])

    def test_empty_rows_leave_room_but_are_not_filled_twice(self):
        site = FakeSite()
        rows = home_fill.fill("neutral", site, [row("all"), row("popular", n=0), row("x"), row("y")], PICKS)
        self.assertEqual([(r.kind, r.slug) for r in rows],
                         [("all", ""), ("popular", ""), ("x", ""), ("y", ""), ("category", "one")])

    def test_short_or_failed_lists_are_left_out(self):
        site = FakeSite(sizes={"popular": 3}, fail={"category"})
        rows = home_fill.fill("neutral", site, [row("all")], PICKS)
        self.assertEqual([r.kind for r in rows], ["all"])

    def test_second_load_comes_from_the_kept_rows(self):
        site = FakeSite()
        home_fill.fill("neutral", site, [row("all")], PICKS)
        calls = len(site.calls)
        rows = home_fill.fill("neutral", site, [row("all")], PICKS)
        self.assertEqual(len(site.calls), calls)
        self.assertEqual(len(rows), 4)

    def test_failures_are_retried_after_a_short_while_only(self):
        site = FakeSite(fail={"popular", "category"})
        home_fill.fill("neutral", site, [row("all")], PICKS)
        home_fill.fill("neutral", site, [row("all")], PICKS)
        self.assertEqual(len(site.calls), 3)
        clock = time.monotonic() + home_fill.RETRY_TTL + 1
        with patch.object(home_fill.time, "monotonic", return_value=clock):
            home_fill.fill("neutral", site, [row("all")], PICKS)
        self.assertEqual(len(site.calls), 6)

    def test_slow_lists_do_not_hold_the_home_page_and_arrive_on_the_next_load(self):
        site = FakeSite(delay=0.3)
        with patch.object(home_fill, "BUDGET", 0.05):
            started = time.monotonic()
            rows = home_fill.fill("neutral", site, [row("all")], PICKS)
            self.assertLess(time.monotonic() - started, 0.25)
            self.assertEqual(len(rows), 1)
            time.sleep(0.5)
            rows = home_fill.fill("neutral", site, [row("all")], PICKS)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(site.calls), 3)

    def test_loads_at_the_same_time_share_one_fetch_per_list(self):
        site = FakeSite(delay=0.2)
        results = []
        threads = [threading.Thread(target=lambda: results.append(home_fill.fill("neutral", site, [row("all")], PICKS)))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(site.calls), 3)
        self.assertTrue(all(len(r) == 4 for r in results))

    def test_rows_take_more_link_from_the_source(self):
        site = SimpleNamespace(more_link=lambda kind: kind != "home-only")
        rows = home_fill.rows(site, [("home-only", "首頁專屬", cards("a")), ("latest", "最新", cards("b"))])
        self.assertEqual([(r.kind, r.more, r.slug) for r in rows], [("home-only", False, ""), ("latest", True, "")])
        self.assertTrue(all(r.more for r in home_fill.rows(SimpleNamespace(), [("a", "A", [])])))


if __name__ == "__main__":
    unittest.main()
