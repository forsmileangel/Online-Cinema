import asyncio
import unittest
from unittest.mock import Mock, patch

from starlette.requests import Request

from backend import hls_proxy, http_client, security


SOURCE = 'https://vip.dytt-tvs.com/episode/segment.ts?hash=example'
TARGET = 'https://cnvod.jimxtc.com/episode/segment.ts?hash=example'


def response(url, status=200, headers=None, content=b'\x47' * 188):
    return Mock(url=url, status_code=status, headers=headers or {}, content=content)


class MediaRedirectTests(unittest.TestCase):
    def setUp(self):
        self.saved_hosts = dict(security._extra_media_hosts)
        self.addCleanup(self.restore_hosts)
        dns = patch.object(security.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))])
        dns.start()
        self.addCleanup(dns.stop)

    def restore_hosts(self):
        security._extra_media_hosts.clear()
        security._extra_media_hosts.update(self.saved_hosts)

    def serve(self, url=SOURCE, method='GET', headers=None):
        request = Request({'type': 'http', 'method': method, 'path': '/api/hls', 'scheme': 'http',
                           'query_string': b'', 'server': ('192.168.1.10', 6970),
                           'headers': [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})
        return hls_proxy.serve_media(request, url)

    def test_gimy_segment_follows_validated_cdn_redirect(self):
        redirect = response(SOURCE, 302, {'location': TARGET})
        media = response(TARGET)
        session = Mock(request=Mock(side_effect=[redirect, media]))
        with patch.object(http_client, 'media_session', return_value=session), patch.object(hls_proxy.seg_cache, 'get', return_value=None), patch.object(hls_proxy.seg_cache, 'put'), patch.object(hls_proxy.seg_cache, 'start_workers'), patch.object(hls_proxy.seg_cache, 'enqueue_next'):
            result = self.serve()
        self.assertEqual(result.body, media.content)
        self.assertEqual(result.media_type, 'video/mp2t')
        self.assertEqual([call.args[1] for call in session.request.call_args_list], [SOURCE, TARGET])
        self.assertTrue(all(not call.kwargs['allow_redirects'] for call in session.request.call_args_list))
        redirect.close.assert_called_once()
        media.close.assert_called_once()

    def test_unsafe_redirect_is_rejected_before_following_or_retrying(self):
        for target in ('http://cnvod.jimxtc.com/a.ts', 'https://127.0.0.1/a.ts',
                       'https://10.0.0.1/a.ts', 'https://cnvod.jimxtc.com:8443/a.ts',
                       'https://user@cnvod.jimxtc.com/a.ts', 'https://unknown.example/page.html'):
            with self.subTest(target=target):
                redirect = response(SOURCE, 302, {'location': target})
                session = Mock(request=Mock(return_value=redirect))
                with patch.object(http_client, 'media_session', return_value=session), patch.object(hls_proxy.seg_cache, 'get', return_value=None), patch.object(hls_proxy.seg_cache, 'start_workers'):
                    with self.assertRaises(security.UnsafeURL):
                        self.serve()
                self.assertEqual(session.request.call_count, 1)
                redirect.close.assert_called_once()

    def test_redirect_preserves_head_range_and_partial_response(self):
        for method in ('GET', 'HEAD'):
            with self.subTest(method=method):
                redirect = response(SOURCE, 302, {'location': TARGET})
                media = response(TARGET, 206, {'Content-Range': 'bytes 0-1/188', 'Content-Length': '2'})
                session = Mock(request=Mock(side_effect=[redirect, media]))
                with patch.object(http_client, 'media_session', return_value=session):
                    result = self.serve(method=method, headers={'Range': 'bytes=0-1'})
                self.assertEqual(result.status_code, 206)
                self.assertEqual(result.headers['content-range'], 'bytes 0-1/188')
                for call in session.request.call_args_list:
                    self.assertEqual(call.args[0], method)
                    self.assertEqual(call.kwargs['headers']['Range'], 'bytes=0-1')
                if method == 'GET':
                    asyncio.run(result.background())
                media.close.assert_called_once()

    def test_redirect_limit_and_rate_limit_do_not_add_retries(self):
        for status in (302, 429):
            with self.subTest(status=status):
                replies = [response(SOURCE, 302, {'location': TARGET})]
                replies += [response(TARGET, status, {'location': TARGET, 'retry-after': '12'}) for _ in range(3)]
                session = Mock(request=Mock(side_effect=replies))
                with patch.object(http_client, 'media_session', return_value=session):
                    with self.assertRaises(security.UnsafeURL if status == 302 else security.SiteBusy):
                        self.serve(method='HEAD')
                self.assertEqual(session.request.call_count, 4 if status == 302 else 2)

    def test_lg_variant_redirect_uses_the_same_validation(self):
        source = SOURCE.replace('segment.ts', 'master.m3u8')
        target = TARGET.replace('segment.ts', 'master.m3u8')
        redirect = response(source, 302, {'location': target})
        media = response(target, content=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nmedia.m3u8\n')
        session = Mock(request=Mock(side_effect=[redirect, media]))
        from urllib.parse import quote
        import time
        with patch.object(http_client, 'media_session', return_value=session):
            result = hls_proxy.dlna_media_url('http://192.168.1.10:6970/api/hls?u=' + quote(source, safe=''), time.monotonic() + 30)
        self.assertEqual(result, 'http://192.168.1.10:6970/api/hls?u=' + quote('https://cnvod.jimxtc.com/episode/media.m3u8', safe=''))

    def test_redirects_share_the_request_timeout(self):
        session = Mock(request=Mock(return_value=response(SOURCE, 302, {'location': TARGET})))
        with patch.object(http_client, 'media_session', return_value=session), patch.object(http_client.time, 'monotonic', side_effect=[0, 1, 6]):
            with self.assertRaises(TimeoutError):
                http_client.fetch_bytes(SOURCE, referer='https://gimyai.tw/', allowed_hosts=security.hls_allowed_hosts(SOURCE),
                                        timeout=5, redirect_validator=Mock())
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(session.request.call_args.kwargs['timeout'], 4)


if __name__ == '__main__':
    unittest.main()
