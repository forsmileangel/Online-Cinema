import unittest
from pathlib import Path
from unittest.mock import patch

from backend.sites import available, chinaq
from backend.security import UnsafeURL, assert_hls_url, assert_image_url


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ChinaqTests(unittest.TestCase):
    def setUp(self):
        chinaq._all_memo = None
        from backend import security
        security._extra_media_hosts.clear()

    def test_source_is_registered(self):
        ids = {s["id"] for s in available()}
        self.assertIn("chinaq", ids)
        self.assertEqual(next(s["label"] for s in available() if s["id"] == "chinaq"), "中國人線上看")

    def test_parse_home_sections(self):
        rows = chinaq.parse_home_sections(load("chinaq_home.html"))
        self.assertEqual([r[0] for r in rows], ["update", "cn"])
        self.assertEqual(rows[0][2][0].id, "kr-202621506")
        self.assertEqual(rows[1][2][0].id, "cn-202659534")
        self.assertEqual(rows[1][2][0].source, "chinaq")
        self.assertTrue(rows[1][2][0].cover.startswith("/api/img?u="))
        from urllib.parse import unquote
        self.assertIn("chinaq.fun/img_th/202659534.jpg", unquote(rows[1][2][0].cover))

    def test_parse_all_catalog(self):
        cards = chinaq.parse_cards(load("chinaq_all.html"))
        self.assertEqual([c.id for c in cards], ["cn-202659534", "kr-202621506", "jp-202614255"])
        self.assertEqual(cards[0].title, "獵罪現場")

    def test_browse_filters_region_from_catalog(self):
        with patch.object(chinaq, "_get", return_value=load("chinaq_all.html")):
            listing = chinaq.browse("cn", page=1)
        self.assertEqual([c.id for c in listing.items], ["cn-202659534"])
        self.assertEqual(listing.title, "陸劇")

    def test_search_filters_titles(self):
        with patch.object(chinaq, "_get", return_value=load("chinaq_all.html")):
            listing = chinaq.search("豐臣")
        self.assertEqual([c.id for c in listing.items], ["jp-202614255"])

    def test_browse_unknown_rejected(self):
        with self.assertRaises(UnsafeURL):
            chinaq.browse("nope")

    def test_fetch_video_episodes_and_qplays(self):
        def fake_get(path: str) -> str:
            if path.startswith("/tv-cn/"):
                return load("chinaq_show.html")
            raise AssertionError(path)

        with patch.object(chinaq, "_get", side_effect=fake_get), patch.object(
            chinaq, "_qplays", return_value=["https://ukzy.ukubf3.com/20260914/x/index.m3u8"]
        ), patch.object(chinaq, "proxied_media", side_effect=lambda u: "/api/hls?u=" + u), patch.object(chinaq, "dlna_media_url"):
            detail = chinaq.fetch_video("cn-202659534", ep="24")
        self.assertEqual(detail.title, "獵罪現場")
        self.assertEqual([e.id for e in detail.episodes], ["1", "23", "24"])
        self.assertTrue(detail.episodes[2].playlist)
        self.assertEqual(detail.episodes[0].playlist, "")
        self.assertIn("ukubf3.com", detail.playlist)
        self.assertEqual(detail.genres[0].name, "陸劇")
        self.assertEqual(detail.release_date, "2026-09-07")

    def test_fetch_video_default_resolves_first_episode(self):
        seen: list[str] = []

        def fake_qplays(num: str, ep: str) -> list[str]:
            seen.append(ep)
            return ["https://v.lzcdn27.com/x/index.m3u8"]

        with patch.object(chinaq, "_get", return_value=load("chinaq_show.html")), patch.object(
            chinaq, "_qplays", side_effect=fake_qplays
        ), patch.object(chinaq, "proxied_media", side_effect=lambda u: "/api/hls?u=" + u), patch.object(chinaq, "dlna_media_url"):
            chinaq.fetch_video("cn-202659534")
        self.assertEqual(seen, ["1"])

    def test_cdn_and_cover_hosts_are_allowed(self):
        with patch("backend.security._assert_not_private"):
            assert_hls_url("https://ukzy.ukubf3.com/20260914/x/index.m3u8")
            assert_hls_url("https://v.lzcdn27.com/x/index.m3u8")
            assert_hls_url("https://super.ffzy-online6.com/x/index.m3u8")
            assert_image_url("https://chinaq.fun/img_th/202659534.jpg")
        with self.assertRaises(UnsafeURL):
            assert_hls_url("https://evil.com/a.m3u8")

    def test_playlist_child_host_is_remembered_not_open_proxy(self):
        from backend.hls_proxy import rewrite_playlist

        chinaq_parent = "https://vod12.wgslsw.com/index.m3u8"
        child = "https://cdn.unexpected-yun.net/seg.ts"
        text = "#EXTM3U\n" + child + "\n"
        with patch("backend.security._assert_not_private"):
            out = rewrite_playlist(text, chinaq_parent)
            self.assertIn("unexpected-yun.net", out)
            assert_hls_url(child)
            with self.assertRaises(UnsafeURL):
                rewrite_playlist("#EXTM3U\nhttps://evil.com/x.ts\n", "https://surrit.com/a.m3u8")
        with self.assertRaises(UnsafeURL):
            assert_hls_url("https://evil.com/a.m3u8")


if __name__ == "__main__":
    unittest.main()
