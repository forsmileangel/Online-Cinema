import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from backend import hls_proxy, security
from backend.sites import mvffm as site
from backend.security import SiteBusy, SourceUnavailable, UnsafeURL


def card(vid="123", path="/drama/123/", title="Example"):
    return f'<article class="item" id="post-{vid}"><img data-lazy-src="https://www.mvffm.net/cover.jpg"><h3><a href="{path}">{title}</a></h3></article>'


def player(data):
    return '<h1>Example</h1><script>new Vue({data:{videourls:' + json.dumps(data) + '}})</script>'


class MVFFMTests(unittest.TestCase):
    def setUp(self):
        for cache in (site._html, site._ids, site._cooldowns, site._preferred, site._inflight):
            cache.clear()
        p = patch.object(security, '_assert_not_private')
        p.start(); self.addCleanup(p.stop)
        for name in ('_extra_media_hosts', '_extra_media_sources'):
            p = patch.object(security, name, {})
            p.start(); self.addCleanup(p.stop)

    def test_home_sections_and_image_guards(self):
        html = '<header><h2>電影</h2></header><div class="items">' + card() + '</div>'
        with patch.object(site, '_get', return_value=html):
            rows, picks = site.home_bundle()
        self.assertEqual(rows[0][2][0].id, '123')
        self.assertEqual(len(picks[0].items), 3)
        for url in ['', 'http://www.mvffm.net/a.jpg', 'https://mvffm.net.evil.test/a.jpg', 'https://user@mvffm.net/a.jpg']:
            self.assertEqual(site._cover(url), '')

    def test_home_rows_have_distinct_working_category_links(self):
        html = ''.join('<header><h2>'+name+'</h2></header><div class="items">'+card()+'</div>' for name in ['熱門推薦', '電影', '推薦連續劇', '美劇'])
        with patch.object(site, '_get', return_value=html):
            rows, _ = site.home_bundle()
        self.assertEqual(len(set(row[0] for row in rows)), 4)
        with patch.object(site, '_listing') as listing:
            site.browse('usdrama')
        self.assertEqual(listing.call_args.args[0], '/tvtype/usdrama/')

    def test_search_stable_numeric_ids_from_shortlink(self):
        html = '<div class="result-item"><article><div class="title"><a href="/movies/example/">Example</a></div></article></div>'
        response = Mock(headers={'link': '<https://www.mvffm.net/?p=456>; rel=shortlink'})
        with patch.object(site, '_request', return_value=response) as request:
            first = site.parse_cards(html, resolve=True)
            second = site.parse_cards(html, resolve=True)
        self.assertEqual(first[0].id, '456')
        self.assertEqual(second[0].id, '456')
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs['method'], 'HEAD')
        response.close.assert_called_once()

    def test_cards_reject_unrelated_paths_and_deduplicate(self):
        html = card() + card() + card('234', '/jav/234/') + card('345', 'https://evil.test/drama/345/')
        self.assertEqual([x.id for x in site.parse_cards(html)], ['123'])

    def test_browse_follows_published_pagination(self):
        first = card() + '<div class="pagination"><a href="/movies/page/2/">2</a></div>'
        with patch.object(site, '_get', side_effect=[first, card('456', '/drama/456/')]) as get:
            result = site.browse('movie', page=2)
        self.assertEqual(result.items[0].id, '456')
        self.assertEqual(get.call_args.args[0], site.ORIGIN + '/movies/page/2/')
        self.assertFalse(result.has_next)

    def test_search_rejects_another_query_pagination(self):
        html = card() + '<div class="pagination"><a href="/xssearch?q=Other&p=2">2</a></div>'
        with patch.object(site, '_get', return_value=html):
            self.assertFalse(site.search('Example').has_next)
            self.assertFalse(site.search('Example', 2).items)

    def test_episode_labels_align_sort_and_deduplicate_across_lines(self):
        choices = site._episodes(player([
            [{'name': '02', 'url': 'https://a.test/2.m3u8'}],
            [{'name': '第1集', 'url': 'https://b.test/1.m3u8'}, {'name': '2', 'url': 'https://b.test/2.m3u8'}],
            [{'name': '02', 'url': 'https://a.test/2.m3u8'}, {'name': '特別篇', 'url': 'https://a.test/s.m3u8'}]]))
        self.assertEqual(list(choices)[:2], ['1', '2'])
        self.assertEqual(len(choices['2'][1]), 2)
        self.assertLessEqual(len(list(choices)[2]), 16)

    def test_movie_lines_merge_and_scripts_are_not_executed(self):
        self.assertEqual(len(site._episodes(player([{'url': 'https://a.test/a.m3u8'}, {'url': 'https://b.test/a.mp4'}]))['1'][1]), 2)
        self.assertFalse(site._episodes('videourls:executeRemoteCode()'))
        self.assertFalse(site._episodes(player([{'url': 'https://a.test/embed.html'}])))

    def test_fetch_rejects_unknown_episode_before_probing(self):
        with patch.object(site, '_get', return_value=player([{'url': 'https://a.test/a.m3u8'}])), patch.object(site, '_fastest') as probe:
            with self.assertRaises(SourceUnavailable):
                site.fetch_video('123', '2')
        probe.assert_not_called()

    def test_legacy_collection_has_selectable_seasons(self):
        collection = '<div id="seasons"><a href="/drama/456/">Season 1</a><a href="/drama/789/">Season 2</a></div>'
        with patch.object(site, '_get', side_effect=[collection, player([{'url': 'https://a.test/a.m3u8'}])]), patch.object(site, '_fastest', return_value='/api/hls?u=test'):
            result = site.fetch_video('123')
        self.assertEqual(result.id, '123')
        self.assertEqual([c.id for c in result.related], ['456', '789'])
        self.assertEqual(result.resolved_episode_id, '1')

    def test_faster_nonfirst_line_wins_without_waiting_for_slow_line(self):
        finished = threading.Event()
        def probe(url, deadline, stop):
            if url == 'slow':
                stop.wait(1)
                finished.set()
                raise TimeoutError()
            return '/api/hls?u=fast'
        start = time.monotonic()
        with patch.object(site, '_probe', side_effect=probe):
            result = site._fastest('123', '1', [(0, 'slow'), (1, 'fast')])
            self.assertTrue(finished.wait(1))
        self.assertEqual(result, '/api/hls?u=fast')
        self.assertLess(time.monotonic() - start, .5)
        self.assertEqual(site._preferred['123'][1], 1)

    def test_failed_initial_lines_fall_back_with_bounded_concurrency(self):
        count, peak, calls = 0, 0, []
        lock = threading.Lock()
        finished = threading.Event()
        def probe(url, deadline, stop):
            nonlocal count, peak
            with lock:
                count += 1; peak = max(peak, count); calls.append(url)
            try:
                stop.wait(.01)
                if url != '6': raise TimeoutError()
                return 'winner'
            finally:
                with lock:
                    count -= 1
                    if count == 0: finished.set()
        with patch.object(site, '_probe', side_effect=probe):
            self.assertEqual(site._fastest('123', '1', [(i, str(i)) for i in range(20)]), 'winner')
            finished.wait(1)
        self.assertLessEqual(peak, 3)
        self.assertLessEqual(len(calls), 8)

    def test_all_rejected_preserves_retry_after(self):
        error = SiteBusy('cdn.test', 429, 90)
        with patch.object(site, '_probe', side_effect=error):
            with self.assertRaises(SiteBusy) as caught:
                site._fastest('123', '1', [(0, 'one')])
        self.assertIs(caught.exception, error)

    def test_host_cooldown_prevents_repeat_request(self):
        error = SiteBusy('cdn.test', 429, 90)
        with patch.object(site.http_client, 'fetch_bytes', side_effect=error) as fetch:
            for _ in range(2):
                with self.assertRaises(SiteBusy):
                    site._request('https://cdn.test/a.m3u8', media=True)
        self.assertEqual(fetch.call_count, 1)

    def test_identical_concurrent_resolution_is_coalesced(self):
        started, release = threading.Event(), threading.Event()
        def probe(*args):
            started.set(); release.wait(1); return 'winner'
        with patch.object(site, '_probe', side_effect=probe) as mocked, ThreadPoolExecutor(2) as pool:
            one = pool.submit(site._fastest, '123', '1', [(0, 'x')])
            self.assertTrue(started.wait(1))
            two = pool.submit(site._fastest, '123', '1', [(0, 'x')])
            release.set()
            self.assertEqual(one.result(), two.result())
        self.assertEqual(mocked.call_count, 1)

    def test_probe_reads_key_and_sample_preserves_master(self):
        master = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,CODECS="avc1.42e01e,mp4a.40.2"\nchild.m3u8\n'
        child = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.key"\n#EXTINF:4,\nfirst.ts\n#EXT-X-ENDLIST\n'
        responses = [(master.encode(), 'https://cdn.test/index.m3u8'), (child.encode(), 'https://cdn.test/child.m3u8'), (b'k'*16, 'https://cdn.test/key.key'), (b's'*100, 'https://cdn.test/first.ts')]
        with patch.object(site, '_read', side_effect=responses) as read:
            result = site._probe('https://cdn.test/index.m3u8', time.monotonic()+1, threading.Event())
        self.assertIn('index.m3u8', result)
        self.assertTrue(read.call_args.kwargs['sample'])
        self.assertEqual(read.call_count, 4)
        self.assertEqual(hls_proxy._media_context('https://cdn.test/first.ts')[0], site.ORIGIN + '/')

    def test_probe_rejects_html_and_bad_keys(self):
        for body in [b'<html>denied</html>', b'#EXTM3U\n#EXTINF:4,\nfirst.ts\n']:
            with patch.object(site, '_read', side_effect=[(body, 'https://cdn.test/a.m3u8'), (b'<html>denied</html>', 'https://cdn.test/first.ts')]):
                with self.assertRaises(SourceUnavailable):
                    site._probe('https://cdn.test/a.m3u8', time.monotonic()+1, threading.Event())

    def test_redirect_validation_and_source_guard(self):
        with self.assertRaises(UnsafeURL): site._source_url('https://evil.test/page')
        with self.assertRaises(UnsafeURL): site._source_url('http://www.mvffm.net/page')
        with self.assertRaises(UnsafeURL): site._probe('http://cdn.test/a.m3u8', time.monotonic()+1, threading.Event())
        response = Mock()
        with patch.object(site.http_client, 'fetch_bytes', return_value=response) as fetch:
            site._request('https://www.mvffm.net/home/')
        self.assertIs(fetch.call_args.kwargs['redirect_validator'], site._source_url)
        with patch.object(security, '_assert_not_private', side_effect=UnsafeURL('private')):
            with self.assertRaises(UnsafeURL):
                site._probe('https://127.0.0.1/a.m3u8', time.monotonic()+1, threading.Event())

    def test_registration_history_and_explicit_episode(self):
        from backend import cast_session, db, main, sites
        from backend.models import Episode, VideoDetail
        self.assertIs(sites.get('mvffm'), site)
        self.assertIn('mvffm', cast_session.SERIES_SOURCES)
        def detail(vid, ep=None):
            return VideoDetail(id=vid, title='Example', cover='', playlist='test', resolved_episode_id=ep,
                               episodes=[Episode(id='1', title='1'), Episode(id='2', title='2')])
        for requested, expected, seconds in [(None, '2', 42), ('1', '1', 0)]:
            with patch.object(db, 'get_history_item', return_value={'episode_id': '2', 'position_sec': 42}), patch.object(db, 'is_favorite', return_value=False), patch.object(site, 'fetch_video', autospec=True, side_effect=detail):
                result = main.video_on_source('mvffm', '123', requested)
            self.assertEqual((result.resolved_episode_id, result.position_sec), (expected, seconds))

    def test_managed_cast_resolves_and_advances_episodes(self):
        from backend import cast, cast_session, offline
        from backend.models import Episode, VideoDetail
        def detail(vid, ep=None):
            selected = ep or '1'
            url = hls_proxy.proxied_media('https://cdn.test/'+selected+'.m3u8')
            return VideoDetail(id=vid, title='Example', cover='', playlist=url, resolved_episode_id=selected,
                episodes=[Episode(id=n, title=n, playlist=url if n == selected else '') for n in ['1','2','3']])
        security.remember_media_host('cdn.test', source='mvffm')
        state = lambda url, *a, **kw: dict(content_id=url, playing=True, paused=False, idle=False, buffering=False, current_time=0, duration=100)
        with patch.object(site, 'fetch_video', autospec=True, side_effect=detail), patch.object(offline, 'playback'), patch.object(cast, 'lan_media_origin', return_value='http://192.168.1.10:6970'), patch.object(cast, 'check_media_origin'), patch.object(cast, 'session_content', return_value=''), patch.object(cast, 'play', side_effect=state):
            session = cast_session.PlaybackSession(dict(uuid='fake', source='mvffm', video_id='123', episode_id='1', autoplay_next=True))
            session.save = Mock(); session.prefetch = Mock()
            session.load('1')
            session.handle('episode', {'episode_id': '2'})
            self.assertEqual(session.get()['episode_id'], '2')
            idle = {**session.previous, 'playing': False, 'idle': True, 'idle_reason': 'FINISHED'}
            with patch.object(cast, 'status', return_value=idle):
                session.tick()
            self.assertEqual(session.get()['episode_id'], '3')


if __name__ == '__main__':
    unittest.main()
