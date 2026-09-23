import unittest
from pathlib import Path
from unittest.mock import patch

from backend.security import UnsafeURL, assert_hls_url, assert_image_url
from backend.sites import available, dramaq


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class DramaqTests(unittest.TestCase):
    def setUp(self):
        dramaq._all_memo = None

    def test_source_is_registered(self):
        self.assertEqual(next(s["label"] for s in available() if s["id"] == "dramaq"), "DramaQ")

    def test_parse_home_sections(self):
        rows = dramaq.parse_home_sections(load("dramaq_home.html"))
        self.assertEqual([row[0] for row in rows], ["latest", "hot"])
        self.assertEqual(rows[0][2][0].id, "202690895")
        self.assertEqual(rows[0][2][0].source, "dramaq")
        self.assertIn("dramasq.io", rows[0][2][0].cover)

    def test_browse_region_and_reject_unknown(self):
        with patch.object(dramaq, "_get", return_value=load("dramaq_list.html")):
            listing = dramaq.browse("kr", page=2)
        self.assertEqual([card.id for card in listing.items], ["202601001", "202601002"])
        self.assertEqual(listing.title, "韓劇")
        self.assertFalse(listing.has_next)
        with self.assertRaises(UnsafeURL):
            dramaq.browse("nope")

    def test_search_strips_episode_suffix(self):
        with patch.object(dramaq, "_get", return_value=load("dramaq_search.html")):
            listing = dramaq.search("挑情")
        self.assertEqual(listing.items[0].id, "202635531")
        self.assertEqual(listing.items[0].title, "挑情醜聞")

    def test_fetch_video_fills_episode_count_and_uses_first_https_stream(self):
        def fake_get(path: str) -> str:
            if path.startswith("/detail/"):
                return load("dramaq_detail.html")
            if path.startswith("/vodplay/"):
                return load("dramaq_play.html")
            raise AssertionError(path)

        plays = [
            "http://evil.example/a.m3u8",
            "https://hn.bfvvs.com/a/index.m3u8",
        ]
        with patch.object(dramaq, "_get", side_effect=fake_get), patch.object(dramaq, "_plays", return_value=plays), patch.object(
            dramaq, "proxied_media", side_effect=lambda url: "/api/hls?u=" + url
        ):
            detail = dramaq.fetch_video("202690895")
        self.assertEqual(detail.title, "未來全明星 第八季(All American Season 8)")
        self.assertEqual(detail.release_date, "2026")
        self.assertEqual([tag.name for tag in detail.genres], ["劇情", "運動"])
        self.assertEqual([episode.id for episode in detail.episodes], [f"ep{n}" for n in range(1, 14)])
        self.assertIn("bfvvs.com", detail.playlist)
        self.assertTrue(detail.episodes[11].playlist)
        self.assertFalse(detail.episodes[0].playlist)

    def test_cdn_and_cover_hosts_are_allowed(self):
        with patch("backend.security._assert_not_private"):
            assert_hls_url("https://hn.bfvvs.com/a/index.m3u8")
            assert_hls_url("https://hd.kuktxu.com/a/index.m3u8")
            assert_image_url("https://dramasq.io/ssimg/202690895.jpg")
        with self.assertRaises(UnsafeURL):
            assert_hls_url("https://evil.example/a.m3u8")


if __name__ == "__main__":
    unittest.main()
