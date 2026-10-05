"""DNS checks must not be repeated per segment, nor turn a DNS blip into a hard refusal."""
import socket
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import hls_proxy, main, security

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.5', 443))]
HOST = 'cdn.yzzy27-play.com'
BASE = f'https://{HOST}/20260630/26585_e82ba729/3000k/hls/mixed.m3u8'


class DnsGuardTests(unittest.TestCase):
    def setUp(self):
        security._public_hosts.clear()
        self.addCleanup(security._public_hosts.clear)
        saved = dict(security._extra_media_hosts)
        self.addCleanup(lambda: (security._extra_media_hosts.clear(), security._extra_media_hosts.update(saved)))
        security.remember_media_host(HOST, source='mvffm')

    def test_long_playlist_resolves_its_cdn_once(self):
        text = '#EXTM3U\n#EXT-X-TARGETDURATION:9\n' + ''.join(f'#EXTINF:4,\nseg{i}.ts\n' for i in range(1655)) + '#EXT-X-ENDLIST\n'
        with patch.object(security.socket, 'getaddrinfo', return_value=PUBLIC) as lookup:
            segments = []
            out = hls_proxy.rewrite_playlist(text, BASE, 'http://127.0.0.1:6970', segments=segments)
        self.assertEqual(len(segments), 1655)
        self.assertEqual(out.count('/api/hls?u='), 1655)
        self.assertEqual(lookup.call_count, 1)

    def test_public_verdict_expires(self):
        with patch.object(security.socket, 'getaddrinfo', return_value=PUBLIC) as lookup, \
                patch.object(security.time, 'monotonic', return_value=1000.0):
            security.assert_hls_url(BASE)
            security.assert_hls_url(BASE)
        self.assertEqual(lookup.call_count, 1)
        with patch.object(security.socket, 'getaddrinfo', return_value=PRIVATE), \
                patch.object(security.time, 'monotonic', return_value=1000.0 + security._PUBLIC_DNS_TTL + 1):
            with self.assertRaises(security.UnsafeURL):
                security.assert_hls_url(BASE)

    def test_private_and_failed_lookups_are_never_reused(self):
        with patch.object(security.socket, 'getaddrinfo', return_value=PRIVATE) as lookup:
            for _ in range(2):
                with self.assertRaises(security.UnsafeURL) as caught:
                    security.assert_hls_url(BASE)
                self.assertNotIsInstance(caught.exception, security.HostUnresolvable)
        self.assertEqual(lookup.call_count, 2)
        with patch.object(security.socket, 'getaddrinfo', side_effect=socket.gaierror(11004, 'failed')) as lookup:
            for _ in range(2):
                with self.assertRaises(security.HostUnresolvable):
                    security.assert_hls_url(BASE)
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(security._public_hosts, {})

    def test_dns_failure_is_retryable_for_the_player(self):
        # The player retries 5xx in place; a 4xx would abandon the stream.
        error = main._err(security.HostUnresolvable('host not resolvable'))
        self.assertIsInstance(error, HTTPException)
        self.assertEqual(error.status_code, 502)
        self.assertEqual(main._err(security.UnsafeURL('address not allowed')).status_code, 400)


if __name__ == '__main__':
    unittest.main()
