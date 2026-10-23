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
            for query in ("蘭香", "如故"):  # The second one hits the cooldown without a request.
                with self.assertRaises(gimy.SourceUnavailable) as raised:
                    gimy.search(query)
                self.assertIn("Cloudflare", str(raised.exception))
        self.assertEqual(get.call_count, 1)
        self.assertIsNone(gimy._hot_memo)

    def test_search_rate_limit_stays_retryable(self):
        with patch.object(gimy, "_get", side_effect=gimy.SiteBusy("Gimy 劇迷", 429, 20)):
            with self.assertRaises(gimy.SiteBusy):
                gimy.search("蘭香")

    def test_blocked_search_falls_back_to_loaded_catalog_with_reason(self):
        with patch.object(gimy, "_get", return_value=load("gimy_home.html")):
            gimy.home_rows()
        with patch.object(gimy, "_get", side_effect=gimy.SiteBusy("Gimy 劇迷", 403)):
            listing = gimy.search("蘭香")
        self.assertEqual([c.id for c in listing.items], ["485760"])
        self.assertIn("被站方驗證擋住", listing.notice)
        with patch.object(gimy, "_get", side_effect=gimy.SiteBusy("Gimy 劇迷", 403)):
            with self.assertRaises(gimy.SourceUnavailable):  # Nothing loaded matches: say why, not "no results".
                gimy.search("完全不相干的片名")

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

    def test_episode_list_spans_every_line_not_just_the_one_playing(self):
        def route(sid, title, eps):
            links = "".join(f'<a class="ep" href="/play/485591-{sid}-{e}.html">{e}</a>' for e in eps)
            return f'<div class="route-title">{title}</div><div class="eps episodes-route" data-route-sid="{sid}">{links}</div>'

        # The preferred cloud line only lists the newest episodes; another line has them all.
        detail = "<h1>Neutral series</h1>" + route(10, "清晰雲", range(7, 11)) + route(8, "藍光線路", range(1, 11))
        played = []

        def fake_get(path: str, **kwargs) -> str:
            if path.startswith("/detail/"):
                return detail
            played.append(path)
            return load("gimy_play.html")

        with patch.object(gimy, "_get", side_effect=fake_get), patch.object(
            gimy, "proxied_media", side_effect=lambda u: "/api/hls?u=" + u
        ):
            video = gimy.fetch_video("485591", ep="7")
        self.assertEqual([e.id for e in video.episodes], [str(i) for i in range(1, 11)])
        self.assertEqual(played, ["/play/485591-10-7.html"])
        self.assertTrue(next(e for e in video.episodes if e.id == "7").playlist)
        self.assertEqual(video.resolved_episode_id, "7")

    def test_cdn_and_cover_hosts_are_allowed(self):
        with patch("backend.security._assert_not_private"):
            assert_hls_url("https://vv.jisuzyv.com/play/x/index.m3u8")
            assert_hls_url("https://svip.ryiplay18.com/20260911/a/index.m3u8")
            assert_image_url("https://imgs.1777cdn.com/upload/vod/a.jpg")
        with self.assertRaises(UnsafeURL):
            assert_hls_url("https://evil.com/a.m3u8")


if __name__ == "__main__":
    unittest.main()
