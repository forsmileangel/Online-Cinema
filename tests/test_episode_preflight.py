import json
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import cast, cast_session, hls_proxy, http_client, security
from backend.sites import gimy


FIXTURES = Path(__file__).resolve().parent / 'fixtures'
BAD = 'https://vv.jisuzyv.com/play/b2k9v3zd/index.m3u8'
GOOD = 'https://vip.dytt-tvs.com/episode/index.m3u8'
BAD_LEAF = b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="enc.key"\n#EXTINF:8.125,\nhttps://p.jisuts.com:999/hls/episode/plist0.ts\n#EXT-X-ENDLIST\n'
GOOD_LEAF = b'#EXTM3U\n#EXTINF:4,\nsegment.ts\n#EXT-X-ENDLIST\n'


class EpisodePreflightTests(unittest.TestCase):
    def setUp(self):
        for obj, name, kwargs in [
            (security.socket, 'getaddrinfo', {'return_value': [(2, 1, 6, '', ('8.8.8.8', 443))]}),
            (gimy, '_get', {'side_effect': self.page}),
        ]:
            p = patch.object(obj, name, **kwargs)
            p.start(); self.addCleanup(p.stop)

    def page(self, path, **kwargs):
        if path.startswith('/detail/'):
            return (FIXTURES / 'gimy_detail.html').read_text(encoding='utf-8')
        url = BAD if '-2-' in path else GOOD
        return '<script>var player_data=' + json.dumps({'url': url, 'url_next': 'https://unknown.example/unverified.m3u8'}) + ';</script>'

    def media(self, url, **kwargs):
        return Mock(url=url, status_code=200, content=BAD_LEAF if url == BAD else GOOD_LEAF)

    def test_unsupported_segment_port_uses_second_route_before_casting(self):
        with patch.object(http_client, 'fetch_bytes', side_effect=self.media) as fetch:
            result = gimy.fetch_video('485760', ep='2')
        self.assertIn('dytt-tvs.com', result.playlist)
        self.assertEqual(result.resolved_episode_id, '2')
        self.assertEqual([c.args[0] for c in fetch.call_args_list], [BAD, GOOD])
        self.assertEqual([e.playlist for e in result.episodes if e.id != '2'], [''])

    def test_master_child_is_checked_before_selecting_route(self):
        child = 'https://vv.jisuzyv.com/play/b2k9v3zd/media.m3u8'
        def fetch(url, **kwargs):
            data = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=10\nmedia.m3u8\n' if url == BAD else BAD_LEAF if url == child else GOOD_LEAF
            return Mock(url=url, status_code=200, content=data)
        with patch.object(http_client, 'fetch_bytes', side_effect=fetch) as request:
            result = gimy.fetch_video('485760', ep='2')
        self.assertIn('dytt-tvs.com', result.playlist)
        self.assertEqual([c.args[0] for c in request.call_args_list], [BAD, child, GOOD])

    def test_rejected_playlist_does_not_try_another_route(self):
        with patch.object(http_client, 'fetch_bytes', side_effect=security.SiteBusy('Gimy', 429, 20)) as fetch:
            with self.assertRaises(security.SiteBusy):
                gimy.fetch_video('485760', ep='2')
        self.assertEqual(fetch.call_count, 1)

    def test_all_invalid_routes_leave_receiver_untouched(self):
        session = cast_session.PlaybackSession(dict(uuid='tv', source='gimy', video_id='485760', episode_id='1'))
        session.snapshot.update(phase='playing', content_id='old-episode', playing=True)
        with patch.object(http_client, 'fetch_bytes', side_effect=lambda url, **kw: Mock(url=url, content=BAD_LEAF)), patch.object(cast, 'play') as play, patch.object(cast, 'check_media_origin') as check:
            with self.assertRaises(security.UnsafeURL):
                session._load('2')
        play.assert_not_called()
        check.assert_not_called()

    def test_validated_prefetch_switches_once_without_browser(self):
        with patch.object(http_client, 'fetch_bytes', side_effect=self.media):
            detail = gimy.fetch_video('485760', ep='2')
        session = cast_session.PlaybackSession(dict(uuid='tv', source='gimy', video_id='485760', episode_id='1'))
        session.snapshot.update(phase='playing', content_id='old-episode', playing=True)
        session.episode_ids = ['1', '2']
        session.prefetched = ('2', time.monotonic(), detail)
        session.save = Mock()
        state = dict(playing=True, paused=False, content_id='new-episode', current_time=1, duration=100)
        with patch.object(gimy, 'fetch_video') as resolve, patch.object(cast, 'lan_media_origin', return_value='http://192.168.1.10:6970'), patch.object(cast, 'check_media_origin'), patch.object(cast, 'session_content', return_value='old-episode'), patch.object(cast, 'play', return_value=state) as play:
            session.handle('episode', {'episode_id': '2'})
        resolve.assert_not_called()
        play.assert_called_once()
        self.assertEqual(play.call_args.kwargs['expected'], 'old-episode')
        self.assertEqual(session.get()['episode_id'], '2')
        self.assertEqual(session.get()['phase'], 'playing')


if __name__ == '__main__':
    unittest.main()
