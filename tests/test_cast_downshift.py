import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, quote, urlparse

from backend import cast, cast_session, hls_proxy, offline, read_ahead, security
from backend.models import Episode, VideoDetail

ORIGIN = "http://192.168.1.2:6969"
TV = "192.168.1.13"
MASTER = "https://cdn.example/show/master.m3u8"
BEST = dict(url="https://cdn.example/show/1080.m3u8", height=1080)
LOWER = dict(url="https://cdn.example/show/720.m3u8", height=720)


def upstream_of(url):
    return parse_qs(urlparse(url).query)["u"][0]


def playing(content, position=600.0, **values):
    return dict(uuid="tv", content_id=content, playing=True, paused=False, idle=False, buffering=False,
                current_time=position, duration=2700, idle_reason="", **values)


class DownshiftTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(security, "_assert_not_private"); p.start(); self.addCleanup(p.stop)
        security.remember_media_host("cdn.example", source="mmov")
        self.detail = VideoDetail(id="123", source="mmov", title="測試劇", cover="", playlist="",
                                  episodes=[Episode(id="1", title="第1集", playlist="/api/hls?u=" + quote(MASTER, safe=""))])
        self.plays = []
        def play(url, mime, title, position, uuid, *, guard=None, marker="", expected=""):
            self.plays.append(dict(url=url, position=position, marker=marker, expected=expected))
            return playing(url + f"&cast_session={marker}", position)
        for target, name, value in ((cast, "lan_media_origin", Mock(return_value=ORIGIN)), (cast, "check_media_origin", Mock()),
                                    (cast, "is_nest_hub", Mock(return_value=False)), (cast, "play", Mock(side_effect=play)),
                                    (cast, "session_content", Mock(return_value="")), (offline, "fetch_detail", Mock(return_value=self.detail)),
                                    (offline, "local_detail", Mock(return_value=None)), (offline, "playback", Mock()),
                                    (read_ahead, "prime", Mock()), (cast, "device_host", Mock(return_value=TV)),
                                    (read_ahead, "_receivers", set())):
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)

    def session(self, renditions):
        session = cast_session.PlaybackSession(dict(uuid="tv", source="mmov", video_id="123", episode_id="1", autoplay_next=False))
        session.save = Mock()
        session.prefetch = Mock()
        with patch.object(hls_proxy, "renditions", return_value=renditions):
            session._load("1")
        return session

    def test_receiver_gets_the_highest_rendition_and_is_read_ahead_by_address(self):
        session = self.session(dict(best=BEST, lower=LOWER, full_hd=None))
        first = self.plays[0]
        self.assertEqual(upstream_of(first["url"]), BEST["url"])
        self.assertNotIn("rx=", first["url"])  # The TV's URLs stay as short as ever.
        self.assertTrue(read_ahead.is_receiver(TV))
        self.assertEqual((session.get()["quality"], session.get()["quality_label"]), ("source", "1080p"))
        # Without listed renditions the original playlist is cast as before.
        self.plays.clear()
        self.session(None)
        self.assertEqual(upstream_of(self.plays[0]["url"]), MASTER)

    def test_a_refused_4k_rendition_retries_at_1080p(self):
        uhd = dict(url="https://cdn.example/show/2160.m3u8", height=2160)
        calls = []
        original = cast.play.side_effect
        def play(url, *args, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("電視無法播放此串流，請檢查來源／影片格式")
            return original(url, *args, **kwargs)
        cast.play.side_effect = play
        session = self.session(dict(best=uhd, lower=LOWER, full_hd=BEST))
        self.assertEqual([upstream_of(u) for u in calls], [uhd["url"], BEST["url"]])
        self.assertEqual(session.get()["quality_label"], "1080p")

    def test_slow_source_switches_to_a_lower_rendition_at_the_same_position(self):
        session = self.session(dict(best=BEST, lower=LOWER, full_hd=None))
        session.snapshot["current_time"] = 1234.0
        session.downshift("upstream")
        last = self.plays[-1]
        self.assertEqual((upstream_of(last["url"]), last["position"]), (LOWER["url"], 1234.0))
        self.assertNotEqual(last["marker"], self.plays[0]["marker"])
        read_ahead.prime.assert_called_once()
        self.assertEqual(read_ahead.prime.call_args.args[:3], (TV, LOWER["url"], 1234.0))
        snapshot = session.get()
        self.assertEqual((snapshot["quality"], snapshot["quality_label"], snapshot["quality_reason"]), ("720p", "720p", "upstream"))
        self.assertIn("720p", snapshot["warning"])

    def test_slow_source_without_a_lower_rendition_only_explains(self):
        session = self.session(dict(best=BEST, lower=None, full_hd=None))
        session.downshift("upstream")
        self.assertEqual(len(self.plays), 1)  # No reload: converting would not download less.
        self.assertIn("離線下載", session.get()["warning"])
        self.assertEqual(session.get()["quality"], "source")

    def test_receiver_bottleneck_converts_to_720p_when_no_rendition_exists(self):
        session = self.session(dict(best=BEST, lower=None, full_hd=None))
        def convert(url, deadline, strict=False):
            self.assertTrue(strict)
            return url + "&nesthub=1"
        with patch.object(hls_proxy, "nesthub_media_url", side_effect=convert):
            session.downshift("receiver")
        last = self.plays[-1]["url"]
        self.assertIn("&nesthub=1", last)
        self.assertIn("&web=1", last)
        self.assertEqual(read_ahead.prime.call_args.kwargs, dict(convert=True, web=True))
        self.assertEqual(session.get()["quality_label"], "720p（電腦轉檔）")

    def test_restore_returns_to_the_highest_rendition_and_stops_deciding(self):
        session = self.session(dict(best=BEST, lower=LOWER, full_hd=None))
        session.downshift("receiver")
        session.handle("quality", {})
        self.assertEqual(upstream_of(self.plays[-1]["url"]), BEST["url"])
        self.assertEqual((session.get()["quality"], session.get()["quality_label"]), ("source", "1080p"))
        self.assertTrue(session.governor.done)

    def test_tick_reports_read_ahead_and_acts_on_a_verdict(self):
        session = self.session(dict(best=BEST, lower=LOWER, full_hd=None))
        content = session.get()["content_id"]
        session.governor.observe = Mock(return_value="upstream")
        session.downshift = Mock()
        with patch.object(cast, "status", return_value=playing(content)), \
                patch.object(read_ahead, "status", return_value=dict(lead_seconds=95.4, ratio=2.0, full=False, error="")) as status:
            session.tick()
        self.assertEqual(status.call_args.args[0], TV)
        self.assertEqual(session.get()["buffer_ahead"], 95)
        session.downshift.assert_called_once_with("upstream")


if __name__ == "__main__":
    unittest.main()
