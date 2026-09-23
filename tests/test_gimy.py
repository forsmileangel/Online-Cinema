import unittest
from pathlib import Path
from unittest.mock import patch

from backend.sites import available, gimy
from backend.security import UnsafeURL, assert_hls_url, assert_image_url


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class GimyTests(unittest.TestCase):
    def setUp(self):
        validate = patch.object(gimy, "dlna_media_url")
        validate.start(); self.addCleanup(validate.stop)
        gimy._hot_memo = None
        gimy._search_memo.clear()
        gimy._search_links.clear()
        gimy._search_cooldown = None

    def test_source_is_registered(self):
        ids = {s["id"] for s in available()}
        self.assertIn("gimy", ids)
        self.assertEqual(next(s["label"] for s in available() if s["id"] == "gimy"), "Gimy 劇迷")

    def test_parse_home_sections(self):
        rows = gimy.parse_home_sections(load("gimy_home.html"))
        self.assertEqual([r[0] for r in rows], ["featured", "tv"])
        self.assertEqual(rows[0][2][0].id, "485760")
        self.assertEqual(rows[0][2][0].source, "gimy")
        self.assertEqual(rows[0][2][0].duration, "更新第26集")
        self.assertTrue(rows[0][2][0].cover.startswith("/api/img?u="))
        from urllib.parse import unquote
        self.assertIn("1777cdn.com", unquote(rows[0][2][0].cover))

    def test_browse_pages_from_pager(self):
        with patch.object(gimy, "_get", return_value=load("gimy_list.html")):
            listing = gimy.browse("cn", page=1)
        self.assertEqual([c.id for c in listing.items], ["485760", "430098"])
        self.assertEqual(listing.pages, 20)
        self.assertTrue(listing.has_next)
        self.assertEqual(listing.title, "陸劇")

    def test_browse_unknown_rejected(self):
        with self.assertRaises(UnsafeURL):
            gimy.browse("nope")

    def test_search_rejection_without_cached_catalog_is_not_an_empty_result(self):
        with patch.object(gimy, "_get", side_effect=gimy.SiteBusy("Gimy 劇迷", 403)) as get:
            with self.assertRaises(gimy.SiteBusy):
                gimy.search("蘭香")
        self.assertEqual(get.call_count, 1)
        self.assertIsNone(gimy._hot_memo)

    def test_fetch_video_skips_official_line_and_uses_yun_hls(self):
        def fake_get(path: str, **kwargs) -> str:
            if path.startswith("/detail/"):
                return load("gimy_detail.html")
            if path.startswith("/play/485760-7-"):
                return '<script>var player_data={"from":"qq","encrypt":0,"url":"https://v.qq.com/x/cover/a.html"};</script>'
            if path.startswith("/play/"):
                return load("gimy_play.html")
            raise AssertionError(path)

        with patch.object(gimy, "_get", side_effect=fake_get), patch.object(
            gimy, "proxied_media", side_effect=lambda u: "/api/hls?u=" + u
        ):
            detail = gimy.fetch_video("485760", ep="1")
        self.assertEqual(detail.title, "蘭香如故")
        self.assertEqual([e.id for e in detail.episodes], ["1", "2", "3"])
        self.assertIn("jisuzyv.com", detail.playlist)
        self.assertTrue(detail.episodes[0].playlist)
        self.assertEqual(detail.genres[0].name, "陸劇")
        self.assertIn("沈嘉蘭", detail.description or "")

    def test_cdn_and_cover_hosts_are_allowed(self):
        with patch("backend.security._assert_not_private"):
            assert_hls_url("https://vv.jisuzyv.com/play/x/index.m3u8")
            assert_hls_url("https://svip.ryiplay18.com/20260911/a/index.m3u8")
            assert_image_url("https://imgs.1777cdn.com/upload/vod/a.jpg")
        with self.assertRaises(UnsafeURL):
            assert_hls_url("https://evil.com/a.m3u8")


if __name__ == "__main__":
    unittest.main()
