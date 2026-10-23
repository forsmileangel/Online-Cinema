import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend import main, security, sites
from backend.models import Card, Listing


def listing(src, n=1):
    return Listing(items=[Card(id=f"{src}{i}", title="Neutral", cover="", source=src) for i in range(n)],
                   page=1, has_next=False, title="sample")


class MultiSearchTests(unittest.TestCase):
    def lines(self, sources, behaviour):
        """behaviour[src] is an exception to raise or a (delay, listing) answer."""

        def get(src):
            def search(q, page=1):
                action = behaviour[src]
                if isinstance(action, Exception):
                    raise action
                time.sleep(action[0])
                return action[1]
            return SimpleNamespace(search=search)

        with patch.object(main.sites, "get", side_effect=get):
            return [json.loads(line) for line in main._multi_search_lines("sample", sources)]

    def test_each_source_is_sent_when_it_finishes_with_safe_errors(self):
        out = self.lines(["slow", "fast", "busy", "gone"], {
            "slow": (0.3, listing("slow")),
            "fast": (0, listing("fast", 2)),
            "busy": security.SiteBusy("Busy site", 429, 20),
            "gone": RuntimeError("internal detail"),
        })
        self.assertEqual(out[-1]["source"], "slow")  # Quick answers are not held back by the slowest site.
        by = {line["source"]: line for line in out}
        self.assertEqual([c["id"] for c in by["fast"]["listing"]["items"]], ["fast0", "fast1"])
        self.assertEqual((by["busy"]["ok"], by["busy"]["error"]), (False, "Busy site 請求過多，來源建議 20 秒後重試"))
        self.assertEqual(by["gone"]["error"], "來源站暫時無法使用")  # Exception text never reaches the page.

    def test_sources_past_the_deadline_are_reported_instead_of_awaited(self):
        with patch.object(main, "MULTI_SEARCH_SECONDS", 0.2):
            started = time.monotonic()
            out = self.lines(["quick", "stuck"], {"quick": (0, listing("quick")), "stuck": (1.5, listing("stuck"))})
            self.assertLess(time.monotonic() - started, 1.0)
        by = {line["source"]: line for line in out}
        self.assertTrue(by["quick"]["ok"])
        self.assertEqual((by["stuck"]["ok"], by["stuck"]["error"]), (False, "搜尋逾時，請稍後再試"))

    def test_endpoint_keeps_known_sources_once_and_rejects_an_empty_choice(self):
        real = [s["id"] for s in sites.available()]
        with patch.object(main, "_multi_search_lines", side_effect=lambda q, wanted: iter(())) as run:
            response = main.search_multi("sample", sources=f"{real[0]},nope,{real[0]},{real[1]}")
        self.assertEqual(run.call_args.args[1], [real[0], real[1]])
        self.assertEqual(response.media_type, "application/x-ndjson")
        with self.assertRaises(HTTPException):
            main.search_multi("sample", sources="nope,../x")

    def test_every_source_has_a_group_and_groups_stay_together(self):
        groups = [s["group"] for s in sites.available()]
        self.assertTrue(all(groups))
        self.assertEqual(len(set(groups)), len([g for i, g in enumerate(groups) if i == 0 or groups[i - 1] != g]))


if __name__ == "__main__":
    unittest.main()
