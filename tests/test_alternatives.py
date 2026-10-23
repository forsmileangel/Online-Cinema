import unittest
from unittest.mock import Mock, patch

from backend import alternatives, main
from backend.models import Card, Episode, Listing, VideoDetail


def site(cards, episodes=None, fail=False):
    def search(query, page=1):
        if fail:
            raise RuntimeError("blocked")
        return Listing(items=[Card(id=i, title=t, cover="", source="x") for i, t in cards], page=1, has_next=False)

    def fetch_video(video_id):
        titles = (episodes or {}).get(video_id, [])
        return VideoDetail(id=video_id, title=dict(cards)[video_id], cover="", playlist="",
                           episodes=[Episode(id=str(n), title=t, playlist="") for n, t in enumerate(titles, 1)])
    return Mock(search=Mock(side_effect=search), fetch_video=Mock(side_effect=fetch_video))


class AlternativesTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(alternatives, "_cache", {}); p.start(); self.addCleanup(p.stop)

    def test_episode_numbers_and_titles(self):
        for label, number in (("第7集", 7), ("第 07 集", 7), ("7", 7), ("EP07", 7), ("第3話", 3), ("正片", None)):
            self.assertEqual(alternatives.episode_number(label), number, label)
        self.assertEqual(alternatives.episode_number("正片", "12"), 12)
        # One upload can hold several episodes; it covers the whole range, not just its first number.
        self.assertEqual(alternatives.episode_number("《某劇》第6-7集", "6"), 6)
        self.assertEqual(alternatives.episode_span("第6-7集", "6"), (6, 7))
        self.assertEqual(alternatives.episode_span("第 1 ～ 3 集"), (1, 3))
        self.assertEqual(alternatives.episode_span("第8集", "7"), (8, 8))
        self.assertEqual(alternatives.episode_span("第9-2集", "9"), (9, 9))
        self.assertEqual(alternatives._core("中頭獎還是要上班 로또 1등도 출근합니다"), alternatives._core("中头奖还是要上班"))
        self.assertEqual(alternatives._core("某劇(2026)"), alternatives._core("某劇"))
        self.assertEqual([alternatives._season(t) for t in ("某劇", "某劇 第一季", "某劇 第2季", "某劇 第十二季", "某劇 Season 3")],
                         [1, 1, 2, 12, 3])

    def test_same_group_sources_report_their_episodes(self):
        sources = {
            "mvffm": site([("41", "中頭獎還是要上班"), ("32", "頭獎危機")], {"41": [f"第 {n} 集" for n in range(1, 11)]}),
            "mmov": site([("51", "中頭獎還是要上班 第2季")], {"51": ["第01集"]}),
            "gimy": site([], fail=True),
            "hongguo": site([("9", "中頭獎還是要上班")]),
        }
        available = [{"id": s, "label": s, "group": "短劇" if s == "hongguo" else "影劇"} for s in ("chinaq", *sources)]
        with patch.object(alternatives.sites, "available", return_value=available), \
                patch.object(alternatives.sites, "get", side_effect=lambda s: sources[s]):
            found = alternatives.find("chinaq", "kr-1", "中頭獎還是要上班 로또 1등도 출근합니다")
            again = alternatives.find("chinaq", "kr-1", "中頭獎還是要上班 로또 1등도 출근합니다")
        # Another show, another season, another group and a refused site are all left out.
        self.assertEqual([(f["source"], f["id"]) for f in found], [("mvffm", "41")])
        self.assertEqual(found[0]["episodes"][0], [1, "1"])
        self.assertEqual(len(found[0]["episodes"]), 10)
        sources["hongguo"].search.assert_not_called()
        self.assertIs(again, found)
        self.assertEqual(sources["mvffm"].search.call_count, 1)  # Answered from the cache.
        sources["mvffm"].search.assert_called_with("中頭獎還是要上班", page=1)

    def test_endpoint_checks_the_source(self):
        with patch.object(alternatives, "find", return_value=[]) as find:
            self.assertEqual(main.other_sources("chinaq", "kr-1", "某劇"), {"items": []})
        find.assert_called_once_with("chinaq", "kr-1", "某劇")
        with self.assertRaises(main.HTTPException):
            main.other_sources("nope", "1", "某劇")


if __name__ == "__main__":
    unittest.main()
