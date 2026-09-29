import subprocess
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import imageio_ffmpeg
from starlette.requests import Request

from backend import cast, hls_proxy, nesthub, seg_cache


ORIGIN = "http://192.168.1.10:6970"
MASTER = "https://surrit.com/master.m3u8"
MEDIA = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\npart.ts\n#EXT-X-ENDLIST\n"


class NestHubTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(hls_proxy, "assert_hls_url", side_effect=lambda url: url)
        p.start()
        self.addCleanup(p.stop)

    def select(self, master, child=MEDIA):
        def read(url, deadline):
            return (master if url == MASTER else child), url
        with patch.object(hls_proxy, "_read_playlist", side_effect=read):
            return hls_proxy.nesthub_media_url(hls_proxy.proxied_media(MASTER, ORIGIN), time.monotonic() + 30)

    def test_existing_720p_variant_avoids_conversion_and_preserves_audio(self):
        text = '#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080\nhigh.m3u8\n#EXT-X-STREAM-INF:RESOLUTION=1280x720\nmedium.m3u8\n#EXT-X-STREAM-INF:RESOLUTION=640x360\nlow.m3u8\n'
        result = parse_qs(urlparse(self.select(text)).query)
        self.assertEqual(result['u'], ['https://surrit.com/medium.m3u8'])
        self.assertNotIn('nesthub', result)
        # A video-only rendition must not lose its separate audio track.
        separate = self.select('#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1280x720,AUDIO="audio"\nvideo.m3u8\n')
        self.assertEqual(separate, hls_proxy.proxied_media(MASTER, ORIGIN))

    def test_high_resolution_source_selects_smallest_and_marks_only_cast_proxy(self):
        text = '#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=3840x2160\n4k.m3u8\n#EXT-X-STREAM-INF:RESOLUTION=1920x800\nhigh.m3u8\n'
        result = parse_qs(urlparse(self.select(text)).query)
        self.assertEqual(result['u'], ['https://surrit.com/high.m3u8'])
        self.assertEqual(result['nesthub'], ['1'])
        self.assertNotIn('nesthub', hls_proxy.proxied_media(MASTER, ORIGIN))

    def test_conversion_preserves_duration_discontinuities_and_nested_proxy_urls(self):
        original = MEDIA.replace('#EXTINF:4,', '#EXT-X-DISCONTINUITY\n#EXTINF:4,')
        rewritten = hls_proxy.rewrite_playlist(original, MASTER, ORIGIN, nesthub=True)
        self.assertIn('#EXTINF:4,', rewritten)
        self.assertIn('#EXT-X-DISCONTINUITY', rewritten)
        self.assertIn('#EXT-X-ENDLIST', rewritten)
        self.assertIn(hls_proxy.proxied_media('https://surrit.com/part.ts', ORIGIN, nesthub=True), rewritten)

    def test_unsupported_segment_formats_fail_before_receiver_launch(self):
        for tag in ['#EXT-X-KEY:METHOD=AES-128,URI="key"', '#EXT-X-MAP:URI="init.mp4"', '#EXT-X-BYTERANGE:100@0']:
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, '分段格式'):
                self.select('#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080\nhigh.m3u8\n', MEDIA.replace('#EXTINF:', tag + '\n#EXTINF:'))

    def test_unlabelled_encrypted_or_fmp4_sources_keep_native_playback(self):
        for tag in ['#EXT-X-KEY:METHOD=AES-128,URI="key"', '#EXT-X-MAP:URI="init.mp4"']:
            self.assertEqual(self.select(MEDIA.replace('#EXTINF:', tag + '\n#EXTINF:')), hls_proxy.proxied_media(MASTER, ORIGIN))

    def test_conversion_retains_url_validation(self):
        from backend import security
        with patch.object(hls_proxy, 'assert_hls_url', side_effect=security.assert_hls_url):
            with self.assertRaises(security.UnsafeURL):
                hls_proxy.rewrite_playlist(MEDIA.replace('part.ts', 'http://127.0.0.1/private.ts'), MASTER, nesthub=True)

    def test_only_nest_hub_hls_uses_compatibility_selection(self):
        for model, mime, selected in [('Google Nest Hub', 'application/vnd.apple.mpegurl', True),
                                      ('Chromecast', 'application/vnd.apple.mpegurl', False),
                                      ('Google Nest Hub', 'video/mp4', False)]:
            device = Mock(cast_info=SimpleNamespace(model_name=model))
            with self.subTest(model=model, mime=mime), patch.object(cast, '_active', {}), patch.object(cast, '_cast', return_value=('tv', device)), patch.object(cast, '_confirm', return_value={}), patch.object(hls_proxy, 'nesthub_media_url', return_value='http://lan/media?nesthub=1') as prepare:
                cast.play('http://lan/media', mime, 'Test', uuid='tv')
                self.assertEqual(prepare.call_count, int(selected))

    def test_converted_ranges_and_head_describe_converted_bytes(self):
        for method in ('GET', 'HEAD'):
            req = Request({'type': 'http', 'method': method, 'path': '/api/hls', 'query_string': b'', 'headers': [(b'range', b'bytes=2-5')]})
            with patch.object(nesthub, 'transcode', return_value=b'0123456789'):
                response = hls_proxy._nesthub_segment(req, 'url', b'original')
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.headers['content-range'], 'bytes 2-5/10')
            self.assertEqual(response.headers['content-length'], '4')
            self.assertEqual(response.body, b'2345' if method == 'GET' else b'')

    def test_conversion_timeout_releases_worker_and_does_not_cache_failure(self):
        with patch.object(seg_cache, 'get', return_value=None), patch.object(seg_cache, 'put') as cache, patch.object(nesthub, '_workers') as workers, patch.object(imageio_ffmpeg, 'get_ffmpeg_exe', return_value='ffmpeg'), patch.object(nesthub.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffmpeg', 20)):
            with self.assertRaisesRegex(TimeoutError, '轉換逾時'):
                nesthub.transcode('url', b'segment')
            workers.release.assert_called_once()
            cache.assert_not_called()

    def test_real_segment_conversion_keeps_source_time_and_decodes_at_720p(self):
        binary = imageio_ffmpeg.get_ffmpeg_exe()
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        source = subprocess.run([binary, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=size=1920x1080:rate=25',
                                 '-t', '1', '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-output_ts_offset', '37', '-f', 'mpegts', 'pipe:1'],
                                capture_output=True, check=True, timeout=20, creationflags=flags).stdout
        with patch.object(seg_cache, 'get', return_value=None), patch.object(seg_cache, 'put'):
            converted = nesthub.transcode('fixture', source)
        decoded = subprocess.run([binary, '-hide_banner', '-f', 'mpegts', '-i', 'pipe:0', '-f', 'null', '-'], input=converted, capture_output=True, timeout=20, creationflags=flags)
        description = decoded.stderr.decode('utf-8', 'replace')
        self.assertEqual(decoded.returncode, 0, description)
        self.assertIn('1280x720', description)
        self.assertRegex(description, r'start: 38\.\d+')  # TS mux adds 1.4 s to source offset.
        self.assertIn('30 fps', description)


if __name__ == '__main__':
    unittest.main()
