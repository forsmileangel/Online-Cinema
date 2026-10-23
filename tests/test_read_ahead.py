import time
import unittest
from collections import OrderedDict
from unittest.mock import Mock, patch

from starlette.requests import Request

from backend import hls_proxy, read_ahead, security, seg_cache

BASE = "https://cdn.example/show/"
SEGMENTS = [f"{BASE}{i:03d}.ts" for i in range(100)]


TV = "192.168.1.13"


def request(client="192.168.1.20", headers=None):
    return Request(dict(type="http", method="GET", path="/api/hls", scheme="http", query_string=b"", client=(client, 40000),
                        server=("192.168.1.2", 6969), headers=[(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]))


class ReadAheadTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("_cache", OrderedDict()), ("_bytes", 0), ("_playlists", OrderedDict()), ("_durations", {}),
                            ("_position", {}), ("_inflight", {}), ("_cooldown", {})):
            p = patch.object(seg_cache, name, value); p.start(); self.addCleanup(p.stop)
        for name, value in (("_windows", {}), ("_receivers", {TV}), ("_start", Mock())):
            p = patch.object(read_ahead, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(security, "_assert_not_private"); p.start(); self.addCleanup(p.stop)
        security.remember_media_host("cdn.example", source="mmov")
        seg_cache.remember(SEGMENTS, [6.0] * len(SEGMENTS))

    def drain(self, window, limit=200):
        """Run the planner the way the workers do, caching each planned segment."""
        fetched = []
        for _ in range(limit):
            job = read_ahead._plan(window)
            if not job:
                break
            fetched.append(job[0])
            seg_cache.put(job[0], b"x" * 1000)
        return fetched

    def test_playlist_seconds_are_collected_and_urls_carry_no_marker(self):
        text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXTINF:4.5,\na.ts\n#EXTINF:5,\nb.ts\n#EXT-X-ENDLIST\n'
        segments, seconds = [], []
        out = hls_proxy.rewrite_playlist(text, BASE + "index.m3u8", "http://192.168.1.2:6969", segments=segments,
                                         durations=seconds)
        self.assertEqual(seconds, [4.5, 5.0])
        self.assertEqual(segments, [BASE + "a.ts", BASE + "b.ts"])
        self.assertEqual(len([line for line in out.splitlines() if "/api/hls" in line]), 3)
        # A TV's playlist must stay as short as it was: an extra "&rx=…" on every segment (+30%)
        # made an LG stop asking for segments.
        self.assertNotIn("rx=", out)

    def test_window_fills_nearest_first_up_to_three_minutes(self):
        read_ahead.touch(SEGMENTS[10], "s1-1")
        window = read_ahead._windows["s1-1"]
        self.assertEqual(window.anchor, 10)
        fetched = self.drain(window)
        self.assertEqual(fetched, SEGMENTS[11:41])  # 30 segments x 6 s = 180 s
        state = read_ahead.status("s1-1")
        self.assertEqual((state["lead_seconds"], state["full"]), (180.0, True))
        # The receiver moved on: the window follows it.
        read_ahead.touch(SEGMENTS[20], "s1-1")
        self.assertEqual(self.drain(window), SEGMENTS[41:51])

    def test_window_respects_bytes_cooldown_downloads_and_failures(self):
        read_ahead.touch(SEGMENTS[0], "s1-1")
        window = read_ahead._windows["s1-1"]
        with patch.object(read_ahead, "WINDOW_BYTES", 3500):
            self.assertEqual(len(self.drain(window)), 4)
        read_ahead.touch(SEGMENTS[50], "s1-1")
        seg_cache._inflight[SEGMENTS[51]] = Mock()  # The receiver is already downloading it.
        window.failed[SEGMENTS[52]] = time.monotonic() + 60
        self.assertEqual(read_ahead._plan(window)[0], SEGMENTS[53])
        seg_cache.pause(SEGMENTS[53])
        self.assertIsNone(read_ahead._plan(window))

    def test_fetch_stores_records_speed_converts_and_backs_off(self):
        read_ahead.touch(SEGMENTS[0], "s1-1", convert=True, web=True)
        window = read_ahead._windows["s1-1"]
        with patch.object(hls_proxy, "_fetch_segment", return_value=b"segment") as fetch, \
                patch("backend.nesthub.transcode") as transcode:
            read_ahead._fetch(window, SEGMENTS[1], 6.0)
        fetch.assert_called_once()
        self.assertEqual(seg_cache.get(SEGMENTS[1]), b"segment")
        transcode.assert_called_once_with(SEGMENTS[1], b"segment", web=True)
        self.assertEqual(len(window.fetches), 1)
        with patch.object(hls_proxy, "_fetch_segment", side_effect=TimeoutError("slow")):
            read_ahead._fetch(window, SEGMENTS[2], 6.0)
        self.assertGreater(window.failed[SEGMENTS[2]], time.monotonic())
        self.assertEqual(window.error, "slow")

    def test_speed_ratio_counts_only_time_spent_downloading(self):
        now = 1000.0
        # Two overlapping 6-second segments over 4 s, then an idle gap, then one more over 2 s.
        fetches = [(990.0, 993.0, 6.0), (991.0, 994.0, 6.0), (996.0, 998.0, 6.0)]
        self.assertAlmostEqual(read_ahead._ratio(fetches, now), 18.0 / 6.0)
        self.assertIsNone(read_ahead._ratio([(800.0, 801.0, 6.0)], now))

    def test_receiver_requests_touch_the_window_and_skip_browser_prefetch(self):
        seg_cache.put(SEGMENTS[5], b"cached")
        with patch.object(read_ahead, "touch") as touch, patch.object(seg_cache, "enqueue_next") as browser:
            hls_proxy.serve_media(request(TV), SEGMENTS[5])
        touch.assert_called_once_with(SEGMENTS[5], TV, convert=False, web=False)
        browser.assert_not_called()
        with patch.object(read_ahead, "touch") as touch, patch.object(seg_cache, "enqueue_next") as browser:
            hls_proxy.serve_media(request(), SEGMENTS[5])
        touch.assert_not_called()
        browser.assert_called_once()

    def test_prime_starts_at_the_playback_position(self):
        text = "#EXTM3U\n" + "".join(f"#EXTINF:6,\n{i:03d}.ts\n" for i in range(100)) + "#EXT-X-ENDLIST\n"
        with patch.object(hls_proxy, "_read_playlist", return_value=(text, BASE + "index.m3u8")):
            read_ahead.prime("s1-3", BASE + "index.m3u8", 125.0, wait=0)
        window = read_ahead._windows["s1-3"]
        self.assertEqual(window.anchor, 19)  # 125 s falls in segment 20 (120-126 s).
        self.assertEqual(read_ahead._plan(window)[0], SEGMENTS[20])

    def test_renditions_pick_the_highest_and_a_720p_fallback(self):
        master = ('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2"\n720.m3u8\n'
                  '#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=3840x2160,CODECS="avc1.640033,mp4a.40.2"\n2160.m3u8\n'
                  '#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"\n1080.m3u8\n'
                  '#EXT-X-STREAM-INF:BANDWIDTH=9000000,RESOLUTION=3840x2160,CODECS="hvc1.1.6.L150,mp4a.40.2"\nhevc.m3u8\n')
        with patch.object(hls_proxy, "_read_playlist", return_value=(master, BASE + "master.m3u8")):
            found = hls_proxy.renditions(BASE + "master.m3u8", time.monotonic() + 5)
        self.assertEqual(found["best"], dict(url=BASE + "2160.m3u8", height=2160))
        self.assertEqual(found["lower"], dict(url=BASE + "720.m3u8", height=720))
        self.assertEqual(found["full_hd"], dict(url=BASE + "1080.m3u8", height=1080))
        media = "#EXTM3U\n#EXTINF:6,\n000.ts\n#EXT-X-ENDLIST\n"
        with patch.object(hls_proxy, "_read_playlist", return_value=(media, BASE + "index.m3u8")):
            self.assertIsNone(hls_proxy.renditions(BASE + "index.m3u8", time.monotonic() + 5))

    def test_receivers_are_known_by_address_only(self):
        read_ahead.add_receiver("192.168.1.7")
        read_ahead.add_receiver("")
        self.assertTrue(read_ahead.is_receiver("192.168.1.7") and read_ahead.is_receiver(TV))
        self.assertFalse(read_ahead.is_receiver("192.168.1.20") or read_ahead.is_receiver("") or read_ahead.is_receiver(None))
        master = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"\n1080.m3u8\n'
        url = hls_proxy.proxied_media(BASE + "master.m3u8", "http://192.168.1.2:6969")
        with patch.object(hls_proxy, "_read_playlist", return_value=(master, BASE + "master.m3u8")):
            chosen = hls_proxy.dlna_media_url(url, time.monotonic() + 5)
        self.assertEqual(chosen, hls_proxy.proxied_media(BASE + "1080.m3u8", "http://192.168.1.2:6969"))


if __name__ == "__main__":
    unittest.main()
