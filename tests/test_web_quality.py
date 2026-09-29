import subprocess
import socket
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import imageio_ffmpeg
from fastapi import HTTPException
from starlette.requests import Request

from backend import hls_proxy, main, nesthub, seg_cache
from backend.security import SiteBusy, UnsafeURL

MASTER = 'https://dytt-tvs.com/master.m3u8'
MEDIA = '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\npart.ts\n#EXT-X-ENDLIST\n'
HIGH = '#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080\nhigh.m3u8\n'


class WebQualityTests(unittest.TestCase):
    def setUp(self):
        dns = patch('backend.security.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])
        dns.start()
        self.addCleanup(dns.stop)

    def prepare(self, master, child=MEDIA):
        def read(url, deadline):
            self.assertGreater(deadline, time.monotonic())
            return (master if url == MASTER else child), url
        with patch.object(hls_proxy, '_read_playlist', side_effect=read):
            return main.web_720p(MASTER)['url']

    def test_native_720p_is_selected_without_conversion(self):
        selected = self.prepare(HIGH + '#EXT-X-STREAM-INF:RESOLUTION=1280x720\nmedium.m3u8\n')
        self.assertEqual(parse_qs(urlparse(selected).query), {'u': ['https://dytt-tvs.com/medium.m3u8']})

    def test_single_1080p_prepares_real_conversion_and_retains_timeline(self):
        selected = self.prepare(HIGH)
        query = parse_qs(urlparse(selected).query)
        self.assertEqual(query, {'u': ['https://dytt-tvs.com/high.m3u8'], 'nesthub': ['1'], 'web': ['1']})
        result = hls_proxy.rewrite_playlist(MEDIA, query['u'][0], nesthub=True, web=True)
        self.assertIn('#EXTINF:4,', result)
        self.assertIn('&nesthub=1&web=1', result)
        self.assertIn('#EXT-X-ENDLIST', result)

    def test_unsupported_formats_do_not_silently_claim_720p(self):
        cases = [(HIGH.replace('1920x1080', '1920x1080,AUDIO="audio"'), MEDIA),
                 (HIGH, MEDIA.replace('#EXTINF:', '#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:')),
                 (MEDIA.replace('#EXTINF:', '#EXT-X-MAP:URI="init.mp4"\n#EXTINF:'), MEDIA),
                 (HIGH, HIGH)]
        for master, child in cases:
            with self.subTest(master=master, child=child), self.assertRaises(HTTPException) as err:
                self.prepare(master, child)
            self.assertEqual(err.exception.status_code, 422)
        with patch.object(hls_proxy, '_read_playlist') as read, self.assertRaises(HTTPException) as err:
            main.web_720p('https://dytt-tvs.com/film.mp4')
        self.assertEqual(err.exception.status_code, 422)
        read.assert_not_called()

    def test_unsafe_hosts_and_ports_fail_before_upstream(self):
        for url in ['http://dytt-tvs.com/master.m3u8', 'https://127.0.0.1/master.m3u8',
                    'https://dytt-tvs.com:999/master.m3u8', 'https://dytt-tvs.com.evil.invalid/master.m3u8']:
            with self.subTest(url=url), patch.object(hls_proxy, '_read_playlist') as read, self.assertRaises(HTTPException) as err:
                main.web_720p(url)
            self.assertEqual(err.exception.status_code, 400)
            read.assert_not_called()
        with self.assertRaises(UnsafeURL):
            hls_proxy.rewrite_playlist(MEDIA.replace('part.ts', 'https://127.0.0.1/part.ts'), MASTER, nesthub=True)

    def test_source_rejection_stops_and_keeps_retry_information(self):
        busy = SiteBusy('Gimy', status_code=429, retry_after=90)
        with patch.object(hls_proxy, '_read_playlist', side_effect=busy) as read, self.assertRaises(HTTPException) as err:
            main.web_720p(MASTER)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(err.exception.status_code, 429)
        self.assertEqual(err.exception.headers['Retry-After'], '90')

    def test_media_proxy_converts_cached_segments_and_supports_head_ranges(self):
        for method in ['GET', 'HEAD']:
            req = Request({'type': 'http', 'method': method, 'path': '/api/hls',
                           'query_string': b'nesthub=1&web=1', 'headers': [(b'range', b'bytes=2-5')]})
            with patch.object(seg_cache, 'get', return_value=b'original'), patch.object(seg_cache, 'start_workers'), \
                 patch.object(seg_cache, 'enqueue_next'), patch.object(nesthub, 'transcode', return_value=b'0123456789') as convert:
                result = hls_proxy.serve_media(req, 'https://dytt-tvs.com/part.ts')
            convert.assert_called_once_with('https://dytt-tvs.com/part.ts', b'original', web=True)
            self.assertEqual(result.status_code, 206)
            self.assertEqual(result.headers['content-range'], 'bytes 2-5/10')
            self.assertEqual(result.headers['content-length'], '4')
            self.assertEqual(result.body, b'2345' if method == 'GET' else b'')

    def test_real_720p_conversion_preserves_audio_and_timestamps(self):
        binary = imageio_ffmpeg.get_ffmpeg_exe()
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        source = subprocess.run([binary, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=size=1920x1080:rate=25',
                                 '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '1',
                                 '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-c:a', 'aac',
                                 '-output_ts_offset', '37', '-f', 'mpegts', 'pipe:1'],
                                capture_output=True, check=True, timeout=20, creationflags=flags).stdout
        with patch.object(seg_cache, 'get', return_value=None), patch.object(seg_cache, 'put'):
            converted = nesthub.transcode('web-quality-fixture', source, web=True)
        decoded = subprocess.run([binary, '-hide_banner', '-f', 'mpegts', '-i', 'pipe:0', '-f', 'null', '-'],
                                 input=converted, capture_output=True, timeout=20, creationflags=flags)
        description = decoded.stderr.decode('utf-8', 'replace')
        self.assertEqual(decoded.returncode, 0, description)
        self.assertIn('1280x720', description)
        self.assertIn('Audio: aac', description)
        self.assertIn('25 fps', description)
        self.assertRegex(description, r'start: 38\.\d+')


if __name__ == '__main__':
    unittest.main()
