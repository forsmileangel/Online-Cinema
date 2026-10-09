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


    def test_search_simplifies_query_and_preserves_original_title(self):
        from urllib.parse import quote
        with patch.object(hongguo, "_get_json", return_value={"total": 100, "list": [{"id": 1, "name": "Neutral"}]}) as get:
            result = hongguo.search("總裁", page=2)
        get.assert_called_once_with(f"/index.php/ajax/suggest?mid=1&wd={quote('总裁')}&limit=50&page=2")
        self.assertEqual(result.title, "總裁")

    def test_empty_simplified_search_retries_original(self):
        from urllib.parse import parse_qs, urlparse
        for empty in ({"total": 0, "list": []}, {"total": 10, "list": []}):
            with patch.object(hongguo, "_get_json", side_effect=[empty, {"total": 1, "list": [{"id": 1}]}]) as get:
                self.assertEqual(len(hongguo.search("總裁").items), 1)
            self.assertEqual([parse_qs(urlparse(c.args[0]).query)["wd"][0] for c in get.call_args_list], ["总裁", "總裁"])

    def test_covers_follow_the_hosts_the_site_publishes(self):
        from urllib.parse import parse_qs, urlparse
        from backend import security
        with patch.object(security.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]), \
             patch.object(security, "_public_hosts", {}):
            def upstream(raw):
                proxied = hongguo._cover(raw)
                return parse_qs(urlparse(proxied).query)["u"][0] if proxied else ""
            # Relative uploads live on the site itself, not on img.picbf.com.
            self.assertEqual(upstream("/upload/vod/20260819-1/a.jpg"), hongguo.ORIGIN + "/upload/vod/20260819-1/a.jpg")
            for url in ("https://img.picbf.com/upload/vod/a.jpg", "https://p.bfvp26.com/upload/vod/a.jpg",
                        "https://img.bfzypic.com/upload/vod/a.jpg", "https://pub2.bfzy.tv/upload/vod/a.jpg",
                        "https://hongniuzyimage.com/cover/a.jpg", "https://image.tmdb.org/t/p/w600/a.jpg"):
                with self.subTest(url=url):
                    self.assertEqual(upstream(url), url)
            for url in ("https://evil.test/a.jpg", "https://bfvp26.com.evil.test/a.jpg",
                        "/template/conch/asset/img/load.gif", "http://img.picbf.com/a.jpg"):
                with self.subTest(url=url):
                    self.assertEqual(upstream(url), "")
        self.assertTrue(hongguo.is_cover_host("p.bfvp26.com"))
        self.assertFalse(hongguo.is_cover_host("notbfvp26.com"))

    def test_unchanged_keyword_sends_one_request(self):
        for query in ("总裁", "ABC-123"):
            with patch.object(hongguo, "_get_json", return_value={"total": 0, "list": []}) as get:
                hongguo.search(query)
            get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
