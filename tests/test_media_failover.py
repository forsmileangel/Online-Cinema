import json
import unittest
from unittest.mock import Mock, patch

from curl_cffi import CurlECode, requests
from starlette.requests import Request

from backend import hls_proxy, http_client, lan, seg_cache, security

URL = 'https://svip.xgplay20.com/episode/segment.ts?hash=sample'


class MediaFailoverTests(unittest.TestCase):
    def setUp(self):
        for target, name, kwargs in [
            (hls_proxy, 'assert_hls_url', {'side_effect': lambda u: u}),
            (hls_proxy, 'hls_allowed_hosts', {'return_value': {'svip.xgplay20.com'}}),
            (seg_cache, 'get', {'return_value': None}),
            (seg_cache, 'start_workers', {}), (seg_cache, 'enqueue_next', {}),
        ]:
            stub = patch.object(target, name, **kwargs)
            stub.start()
            self.addCleanup(stub.stop)

    def serve(self, url=URL):
        request = Request({'type': 'http', 'method': 'GET', 'path': '/api/hls', 'scheme': 'http',
                           'query_string': b'', 'server': ('127.0.0.1', 6970), 'headers': []})
        return hls_proxy.serve_media(request, url)

    def test_failover_applies_to_other_episodes_and_segment_formats(self):
        for path in ('different-series/episode-81/0009.ts', 'episode-2/video9.jpeg',
                     'episode-3/0004.m4s', 'episode-4/audio.aac', 'episode-5/encryption.key'):
            url = 'https://media.example.com/' + path
            with self.subTest(path=path), \
                    patch.object(http_client, 'fetch_bytes', side_effect=[TimeoutError('slow'),
                        Mock(status_code=200, headers={}, content=b'whole segment', url=url)]) as fetch, \
                    patch.object(lan, 'download_interfaces', return_value=['192.168.1.15', '192.168.1.10']), \
                    patch.object(seg_cache, 'put'):
                self.assertEqual(self.serve(url).body, b'whole segment')
                self.assertEqual(fetch.call_args.args[0], url)
                self.assertEqual(fetch.call_args.kwargs['interface'], '192.168.1.10')

    def test_partial_download_retries_whole_segment_on_other_adapter(self):
        complete = Mock(status_code=200, headers={}, content=b'complete segment', url=URL)
        partial = requests.RequestsError('truncated', code=CurlECode.PARTIAL_FILE)
        with patch.object(http_client, 'fetch_bytes', side_effect=[partial, complete]) as fetch, \
                patch.object(lan, 'download_interfaces', return_value=['192.168.1.15', '192.168.1.10']), \
                patch.object(seg_cache, 'put') as save:
            result = self.serve()
        self.assertEqual(result.body, b'complete segment')
        self.assertEqual([c.args[0] for c in fetch.call_args_list], [URL, URL])
        self.assertEqual([c.kwargs['interface'] for c in fetch.call_args_list], [None, '192.168.1.10'])
        self.assertTrue(all(c.kwargs['timeout'] <= 6 for c in fetch.call_args_list))
        save.assert_called_once_with(URL, b'complete segment')
        complete.close.assert_called_once()

    def test_retries_share_total_budget_including_adapter_detection(self):
        with patch.object(http_client, 'fetch_bytes', side_effect=TimeoutError('slow')) as fetch, \
                patch.object(lan, 'download_interfaces', return_value=['192.168.1.15', '192.168.1.10']), \
                patch.object(hls_proxy.time, 'monotonic', side_effect=[0, 0, 10, 18]), \
                patch.object(seg_cache, 'put') as save:
            with self.assertRaises(TimeoutError):
                self.serve()
        self.assertEqual(fetch.call_count, 2)
        save.assert_not_called()

    def test_single_adapter_keeps_default_route_and_limits_attempts(self):
        with patch.object(http_client, 'fetch_bytes', side_effect=TimeoutError('slow')) as fetch, \
                patch.object(lan, 'download_interfaces', return_value=['192.168.1.15']):
            with self.assertRaises(TimeoutError):
                self.serve()
        self.assertEqual(fetch.call_count, 3)
        self.assertTrue(all(c.kwargs['interface'] is None for c in fetch.call_args_list))

    def test_rejected_or_unsafe_sources_do_not_switch_adapters(self):
        for error in (security.SiteBusy('CDN', 429, 10), security.UnsafeURL('blocked'), ValueError('bad response')):
            with self.subTest(error=error), patch.object(http_client, 'fetch_bytes', side_effect=error) as fetch, \
                    patch.object(lan, 'download_interfaces') as interfaces:
                with self.assertRaises(type(error)):
                    self.serve()
                fetch.assert_called_once()
                interfaces.assert_not_called()

    def test_sessions_are_separate_for_each_adapter(self):
        with patch.object(http_client._tls, 'by_imp', {}, create=True), patch.object(http_client.requests, 'Session') as session:
            session.side_effect = [Mock(), Mock()]
            first = http_client.media_session('chrome131', '192.168.1.10')
            second = http_client.media_session('chrome131', '192.168.1.15')
            self.assertIsNot(first, second)
            self.assertIs(first, http_client.media_session('chrome131', '192.168.1.10'))
            self.assertEqual(session.call_count, 2)
            self.assertEqual(session.call_args.kwargs['interface'], '192.168.1.15')

    def test_bound_interface_survives_redirects_without_relaxing_url_validation(self):
        redirected = URL.replace('segment.ts', 'other.ts')
        replies = [Mock(url=URL, status_code=302, headers={'location': redirected}),
                   Mock(url=redirected, status_code=200, headers={}, content=b'ok')]
        session = Mock(request=Mock(side_effect=replies))
        validator = Mock()
        with patch.object(http_client, 'media_session', return_value=session) as get_session, \
                patch.object(http_client, 'final_url_still_allowed'), patch.object(http_client, 'touch_media_host'):
            http_client.fetch_bytes(URL, referer='https://gimyai.tw/', allowed_hosts={'svip.xgplay20.com'},
                                    interface='192.168.1.10', redirect_validator=validator)
        get_session.assert_called_once_with(None, '192.168.1.10')
        self.assertEqual(session.request.call_count, 2)
        validator.assert_called_once_with(redirected, 'svip.xgplay20.com')
        self.assertTrue(all(not c.kwargs['allow_redirects'] for c in session.request.call_args_list))

    def test_current_default_route_is_first_and_detection_refreshes_after_network_change(self):
        route = Mock()
        route.getsockname.side_effect = [('192.168.1.15', 1), ('192.168.50.2', 1)]
        with patch.object(lan, '_download_checked', 0), patch.object(lan, '_download_ips', []), \
                patch.object(lan.os, 'name', 'nt'), patch.object(lan.time, 'monotonic', side_effect=[100, 101, 110, 140, 141]), \
                patch.object(lan.subprocess, 'run', side_effect=[Mock(stdout=json.dumps(['192.168.1.10', '192.168.1.15']).encode()),
                                                               Mock(stdout=json.dumps(['192.168.50.2']).encode())]) as query, \
                patch.object(lan.socket, 'socket') as sock:
            sock.return_value.__enter__.return_value = route
            self.assertEqual(lan.download_interfaces(), ['192.168.1.15', '192.168.1.10'])
            self.assertEqual(lan.download_interfaces(), ['192.168.1.15', '192.168.1.10'])
            self.assertEqual(lan.download_interfaces(), ['192.168.50.2'])
            self.assertEqual(query.call_count, 2)


if __name__ == '__main__':
    unittest.main()
