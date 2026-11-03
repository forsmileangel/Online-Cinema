"""The TV remote: arrows move by position, the search box keeps its keys, the watch page is reachable."""
import unittest

from e2e._base import WATCH, E2ETest
from e2e.cdp import Browser

FOCUS = """(() => { const a = document.activeElement; if (!a || a === document.body) return {tag: 'BODY'};
  const r = a.getBoundingClientRect();
  return {tag: a.tagName, cls: String(a.className), l: r.left + scrollX, t: r.top + scrollY, r: r.right + scrollX, b: r.bottom + scrollY,
          inPlayer: !!a.closest('.player'), inRail: !!a.closest('.ep-rail')}; })()"""


class TvKeysTests(E2ETest):
    def test_arrows_walk_the_home_grid_one_row_at_a_time(self):
        async def run():
            async with Browser(self.base) as b:
                await self.open(b, "/tv", 3)
                await b.wait_for("document.querySelectorAll('.card .poster-link').length >= 8", 40)
                self.assertEqual(await b.ev("document.querySelectorAll('.card')[0].querySelectorAll('[data-tv]').length"), 1)
                await b.ev("document.querySelectorAll('.card .poster-link')[0].focus(); true")
                first = await b.ev(FOCUS)
                await b.key("ArrowRight")
                right = await b.ev(FOCUS)
                self.assertEqual(right["cls"], "poster-link")
                self.assertGreater(right["l"], first["r"] - 1)
                await b.key("ArrowDown")
                down = await b.ev(FOCUS)
                self.assertGreaterEqual(down["t"], right["b"] - 2, "one press reaches the next row")
                self.assertLess(abs(down["l"] - right["l"]), 40, "the column is kept")
                await b.key("ArrowUp")
                up = await b.ev(FOCUS)
                self.assertLess(abs(up["t"] - right["t"]), 2)
                await b.key("ArrowLeft")
                left = await b.ev(FOCUS)
                self.assertLess(abs(left["l"] - first["l"]), 2)
        self.run_async(run())

    def test_search_box_keeps_backspace_and_arrows(self):
        async def run():
            async with Browser(self.base) as b:
                await self.open(b, "/tv", 3)
                await b.wait_for("document.readyState === 'complete' && !!document.querySelector('.card')", 40)
                await b.goto("/tv/search", 3)
                await b.wait_for("document.readyState === 'complete' && !!document.querySelector('input')", 40)
                await b.ev("document.querySelector('.search input').focus(); true")
                await b.send("Input.insertText", {"text": "abc"})
                await b.key("Backspace")
                self.assertEqual(await b.ev("document.activeElement.value"), "ab")
                self.assertEqual(await b.ev("location.pathname"), "/tv/search")
                await b.key("ArrowLeft")
                await b.key("ArrowLeft")
                await b.send("Input.insertText", {"text": "X"})
                self.assertEqual(await b.ev("document.activeElement.value"), "Xab")
                await b.ev("document.activeElement.blur(); true")
                await b.key("Backspace", 1.0)
                self.assertIn(await b.ev("location.pathname"), ("/tv", "/tv/"))
        self.run_async(run())

    @unittest.skipUnless(WATCH, "set CINEMA_WATCH to a watch page whose episode is downloaded")
    def test_watch_page_arrows_reach_the_episodes_and_the_position_is_saved(self):
        async def run():
            async with Browser(self.base) as b:
                await self.open(b, WATCH, 3)
                await b.wait_for("(() => { const v = document.querySelector('video'); return v && v.readyState >= 2 && v.duration > 60; })()", 60)
                await b.ev("document.querySelector('video').play().then(() => true).catch(() => false)")
                await b.wait_for("!document.querySelector('video').paused", 10)
                await b.ev("document.activeElement && document.activeElement.blur(); true")
                chips = await b.ev("document.querySelectorAll('.ep-rail .chip').length")
                if chips:  # Episode chips are disabled while the cast bar scans for TVs.
                    await b.wait_for("!document.querySelector('.ep-rail .chip').disabled", 70)
                t0 = await b.ev("document.querySelector('video').currentTime")
                await b.key("ArrowRight", 0.6)
                t1 = await b.ev("document.querySelector('video').currentTime")
                self.assertTrue(8 < t1 - t0 < 13, "ArrowRight with nothing focused seeks 10 s")
                await b.key("ArrowDown", 0.4)
                f = await b.ev(FOCUS)
                self.assertTrue(f["tag"] != "BODY" and not f["inPlayer"], "ArrowDown leaves the player")
                await b.key("ArrowUp", 0.4)
                self.assertTrue((await b.ev(FOCUS))["cls"].startswith("player"), "ArrowUp from the cast bar lands on the player")
                if chips:
                    await b.key("ArrowUp", 0.4)
                    self.assertTrue((await b.ev(FOCUS))["inRail"], "ArrowUp from the player reaches the episodes")
                    t2 = await b.ev("document.querySelector('video').currentTime")
                    await b.key("ArrowRight", 0.6)
                    t3 = await b.ev("document.querySelector('video').currentTime")
                    self.assertLess(t3 - t2, 3, "ArrowRight on a chip does not seek")
                before = len([x for x in b.blocked if x.startswith("PUT /api/history")])
                await b.ev("document.querySelector('video').pause(); true")
                await b.wait_for("true", 1)
                import asyncio
                await asyncio.sleep(0.8)
                after = len([x for x in b.blocked if x.startswith("PUT /api/history")])
                self.assertGreater(after, before, "pausing writes the position at once (blocked here, but attempted)")
        self.run_async(run())


if __name__ == "__main__":
    unittest.main()
