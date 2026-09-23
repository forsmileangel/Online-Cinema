import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend import hls_proxy, security
from backend.sites import chinaq

FIRST = 'https://v3.qqqrst.com/202308/22/XgNv3tZwr52/video/index.m3u8'
SECOND = 'https://v6.1080pzy.co/20220806/2Ksub6f4/index.m3u8'


class ChinaqPlaybackTests(unittest.TestCase):
    def setUp(self):
        security._extra_media_hosts.clear()

    def test_observed_cdn_roots_and_subdomains_have_exact_boundaries(self):
        with patch.object(security, '_assert_not_private'):
            for host in ('qqqrst.com', 'v3.qqqrst.com', '1080pzy.co', 'v6.1080pzy.co'):
                security.assert_hls_url('https://' + host + '/index.m3u8')
            for url in ('https://evilqqqrst.com/x.m3u8', 'https://qqqrst.com.evil.test/x.m3u8',
                        'https://evil1080pzy.co/x.m3u8', 'https://1080pzy.co.evil.test/x.m3u8',
                        'http://v3.qqqrst.com/x.m3u8', 'https://v3.qqqrst.com:999/x.m3u8'):
                with self.subTest(url=url), self.assertRaises(security.UnsafeURL):
                    security.assert_hls_url(url)
        with patch.object(security.socket, 'getaddrinfo', return_value=[(0,0,0,'',('127.0.0.1',443))]):
            with self.assertRaises(security.UnsafeURL):
                security.assert_hls_url(FIRST)

    def test_empty_first_candidate_uses_valid_second_playlist(self):
        def response(url, **kwargs):
            return SimpleNamespace(url=url, content=b'' if url == FIRST else b'#EXTM3U\n#EXTINF:5,\nseg.ts\n', close=lambda: None)
        with patch.object(security, '_assert_not_private'), patch.object(hls_proxy.http_client, 'fetch_bytes', side_effect=response) as fetch:
            self.assertEqual(chinaq._pick_stream([FIRST, SECOND]), hls_proxy.proxied_media(SECOND))
        self.assertEqual(fetch.call_count, 2)

    def test_invalid_child_playlist_uses_second_candidate(self):
        def response(url, **kwargs):
            raw = (b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nchild.m3u8\n' if url == FIRST
                   else b'#EXTM3U\nhttps://v3.qqqrst.com:999/seg.ts\n' if url.endswith('child.m3u8')
                   else b'#EXTM3U\n#EXTINF:5,\nseg.ts\n')
            return SimpleNamespace(url=url, content=raw, close=lambda: None)
        with patch.object(security, '_assert_not_private'), patch.object(hls_proxy.http_client, 'fetch_bytes', side_effect=response) as fetch:
            self.assertEqual(chinaq._pick_stream([FIRST, SECOND]), hls_proxy.proxied_media(SECOND))
        self.assertEqual(fetch.call_count, 3)

    def test_source_refusal_or_rate_limit_stops_fallback(self):
        for status in (403, 429, 503):
            error = security.SiteBusy('ChinaQ', status)
            with self.subTest(status=status), patch.object(security, '_assert_not_private'), patch.object(chinaq, 'dlna_media_url', side_effect=error) as validate:
                with self.assertRaises(security.SiteBusy) as caught:
                    chinaq._pick_stream([FIRST, SECOND])
                self.assertIs(caught.exception, error)
                self.assertEqual(validate.call_count, 1)

    def test_candidate_limit_and_shared_deadline(self):
        with patch.object(security, '_assert_not_private'), patch.object(chinaq, 'dlna_media_url', side_effect=security.UnsafeURL('invalid playlist')) as validate:
            with self.assertRaisesRegex(security.UnsafeURL, 'invalid playlist'):
                chinaq._pick_stream([FIRST] * 8)
            self.assertEqual(validate.call_count, 3)
            self.assertEqual(len({call.args[1] for call in validate.call_args_list}), 1)
        with patch.object(security, '_assert_not_private'), patch.object(chinaq.time, 'monotonic', side_effect=[0, 0, 31]), patch.object(chinaq, 'dlna_media_url', side_effect=TimeoutError) as validate:
            with self.assertRaises(TimeoutError):
                chinaq._pick_stream([FIRST, SECOND])
            self.assertEqual(validate.call_count, 1)

if __name__ == '__main__':
    unittest.main()
