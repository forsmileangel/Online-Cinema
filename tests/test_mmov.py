import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import cast, cast_session, db, hls_proxy, http_client, main, security, sites
from backend.models import Episode, VideoDetail
from backend.sites import mmov

FIXTURES = Path(__file__).parent / 'fixtures'


def fixture(name):
    return (FIXTURES / ('mmov_' + name + '.html')).read_text(encoding='utf-8')


class MMOVTests(unittest.TestCase):
    def setUp(self):
        mmov._preferred.clear()
        mmov._pages.clear()
        mmov._html.clear()
        mmov._cooldowns.clear()
        mmov._search_finished = 0.0
        p = patch.object(mmov, '_SEARCH_INTERVAL', 0)
        p.start(); self.addCleanup(p.stop)
        p = patch.object(security, '_assert_not_private')
        p.start(); self.addCleanup(p.stop)
        for name in ('_extra_media_hosts', '_extra_media_sources'):
            p = patch.object(security, name, {})
            p.start(); self.addCleanup(p.stop)

    def test_home_cards_categories_and_empty_or_foreign_covers(self):
        with patch.object(mmov, '_get', return_value=fixture('home')):
            rows, picks = mmov.home_bundle()
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0][2][0].source, 'mmov')
        self.assertEqual(rows[0][2][0].title, '大刑伺候')
        self.assertTrue(rows[0][2][0].cover.startswith('/api/img?u=https'))
        self.assertEqual(len(picks[0].items), 4)
        self.assertEqual(mmov._cover(''), '')
        self.assertEqual(mmov._cover('https://img.mmov.app.evil.test/a.jpg'), '')
        self.assertEqual(mmov._cover('http://img.mmov.app/a.jpg'), '')

    def test_search_and_category_follow_real_page_links(self):
        for search in (False, True):
            with self.subTest(search=search):
                base = '/vodsearch/Neutral----------2---.html' if search else '/type/2-2.html'
                first = fixture('search') + f'<ul class="stui-page"><a href="{base}">下一頁</a></ul>'
                second = fixture('search').replace('515042', '515043')
                with patch.object(mmov, '_get', side_effect=[first, second]) as get:
                    run = (lambda page: mmov.search('Neutral', page)) if search else (lambda page: mmov.browse('tv', page=page))
                    self.assertTrue(run(1).has_next)
                    self.assertEqual(run(2).items[0].id, '515043')
                    self.assertFalse(run(3).items)
                self.assertEqual(get.call_args_list[1].args[0], base)
                self.assertEqual(get.call_count, 2)

    def test_real_category_and_genre_pagination(self):
        for kind, slug, fixture_name in [('tv', None, 'category'), ('category', '13', 'genre')]:
            with patch.object(mmov, '_get', return_value=fixture(fixture_name)):
                result = mmov.browse(kind, slug)
            self.assertTrue(result.has_next)
            self.assertEqual(len(result.items), 2)

    def test_search_repeated_first_page_does_not_offer_next(self):
        with patch.object(mmov, '_get', return_value=fixture('search')):
            self.assertFalse(mmov.search('大刑伺候').has_next)

    def test_empty_search_retries_normalized_traditional(self):
        from urllib.parse import quote
        for query, normalized in [('鬥羅大陸', '斗羅大陸'), ('庆余年', '慶餘年')]:
            with self.subTest(query=query), patch.object(mmov, '_get', side_effect=['', fixture('search')]) as get:
                result = mmov.search(query)
            self.assertTrue(result.items)
            self.assertEqual(result.title, query)
            self.assertEqual([call.args[0] for call in get.call_args_list],
                             ['/vodsearch/' + quote(q, safe='') + '-------------.html' for q in (query, normalized)])
            self.assertTrue(all(call.kwargs == {'operation': 'search'} for call in get.call_args_list))
            self.assertIn('search:' + query, mmov._pages)
            self.assertIn('search:' + normalized, mmov._pages)

    def test_successful_original_search_does_not_retry(self):
        with patch.object(mmov, '_get', return_value=fixture('search')) as get:
            result = mmov.search('鬥羅大陸')
        self.assertTrue(result.items)
        self.assertEqual(result.title, '鬥羅大陸')
        get.assert_called_once()

    def test_unchanged_empty_search_does_not_retry(self):
        for query in ('慶餘年', 'Neutral-123'):
            with self.subTest(query=query), patch.object(mmov, '_get', return_value='') as get:
                self.assertFalse(mmov.search(query).items)
            get.assert_called_once()

    def test_busy_search_is_not_retried(self):
        busy = security.SiteBusy('MMOV', 429, 30)
        with patch.object(mmov, '_get', side_effect=busy) as get:
            with self.assertRaises(security.SiteBusy) as caught:
                mmov.search('鬥羅大陸')
        self.assertIs(caught.exception, busy)
        get.assert_called_once()

    def test_normalized_search_uses_its_own_next_page_links(self):
        from urllib.parse import quote
        path = '/vodsearch/' + quote('斗羅大陸', safe='') + '----------2---.html'
        first = fixture('search') + f'<ul class="stui-page"><a href="{path}">下一頁</a></ul>'
        second = fixture('search').replace('515042', '515043')
        with patch.object(mmov, '_get', side_effect=['', first, second]) as get:
            self.assertTrue(mmov.search('鬥羅大陸').has_next)
            listing = mmov.search('鬥羅大陸', page=2)
            self.assertEqual(listing.items[0].id, '515043')
            self.assertEqual((listing.page, listing.title, listing.has_next), (2, '鬥羅大陸', False))
            self.assertFalse(mmov.search('鬥羅大陸', page=3).items)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args.args[0], path)

    def test_third_route_succeeds_and_is_preferred_on_next_episode(self):
        with patch.object(mmov, '_get', return_value=fixture('detail')), patch.object(mmov, '_resolve', side_effect=[ValueError('TLS'), TimeoutError(), '/api/hls?u=good']) as resolve:
            detail = mmov.fetch_video('515042', '2')
        self.assertEqual(resolve.call_count, 3)
        self.assertTrue(resolve.call_args.args[0].endswith('/2-2.html'))
        self.assertEqual(detail.resolved_episode_id, '2')
        self.assertEqual([e.id for e in detail.episodes], ['1', '2'])
        self.assertEqual(detail.episodes[0].playlist, '')
        self.assertTrue(detail.cover)
        with patch.object(mmov, '_get', return_value=fixture('detail')), patch.object(mmov, '_resolve', return_value='/api/hls?u=good') as resolve:
            mmov.fetch_video('515042', '1')
        self.assertTrue(resolve.call_args.args[0].endswith('/2-1.html'))
        self.assertEqual(resolve.call_count, 1)

    def test_rejected_route_stops_without_skipping_episode(self):
        with patch.object(mmov, '_get', return_value=fixture('detail')), patch.object(mmov, '_resolve', side_effect=security.SiteBusy('MMOV', 429, 30)) as resolve:
            with self.assertRaises(security.SiteBusy):
                mmov.fetch_video('515042')
        self.assertEqual(resolve.call_count, 1)

    def test_missing_episode_and_failed_routes_are_retryable_errors(self):
        with patch.object(mmov, '_get', return_value=fixture('detail')), patch.object(mmov, '_resolve', side_effect=ValueError('bad source')) as resolve:
            with self.assertRaises(security.SourceUnavailable): mmov.fetch_video('515042', '3')
            resolve.assert_not_called()
            with self.assertRaises(security.SourceUnavailable): mmov.fetch_video('515042', '2')
        self.assertEqual(resolve.call_count, 3)

    def test_selected_playlist_is_validated_before_being_returned(self):
        with patch.object(mmov, '_get', return_value=fixture('play')), patch.object(mmov, 'dlna_media_url') as validate:
            result = mmov._resolve('https://hk.mmov.io/vodplay/515042/2-1.html', time.monotonic() + 10)
        self.assertIn('v.lzcdn27.com', result)
        self.assertTrue(validate.call_args.kwargs['validate'])
        self.assertEqual(hls_proxy._media_context('https://v.lzcdn27.com/1.m3u8')[0], 'https://hk.mmov.io/')

    def test_source_redirect_boundary_private_ip_and_media_port(self):
        for url in ['https://hk.mmov.app.evil.test/x', 'http://hk.mmov.app/x', 'https://hk.mmov.io:999/x']:
            with self.subTest(url=url), self.assertRaises((security.UnsafeURL, ValueError)):
                mmov._source_url(url)
        with patch.object(security, '_assert_not_private', side_effect=security.UnsafeURL('private')):
            with self.assertRaises(security.UnsafeURL): mmov._source_url('https://hk.mmov.app/x')
        with patch.object(mmov, '_get', return_value='<script>var videoSrc = "https://public.example:999/index.m3u8";</script>'), patch.object(mmov, 'dlna_media_url') as validate:
            with self.assertRaises(security.UnsafeURL): mmov._resolve('unused', time.monotonic() + 10)
        validate.assert_not_called()

    def test_cross_cdn_context_and_idle_expiration(self):
        security.remember_media_host('media.example', source='mmov')
        text = '#EXTM3U\n#EXTINF:5,\nhttps://child.example/1.ts\n#EXT-X-ENDLIST\n'
        hls_proxy.rewrite_playlist(text, 'https://media.example/index.m3u8')
        self.assertEqual(hls_proxy._media_context('https://child.example/1.ts')[0], 'https://hk.mmov.io/')
        start = time.monotonic()
        with patch.object(security.time, 'monotonic', return_value=start + 3500):
            security.touch_media_host('https://child.example/1.ts')
        with patch.object(security.time, 'monotonic', return_value=start + 3601):
            self.assertEqual(security.media_host_source('child.example'), 'mmov')
            self.assertEqual(security.media_host_source('media.example'), '')
        with patch.object(security.time, 'monotonic', return_value=start + 7200):
            self.assertEqual(security.media_host_source('child.example'), '')

    def test_failed_search_does_not_clear_good_cache_or_block_video(self):
        good = Mock(iter_content=Mock(return_value=[b'ok']))
        with patch.object(http_client, 'fetch_bytes', side_effect=[good, security.SiteBusy('MMOV', 429, 10), good]) as fetch:
            self.assertEqual(mmov._get('/one', operation='search'), 'ok')
            with self.assertRaises(security.SiteBusy): mmov._get('/two', operation='search')
            with self.assertRaises(security.SiteBusy): mmov._get('/three', operation='search')
            self.assertEqual(mmov._get('/one', operation='search'), 'ok')
            self.assertEqual(mmov._get('/vod/123.html', operation='video'), 'ok')
        self.assertEqual(fetch.call_count, 3)

    def test_uncached_searches_are_spaced_but_cache_hits_are_immediate(self):
        now = [100.0]
        response = Mock(iter_content=Mock(return_value=[b'<html>ok</html>']))
        def sleep(delay):
            now[0] += delay
        with patch.object(mmov, '_SEARCH_INTERVAL', 5.5), patch.object(mmov.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(mmov.time, 'sleep', side_effect=sleep) as wait, patch.object(http_client, 'fetch_bytes', return_value=response) as fetch:
            mmov._get('/one', operation='search')
            mmov._get('/two', operation='search')
            mmov._get('/one', operation='search')
            mmov._get('/vod/123.html', operation='video')
        wait.assert_called_once_with(5.5)
        self.assertEqual(fetch.call_count, 3)

    def test_soft_404_stops_fallback_and_enters_search_cooldown(self):
        response = Mock(iter_content=Mock(return_value=[b'<title>404</title><h3>404,Data not found!</h3>']))
        with patch.object(http_client, 'fetch_bytes', return_value=response) as fetch:
            for query in ['鬥羅大陸', '慶餘年']:
                with self.assertRaises(security.SiteBusy):
                    mmov.search(query)
        fetch.assert_called_once()
        self.assertFalse(mmov._html)
        self.assertFalse(mmov._inflight)
        self.assertFalse(mmov._pages)
        self.assertFalse(mmov._search_lock.locked())

    def test_registration_history_and_explicit_episode_seconds(self):
        self.assertIn('mmov', cast_session.SERIES_SOURCES)
        self.assertIs(sites.get('mmov'), mmov)
        for requested, expected, seconds in [(None, '2', 42), ('1', '1', 0)]:
            with patch.object(db, 'get_history_item', return_value={'episode_id': '2', 'position_sec': 42}), patch.object(db, 'is_favorite', return_value=False), patch.object(mmov, 'fetch_video', side_effect=lambda vid, ep=None: self.detail(ep or '1')):
                result = main.video_on_source('mmov', '515042', requested)
            self.assertEqual((result.resolved_episode_id, result.position_sec), (expected, seconds))

    @staticmethod
    def detail(ep):
        return VideoDetail(id='515042', source='mmov', title='Neutral', cover='', resolved_episode_id=ep,
                           playlist='/api/hls?u=https%3A%2F%2Fmedia.example%2F1.m3u8',
                           episodes=[Episode(id=n, title='第'+n+'集', playlist='/api/hls?u=https%3A%2F%2Fmedia.example%2F'+n+'.m3u8') for n in ['1', '2', '3']])

    def test_server_switch_and_browserless_next_use_mmov_resolver(self):
        security.remember_media_host('media.example', source='mmov')
        state = lambda url, *a, **kw: dict(content_id=url, playing=True, paused=False, idle=False, buffering=False, current_time=0, duration=100)
        with patch.object(mmov, 'fetch_video', side_effect=lambda vid, ep=None: self.detail(ep or '1')), patch.object(cast, 'lan_media_origin', return_value='http://192.168.1.10:6970'), patch.object(cast, 'check_media_origin'), patch.object(cast, 'session_content', return_value=''), patch.object(cast, 'play', side_effect=state):
            session = cast_session.PlaybackSession(dict(uuid='fake', source='mmov', video_id='515042', episode_id='1', autoplay_next=True))
            session.save = Mock(); session.prefetch = Mock()
            session.load('1')
            session.handle('episode', {'episode_id': '2'})
            self.assertEqual(session.get()['episode_id'], '2')
            idle = {**session.previous, 'playing': False, 'idle': True, 'idle_reason': 'FINISHED'}
            with patch.object(cast, 'status', return_value=idle): session.tick()
            self.assertEqual(session.get()['episode_id'], '3')


if __name__ == '__main__':
    unittest.main()
