import asyncio
import unittest
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from starlette.requests import Request

from backend import hls_proxy, http_client, main, security
from backend.models import CastPlayIn


def request(method="GET", headers=None):
    return Request({"type": "http", "method": method, "path": "/api/hls", "scheme": "http", "query_string": b"",
                    "server": ("192.168.1.10", 6969), "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


class MediaProxyTests(unittest.TestCase):
    def setUp(self):
        for obj, name, kwargs in [
            (hls_proxy, "assert_hls_url", {"side_effect": lambda u: u}),
            (hls_proxy.seg_cache, "start_workers", {}),
        ]:
            p = patch.object(obj, name, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def test_image_proxy_uses_image_bytes_not_upstream_content_type(self):
        url = "https://picbf.com/poster"
        jpeg = b"\xff\xd8\xff" + b"poster-bytes"
        response = Mock(content=jpeg, headers={"content-type": "text/html"})
        with patch.object(main, "assert_image_url", side_effect=lambda value: value), patch.object(
            http_client, "fetch_bytes", return_value=response
        ):
            image = main.image_proxy(url)
        self.assertEqual(image.media_type, "image/jpeg")
        self.assertEqual(image.body, jpeg)
        response.close.assert_called_once()

        html_response = Mock(content=b"<!doctype html><title>blocked</title>", headers={"content-type": "image/jpeg"})
        with patch.object(main, "assert_image_url", side_effect=lambda value: value), patch.object(
            http_client, "fetch_bytes", return_value=html_response
        ):
            with self.assertRaises(HTTPException) as raised:
                main.image_proxy(url)
        self.assertEqual(raised.exception.status_code, 502)
        html_response.close.assert_called_once()

    def test_all_sources_use_the_same_proxy_with_correct_referer(self):
        cases = [("hongguo", "s2.bfllvip.com", "https://www.hongguoapp.cn/"),
                 ("chinaq", "ukzy.ukubf3.com", "https://chinaq.fun/"),
                 ("gimy", "vv.jisuzyv.com", "https://gimyai.tw/")]
        for source, host, referer in cases:
            with self.subTest(source=source):
                url = f"https://{host}/clip/master.m3u8"
                upstream = Mock(content=b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.key"\n#EXT-X-MAP:URI="init.mp4"\nsub/video.m3u8\n', headers={}, status_code=200, url=url)
                with patch.object(http_client, "fetch_bytes", return_value=upstream) as fetch:
                    result = hls_proxy.serve_media(request(), url)
                self.assertEqual(fetch.call_args.kwargs["referer"], referer)
                text = result.body.decode()
                self.assertEqual(text.count("http://192.168.1.10:6969/api/hls?u="), 3)
                self.assertIn(quote(f"https://{host}/clip/sub/video.m3u8", safe=""), text)
                self.assertEqual(result.media_type, "application/vnd.apple.mpegurl")

    def test_mp4_large_range_streams_without_reading_or_caching_entire_file(self):
        class Upstream:
            headers = {"Content-Type": "video/mp4", "Content-Range": "bytes 10-40000009/90000000", "Content-Length": "40000000", "Accept-Ranges": "bytes"}
            status_code = 206
            closed = False
            @property
            def content(self):
                raise AssertionError("MP4 must not be buffered")
            def iter_content(self, chunk_size):
                yield b"part1"
                yield b"part2"
            def close(self):
                self.closed = True
        upstream = Upstream()
        with patch.object(http_client, "fetch_bytes", return_value=upstream) as fetch, patch.object(hls_proxy.seg_cache, "put") as cache:
            response = hls_proxy.serve_media(request(headers={"Range": "bytes=10-40000009"}), "https://phncdn.com/video.mp4")
            self.assertIsInstance(response, StreamingResponse)
            async def consume():
                return b"".join([part async for part in response.body_iterator])
            self.assertEqual(asyncio.run(consume()), b"part1part2")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-range"], "bytes 10-40000009/90000000")
        self.assertEqual(fetch.call_args.kwargs["range_header"], "bytes=10-40000009")
        self.assertTrue(upstream.closed)
        cache.assert_not_called()

    def test_head_does_not_read_a_body(self):
        upstream = Mock(headers={"Content-Length": "90000000", "Accept-Ranges": "bytes"}, status_code=200)
        with patch.object(http_client, "fetch_bytes", return_value=upstream) as fetch:
            response = hls_proxy.serve_media(request("HEAD"), "https://phncdn.com/video.mp4")
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers["content-length"], "90000000")
        self.assertEqual(fetch.call_args.kwargs["method"], "HEAD")
        upstream.iter_content.assert_not_called()
        upstream.close.assert_called_once()

    def test_playlist_head_matches_rewritten_get_length_without_sending_body(self):
        url = "https://surrit.com/clip/video.m3u8"
        upstream = Mock(content=b'#EXTM3U\n#EXTINF:4,\nvideo0.jpeg\n#EXT-X-ENDLIST\n', headers={"Content-Length": "56", "Accept-Ranges": "bytes"}, status_code=200, url=url)
        with patch.object(http_client, "fetch_bytes", return_value=upstream) as fetch:
            head = hls_proxy.serve_media(request("HEAD", {"Range": "bytes=0-1"}), url)
            get = hls_proxy.serve_media(request(), url)
        self.assertEqual(head.body, b"")
        self.assertGreater(len(get.body), 56)
        self.assertEqual(head.headers["content-length"], str(len(get.body)))
        self.assertEqual(head.headers["content-type"], get.headers["content-type"])
        self.assertNotIn("accept-ranges", head.headers)
        self.assertEqual(fetch.call_args_list[0].kwargs["method"], "GET")
        self.assertIsNone(fetch.call_args_list[0].kwargs["range_header"])

    def test_transient_upstream_failure_keeps_existing_retry_behavior(self):
        upstream = Mock(content=b"#EXTM3U\n", headers={}, status_code=200, url="https://surrit.com/test/master.m3u8")
        with patch.object(http_client, "fetch_bytes", side_effect=[OSError("temporary connection failure"), upstream]) as fetch:
            response = hls_proxy.serve_media(request(), upstream.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fetch.call_count, 2)

    def test_bad_range_is_rejected_and_upstream_416_preserved(self):
        with patch.object(http_client, "fetch_bytes") as fetch:
            response = hls_proxy.serve_media(request(headers={"Range": "bytes=0-1,5-9"}), "https://phncdn.com/video.mp4")
        self.assertEqual(response.status_code, 416)
        fetch.assert_not_called()
        upstream = Mock(headers={"Content-Range": "bytes */900"}, status_code=416)
        with patch.object(http_client, "fetch_bytes", return_value=upstream):
            response = hls_proxy.serve_media(request(headers={"Range": "bytes=999-"}), "https://phncdn.com/video.mp4")
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers["content-range"], "bytes */900")

    def test_segment_cache_does_not_serve_full_segment_for_a_range(self):
        upstream = Mock(headers={"Content-Range": "bytes 0-1/100"}, status_code=206)
        with patch.object(http_client, "fetch_bytes", return_value=upstream), patch.object(hls_proxy.seg_cache, "get") as cache:
            response = hls_proxy.serve_media(request(headers={"Range": "bytes=0-1"}), "https://surrit.com/seg.ts")
        self.assertEqual(response.status_code, 206)
        cache.assert_not_called()
        asyncio.run(response.background())

    def test_jpeg_named_hls_segment_keeps_media_mime_and_cache(self):
        with patch.object(hls_proxy.seg_cache, "get", return_value=b"\x47" * 188), patch.object(hls_proxy.seg_cache, "enqueue_next"):
            response = hls_proxy.serve_media(request(), "https://mushroomtrack.com/video1.jpeg")
        self.assertEqual(response.media_type, "video/mp2t")
        self.assertEqual(len(response.body), 188)

    def test_dlna_selects_highest_muxed_avc_variant_and_keeps_proxy_origin(self):
        playlist = b'''#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=4000000,CODECS="hvc1.1.6.L120,mp4a.40.2",RESOLUTION=3840x2160
hevc/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000000,CODECS="avc1.64001e,mp4a.40.2",RESOLUTION=640x360
360p/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.64001f,mp4a.40.2",RESOLUTION=1280x720
720p/video.m3u8?token=example
'''
        response = Mock(content=playlist, url="https://surrit.com/redirect/master.m3u8")
        with patch.object(http_client, "fetch_bytes", return_value=response):
            result = hls_proxy.dlna_media_url("http://192.168.1.10:6969/api/hls?u=" + quote("https://surrit.com/start/master.m3u8", safe=""), time.monotonic() + 30)
        self.assertEqual(result, "http://192.168.1.10:6969/api/hls?u=" + quote("https://surrit.com/redirect/720p/video.m3u8?token=example", safe=""))
        response.close.assert_called_once()

    def test_dlna_keeps_media_playlist_and_separate_audio_master_intact(self):
        playlists = [b'#EXTM3U\n#EXTINF:4,\npart.ts\n#EXT-X-ENDLIST\n',
                     b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",URI="audio.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=1,CODECS="avc1.64001f,mp4a.40.2",AUDIO="audio"\nvideo.m3u8\n']
        url = "http://192.168.1.10:6969/api/hls?u=" + quote("https://phncdn.com/master.m3u8", safe="")
        for playlist in playlists:
            with self.subTest(playlist=playlist), patch.object(http_client, "fetch_bytes", return_value=Mock(content=playlist, url="https://phncdn.com/master.m3u8")):
                self.assertEqual(hls_proxy.dlna_media_url(url, time.monotonic() + 30), url)

    def test_dlna_unlabelled_variants_use_bandwidth_and_source_request_headers(self):
        upstream = "https://s2.bfllvip.com/master.m3u8"
        response = Mock(content=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=10\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=20\nhigh.m3u8\n', url=upstream)
        with patch.object(http_client, "fetch_bytes", return_value=response) as fetch:
            result = hls_proxy.dlna_media_url("http://192.168.1.10:6969/api/hls?u=" + quote(upstream, safe=""), time.monotonic() + 30)
        self.assertIn(quote("https://s2.bfllvip.com/high.m3u8", safe=""), result)
        self.assertEqual(fetch.call_args.kwargs["referer"], "https://www.hongguoapp.cn/")
        self.assertEqual(fetch.call_args.kwargs["impersonate"], "chrome131")

    def test_dlna_variant_fetch_uses_remaining_startup_budget(self):
        url = "http://192.168.1.10:6969/api/hls?u=" + quote("https://surrit.com/master.m3u8", safe="")
        response = Mock(content=b'#EXTM3U\n', url="https://surrit.com/master.m3u8")
        with patch.object(hls_proxy.time, "monotonic", return_value=9), patch.object(http_client, "fetch_bytes", return_value=response) as fetch:
            hls_proxy.dlna_media_url(url, 10)
        self.assertEqual(fetch.call_args.kwargs["timeout"], 1)
        with patch.object(hls_proxy.time, "monotonic", return_value=10), patch.object(http_client, "fetch_bytes") as fetch:
            with self.assertRaises(TimeoutError):
                hls_proxy.dlna_media_url(url, 10)
        fetch.assert_not_called()

    def test_dlna_rejects_unsafe_variant_url(self):
        url = "http://192.168.1.10:6969/api/hls?u=" + quote("https://s2.bfllvip.com/master.m3u8", safe="")
        response = Mock(content=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=10\nhttp://127.0.0.1/private.m3u8\n', url="https://s2.bfllvip.com/master.m3u8")
        with patch.object(http_client, "fetch_bytes", return_value=response), patch.object(hls_proxy, "assert_hls_url", side_effect=security.assert_hls_url), patch.object(security, "_assert_not_private"):
            with self.assertRaises(hls_proxy.UnsafeURL):
                hls_proxy.dlna_media_url(url, time.monotonic() + 30)

    def test_media_cors_accepts_receiver_origin_and_range_preflight(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                next_call = AsyncMock(return_value=Response(status_code=502))
                response = asyncio.run(main.media_cors(request(method, {"Origin": "https://www.gstatic.com", "Access-Control-Request-Headers": "range"}), next_call))
                self.assertEqual(response.headers["access-control-allow-origin"], "*")
                self.assertIn("Range", response.headers["access-control-allow-headers"])
                if method == "OPTIONS":
                    self.assertEqual(response.status_code, 204)
                    next_call.assert_not_called()

    def test_cors_does_not_open_settings_api(self):
        req = request()
        req.scope["path"] = "/api/settings"
        response = asyncio.run(main.media_cors(req, AsyncMock(return_value=Response())))
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_cast_uses_decoded_upstream_path_for_mime_and_selected_route(self):
        url = "https://surrit.com/master.m3u8?label=.mp4"
        with patch.object(main.db, "get_setting", return_value="1"), patch.object(main, "assert_hls_url", side_effect=lambda u: u), patch.object(main.chromecast, "lan_media_origin", return_value="http://192.168.1.10:6969") as origin, patch.object(main.chromecast, "check_media_origin"), patch.object(main.chromecast, "play", return_value={}) as play:
            main.cast_play(CastPlayIn(url="/api/hls?u=" + quote(url, safe=""), uuid="lg"))
        origin.assert_called_once_with("lg")
        self.assertEqual(play.call_args.args[1], "application/vnd.apple.mpegurl")
        self.assertTrue(play.call_args.args[0].startswith("http://192.168.1.10:6969/api/hls?u="))


if __name__ == "__main__":
    unittest.main()
