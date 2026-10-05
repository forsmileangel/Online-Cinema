import threading
import unittest
from collections import OrderedDict
from queue import Queue
from unittest.mock import Mock, patch

from starlette.requests import Request

from backend import hls_proxy, http_client, lan, next_buffer, offline, seg_cache
from backend.security import SiteBusy

BASE = 'https://cdn.example.com/show/'
URL = BASE + 'seg2.ts'


def segment(body, headers=None):
    return Mock(status_code=200, headers=headers or {}, url=URL, iter_content=Mock(return_value=iter([body])))


def serve(url=URL):
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/hls', 'scheme': 'http',
                       'query_string': b'', 'server': ('127.0.0.1', 6970), 'headers': []})
    return hls_proxy.serve_media(request, url)


class SegmentCacheTests(unittest.TestCase):
    def setUp(self):
        for name, value in [('_cache', OrderedDict()), ('_bytes', 0), ('_inflight', {}), ('_playlists', OrderedDict()),
                            ('_position', {}), ('_cooldown', {}), ('_q', Queue(maxsize=24))]:
            state = patch.object(seg_cache, name, value)
            state.start()
            self.addCleanup(state.stop)
        for target, name, kwargs in [
            (hls_proxy, 'assert_hls_url', {'side_effect': lambda u: u}),
            (hls_proxy, 'hls_allowed_hosts', {'return_value': {'cdn.example.com'}}),
            (hls_proxy, '_media_context', {'return_value': ('https://source.example/', 'chrome131')}),
            (hls_proxy, 'touch_media_host', {}),
            (seg_cache, 'assert_hls_url', {'side_effect': lambda u: u}),
            (seg_cache, 'hls_allowed_hosts', {'return_value': {'cdn.example.com'}}),
            (seg_cache, 'start_workers', {}),
            (offline, 'cached_part', {'return_value': None}),
            (next_buffer, 'cached', {'return_value': None}),
            (lan, 'download_interfaces', {'return_value': [], 'create': True}),
        ]:
            stub = patch.object(target, name, **kwargs)
            stub.start()
            self.addCleanup(stub.stop)

    def queued(self):
        return list(seg_cache._q.queue)

    def test_budget_counts_bytes_and_keeps_large_hd_segments(self):
        with patch.object(seg_cache, '_MAX_BYTES', 10_000_000):
            for name in ('a', 'b', 'c'):
                seg_cache.put(BASE + name, b'x' * 4_500_000)
            self.assertIsNone(seg_cache.get(BASE + 'a'))
            self.assertIsNotNone(seg_cache.get(BASE + 'c'))
            self.assertLessEqual(seg_cache._bytes, 10_000_000)
        seg_cache.put(BASE + 'hd', b'x' * 6_000_000)
        self.assertIsNotNone(seg_cache.get(BASE + 'hd'))
        seg_cache.put(BASE + 'huge', b'x' * (seg_cache._MAX_EACH + 1))
        self.assertIsNone(seg_cache.get(BASE + 'huge'))

    def test_prefetch_follows_playlist_order_and_skips_ready_segments(self):
        segments = [BASE + f'seg{i}.ts' for i in range(1, 7)]
        seg_cache.remember(segments)
        seg_cache.put(segments[2], b'ready')
        with seg_cache.fetching(segments[3]) as owner:
            self.assertTrue(owner)
            seg_cache.enqueue_next(segments[1])
        self.assertEqual(self.queued(), [segments[4]])
        seg_cache.remember([BASE + 'only.ts'])
        seg_cache.enqueue_next(BASE + 'only.ts')
        self.assertEqual(self.queued(), [segments[4]])

    def test_jpeg_numbering_remains_the_fallback(self):
        seg_cache.enqueue_next('https://surrit.com/abc/1080p/video7.jpeg')
        self.assertEqual(self.queued(), [f'https://surrit.com/abc/1080p/video{n}.jpeg' for n in (8, 9, 10)])
        seg_cache.enqueue_next(BASE + 'unknown.ts')
        self.assertEqual(len(self.queued()), 3)

    def test_old_playlists_are_forgotten(self):
        for show in range(seg_cache._PLAYLISTS + 1):
            seg_cache.remember([f'{BASE}{show}/a.ts', f'{BASE}{show}/b.ts'])
        self.assertEqual(seg_cache._following(f'{BASE}0/a.ts', 3), [])
        self.assertEqual(seg_cache._following(f'{BASE}{seg_cache._PLAYLISTS}/a.ts', 3), [f'{BASE}{seg_cache._PLAYLISTS}/b.ts'])

    def test_prefetch_uses_source_request_context_and_streaming(self):
        with patch.object(http_client, 'fetch_bytes', return_value=segment(b'next')) as fetch:
            seg_cache._prefetch_one(URL)
        self.assertEqual(seg_cache.get(URL), b'next')
        kwargs = fetch.call_args.kwargs
        self.assertEqual((kwargs['referer'], kwargs['impersonate']), ('https://source.example/', 'chrome131'))
        self.assertEqual(kwargs['allowed_hosts'], {'cdn.example.com'})
        self.assertTrue(kwargs['stream'])
        self.assertLessEqual(kwargs['timeout'], 6)
        self.assertIs(kwargs['redirect_validator'], hls_proxy._playlist_media_url)

    def test_rejected_host_is_not_prefetched_again(self):
        segments = [BASE + f'seg{i}.ts' for i in range(1, 5)]
        seg_cache.remember(segments)
        with patch.object(http_client, 'fetch_bytes', side_effect=SiteBusy('CDN', 429, 60)) as fetch:
            seg_cache._prefetch_one(segments[1])
            seg_cache._prefetch_one(segments[2])
        fetch.assert_called_once()
        seg_cache.enqueue_next(segments[0])
        self.assertEqual(self.queued(), [])

    def test_browser_request_waits_for_the_same_download(self):
        started, release = threading.Event(), threading.Event()

        def slow_fetch(*args, **kwargs):
            started.set()
            release.wait(5)
            return segment(b'shared segment')
        bodies = []
        with patch.object(http_client, 'fetch_bytes', side_effect=slow_fetch) as fetch:
            first = threading.Thread(target=lambda: bodies.append(serve().body))
            first.start()
            self.assertTrue(started.wait(5))
            second = threading.Thread(target=lambda: bodies.append(serve().body))
            second.start()
            release.set()
            first.join(5)
            second.join(5)
        self.assertEqual(bodies, [b'shared segment', b'shared segment'])
        fetch.assert_called_once()
        self.assertEqual(seg_cache._inflight, {})

    def test_slow_but_steady_segment_is_not_restarted(self):
        clock = [0.0]

        def chunks(*args, **kwargs):
            for _ in range(3):
                clock[0] += 4
                yield b'x' * 100
        response = Mock(status_code=200, headers={'content-length': '300'}, iter_content=Mock(side_effect=chunks))
        with patch.object(http_client, 'fetch_bytes', return_value=response) as fetch, \
                patch.object(hls_proxy.time, 'monotonic', side_effect=lambda: clock[0]):
            body = serve().body
        self.assertEqual(body, b'x' * 300)
        fetch.assert_called_once()
        self.assertTrue(fetch.call_args.kwargs['stream'])
        self.assertLessEqual(fetch.call_args.kwargs['timeout'], 6)
        response.close.assert_called_once()

    def test_short_body_is_retried_and_never_cached(self):
        short = segment(b'x' * 100, {'content-length': '300'})
        full = segment(b'x' * 300, {'Content-Length': '300'})
        with patch.object(http_client, 'fetch_bytes', side_effect=[short, full]) as fetch:
            body = serve().body
        self.assertEqual(body, b'x' * 300)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(seg_cache.get(URL), b'x' * 300)
        short.close.assert_called_once()

    def test_stalled_attempts_share_the_total_budget(self):
        clock = [0.0]

        def stalled(*args, **kwargs):
            clock[0] += 10
            raise TimeoutError('stalled')
        with patch.object(http_client, 'fetch_bytes', side_effect=stalled) as fetch, \
                patch.object(hls_proxy.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                serve()
        self.assertEqual(fetch.call_count, 2)
        self.assertIsNone(seg_cache.get(URL))

    def test_media_playlist_registers_segment_order(self):
        text = b'#EXTM3U\n#EXTINF:4,\nseg1.ts\n#EXTINF:4,\nseg2.ts\n#EXTINF:4,\nseg3.ts\n#EXT-X-ENDLIST\n'
        playlist = Mock(status_code=200, headers={}, content=text, url=BASE + 'index.m3u8')
        with patch.object(http_client, 'fetch_bytes', return_value=playlist):
            self.assertEqual(serve(BASE + 'index.m3u8').status_code, 200)
        self.assertEqual(seg_cache._following(BASE + 'seg1.ts', 3), [BASE + 'seg2.ts', BASE + 'seg3.ts'])
        master = Mock(status_code=200, headers={}, content=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n',
                      url=BASE + 'master.m3u8')
        with patch.object(http_client, 'fetch_bytes', return_value=master):
            serve(BASE + 'master.m3u8')
        self.assertEqual(len(seg_cache._playlists), 1)


if __name__ == '__main__':
    unittest.main()
