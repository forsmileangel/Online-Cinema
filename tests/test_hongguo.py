import unittest
from pathlib import Path
from unittest.mock import patch

from backend.sites import available, hongguo


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class HongguoTests(unittest.TestCase):
    def test_source_is_registered(self):
        ids = {s["id"] for s in available()}
        self.assertIn("hongguo", ids)
        self.assertEqual(next(s["label"] for s in available() if s["id"] == "hongguo"), "紅果短劇")

    def test_parse_home_cards(self):
        cards = hongguo.parse_cards(load("hongguo_home.html"))
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].id, "3490")
        self.assertEqual(cards[0].source, "hongguo")
        self.assertEqual(cards[0].duration, "全71集")
        from urllib.parse import unquote
        self.assertIn("img.picbf.com", unquote(cards[0].cover))
        self.assertTrue(cards[0].cover.startswith("/api/img?u="))

    def test_parse_list_cards(self):
        cards = hongguo.parse_cards(load("hongguo_list.html"))
        self.assertEqual(cards[0].id, "55272")
        self.assertEqual(cards[0].title, "篮门女将")

    def test_fetch_video_episodes(self):
        def fake_get(path: str) -> str:
            if path.startswith("/voddetail/"):
                return load("hongguo_detail.html")
            if path.startswith("/vodplay/"):
                return load("hongguo_play.html")
            raise AssertionError(path)

        with patch.object(hongguo, "_get", side_effect=fake_get), patch.object(
            hongguo, "proxied_media", side_effect=lambda u: "/api/hls?u=" + u
        ):
            detail = hongguo.fetch_video("3490")
        self.assertEqual(detail.title, "人前不熟，人后熟透")
        self.assertEqual([e.id for e in detail.episodes], ["1", "2", "3"])
        self.assertTrue(detail.episodes[0].playlist)
        self.assertTrue(detail.episodes[1].playlist)
        self.assertEqual(detail.episodes[2].playlist, "")
        self.assertIn("s2.bfllvip.com", detail.playlist)
        self.assertEqual(detail.genres[0].kind, "class")
        self.assertEqual(detail.related[0].id, "1001")

    def test_fetch_video_ep_path(self):
        seen = []

        def fake_get(path: str) -> str:
            seen.append(path)
            if path.startswith("/voddetail/"):
                return load("hongguo_detail.html")
            return load("hongguo_play.html")

        with patch.object(hongguo, "_get", side_effect=fake_get), patch.object(
            hongguo, "proxied_media", side_effect=lambda u: "/api/hls?u=" + u
        ):
            hongguo.fetch_video("3490", ep="5")
        self.assertIn("/vodplay/3490-1-5.html", seen)

    def test_pages_from_tips(self):
        html = '<ul class="hl-page-wrap"><li class="hl-page-tips"><a>1 / 907</a></li></ul>'
        self.assertEqual(hongguo._pages_from_html(html), 907)

    def test_search_uses_suggest_json(self):
        payload = {
            "code": 1,
            "total": 2,
            "list": [
                {"id": 15297, "name": "宴律，你的白月光回国了", "pic": "/upload/vod/a.png"},
                {"id": 1, "name": "other", "pic": "https://img.picbf.com/b.jpg"},
            ],
        }
        with patch.object(hongguo, "_get_json", return_value=payload):
            listing = hongguo.search("宴律")
        self.assertEqual([c.id for c in listing.items], ["15297", "1"])
        self.assertEqual(listing.title, "宴律")


if __name__ == "__main__":
    unittest.main()
