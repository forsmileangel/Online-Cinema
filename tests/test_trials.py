import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import db, hls_proxy, line_memory, main, security, trials
from backend.sites import chinaq, gimy, hongguo

FIRST = 'https://v3.qqqrst.com/202308/22/XgNv3tZwr52/video/index.m3u8'
SECOND = 'https://v6.1080pzy.co/20220806/2Ksub6f4/index.m3u8'
PLAYLIST = b'#EXTM3U\n#EXTINF:5,\nseg.ts\n'


class Armed(unittest.TestCase):
    """The switches read from a database of the test's own."""

    def setUp(self):
        self.conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        connect = patch.object(db, 'connect', return_value=self.conn)
        connect.start()
        self.addCleanup(connect.stop)
        for clear in (db._settings.clear, line_memory.clear, security._extra_media_hosts.clear, trials.disarm):
            clear()
            self.addCleanup(clear)
        trials.arm()

    def counts(self, name):
        row = next(item for item in trials.listing() if item['id'] == name)
        return row['used'], row['fallbacks']


class SwitchTests(Armed):
    def test_every_trial_is_off_until_switched_on_and_off_again_on_request(self):
        self.assertTrue(trials.listing())
        self.assertFalse(any(item['on'] for item in trials.listing()))
        trials.switch('line_memory', True)
        self.assertTrue(trials.on('line_memory'))
        self.assertEqual([item['id'] for item in trials.listing() if item['on']], ['line_memory'])
        trials.switch('line_memory', False)
        self.assertFalse(trials.on('line_memory'))

    def test_nothing_is_on_before_the_server_arms_the_switches(self):
        trials.switch('line_memory', True)
        trials.disarm()
        with patch.object(db, 'get_setting', side_effect=AssertionError('the database was read')):
            self.assertFalse(trials.on('line_memory'))
            self.assertEqual(trials.attempt('line_memory', lambda: 'new', lambda: 'old'), 'old')

    def test_unknown_and_foreign_trials_are_refused(self):
        with self.assertRaises(security.UnsafeURL):
            trials.switch('no_such_trial', True)
        with patch.object(trials, 'SOURCES', ('anigamer',)):
            self.assertEqual(trials.listing(), [])
            with self.assertRaises(security.UnsafeURL):
                trials.switch('line_memory', True)

    def test_the_settings_route_lists_and_switches_trials(self):
        saved = main.put_settings(main.SettingsIn(trials={'line_memory': True}))
        self.assertEqual([trial.id for trial in saved.trials if trial.on], ['line_memory'])
        self.assertEqual([trial.id for trial in main.get_settings().trials if trial.on], ['line_memory'])
        with self.assertRaises(main.HTTPException) as refused:
            main.put_settings(main.SettingsIn(trials={'no_such_trial': True}))
        self.assertEqual(refused.exception.status_code, 400)

    def test_a_switch_that_cannot_be_read_is_off(self):
        trials.switch('line_memory', True)
        with patch.object(db, 'get_setting', side_effect=sqlite3.OperationalError('locked')):
            self.assertFalse(trials.on('line_memory'))

    def test_attempt_uses_the_earlier_way_when_off_and_when_the_new_one_fails(self):
        def broken():
            raise RuntimeError('a title that must not be logged')
        self.assertEqual(trials.attempt('line_memory', broken, lambda: 'old'), 'old')
        self.assertEqual(self.counts('line_memory'), (0, 0))
        trials.switch('line_memory', True)
        self.assertEqual(trials.attempt('line_memory', lambda: 'new', lambda: 'old'), 'new')
        with self.assertLogs('backend.trials', 'WARNING') as logs:
            self.assertEqual(trials.attempt('line_memory', broken, lambda: 'old'), 'old')
        self.assertIn('RuntimeError', logs.output[0])
        self.assertNotIn('title', logs.output[0])
        self.assertEqual(self.counts('line_memory'), (1, 1))

    def test_counts_are_saved_at_intervals_and_read_back_at_start_up(self):
        with patch.object(trials, '_SAVE_EVERY', 0):
            trials.count('line_memory')
            trials.count('line_memory', fell_back=True)
        trials.disarm()
        trials.arm()
        self.assertEqual(self.counts('line_memory'), (1, 1))
        db.set_setting('trial_counts', '{"line_memory": {"used": "many"}, "x": 3}')
        trials.arm()
        self.assertEqual(self.counts('line_memory'), (0, 0))


class LineMemoryTests(Armed):
    def serve(self, bad):
        def response(url, **kwargs):
            return SimpleNamespace(url=url, content=b'' if url in bad else PLAYLIST, close=lambda: None)
        return patch.object(hls_proxy.http_client, 'fetch_bytes', side_effect=response)

    def test_off_keeps_the_sites_order_and_remembers_nothing(self):
        line_memory.played('chinaq', 'cn-1', 'v6.1080pzy.co')
        self.assertEqual(line_memory.first('chinaq', 'cn-1', [FIRST, SECOND], line_memory.host), [FIRST, SECOND])
        trials.switch('line_memory', True)
        self.assertEqual(line_memory.first('chinaq', 'cn-1', [FIRST, SECOND], line_memory.host), [FIRST, SECOND])

    def test_the_line_that_played_is_tried_first_next_time_for_that_show_only(self):
        trials.switch('line_memory', True)
        with patch.object(security, '_assert_not_private'), self.serve({FIRST}) as fetch:
            self.assertEqual(chinaq._pick_stream([FIRST, SECOND], 'cn-1'), hls_proxy.proxied_media(SECOND))
            self.assertEqual(fetch.call_count, 2)
            fetch.reset_mock()
            self.assertEqual(chinaq._pick_stream([FIRST, SECOND], 'cn-1'), hls_proxy.proxied_media(SECOND))
            self.assertEqual([call.args[0] for call in fetch.call_args_list], [SECOND])
            fetch.reset_mock()
            chinaq._pick_stream([FIRST, SECOND], 'cn-2')
            self.assertEqual(fetch.call_args_list[0].args[0], FIRST)
        self.assertEqual(self.counts('line_memory'), (1, 0))

    def test_a_remembered_line_that_stops_working_gives_way_to_the_next(self):
        trials.switch('line_memory', True)
        line_memory.played('chinaq', 'cn-1', 'v6.1080pzy.co')
        with patch.object(security, '_assert_not_private'), self.serve({SECOND}) as fetch:
            self.assertEqual(chinaq._pick_stream([FIRST, SECOND], 'cn-1'), hls_proxy.proxied_media(FIRST))
            self.assertEqual([call.args[0] for call in fetch.call_args_list], [SECOND, FIRST])
        self.assertEqual(self.counts('line_memory'), (0, 1))
        self.assertEqual(line_memory.first('chinaq', 'cn-1', [SECOND, FIRST], line_memory.host), [FIRST, SECOND])

    def test_memory_runs_out_and_stays_bounded(self):
        trials.switch('line_memory', True)
        line_memory.played('gimy', '1', '7')
        with patch.object(line_memory.time, 'monotonic', return_value=line_memory.time.monotonic() + line_memory._KEEP + 1):
            self.assertEqual(line_memory.first('gimy', '1', ['3', '7'], str), ['3', '7'])
        for number in range(line_memory._MAX + 5):
            line_memory.played('gimy', str(number), '7')
        self.assertEqual(len(line_memory._lines), line_memory._MAX)

    def test_gimy_opens_the_remembered_route_first(self):
        def route(sid, title):
            links = ''.join(f'<a class="ep" href="/play/485591-{sid}-{e}.html">{e}</a>' for e in (1, 2))
            return f'<div class="route-title">{title}</div><div class="eps episodes-route" data-route-sid="{sid}">{links}</div>'
        opened = []

        def fake_get(path, **kwargs):
            if path.startswith('/detail/'):
                return '<h1>測試劇</h1>' + route(3, '雲一') + route(9, '雲二')
            opened.append(path)
            return '<script>var player_data={"from":"x","encrypt":0,"url":"https://vv.jisuzyv.com/play/x/index.m3u8"};</script>'

        def check(master, deadline, validate):
            if opened[-1].startswith('/play/485591-3-') and broken:
                raise ValueError('empty playlist')
        broken = [True]
        trials.switch('line_memory', True)
        with patch.object(gimy, '_get', side_effect=fake_get), patch.object(gimy, 'dlna_media_url', side_effect=check), \
                patch.object(gimy, 'proxied_media', side_effect=lambda u: '/api/hls?u=' + u):
            gimy.fetch_video('485591', ep='1')
            self.assertEqual(opened, ['/play/485591-3-1.html', '/play/485591-9-1.html'])
            opened.clear()
            gimy.fetch_video('485591', ep='2')
            self.assertEqual(opened, ['/play/485591-9-2.html'])
            trials.switch('line_memory', False)
            opened.clear()
            broken.clear()
            gimy.fetch_video('485591', ep='2')
            self.assertEqual(opened, ['/play/485591-3-2.html'])


class HongguoHostTests(Armed):
    NEW = 'https://hn.new-line.example/20261113/abc/index.m3u8'

    def serve(self, playlist):
        return patch.object(hls_proxy.http_client, 'fetch_bytes',
                            side_effect=lambda url, **kwargs: SimpleNamespace(url=url, content=playlist, close=lambda: None))

    def test_an_unlisted_host_is_refused_while_the_trial_is_off(self):
        with patch.object(security, '_assert_not_private'):
            self.assertEqual(hongguo._hls(self.NEW), '')
        self.assertEqual(security.media_host_source('hn.new-line.example'), '')

    def test_the_trial_accepts_the_playlist_host_the_player_page_names(self):
        trials.switch('hongguo_hosts', True)
        with patch.object(security, '_assert_not_private'), self.serve(PLAYLIST) as fetch:
            self.assertEqual(hongguo._hls(self.NEW), hls_proxy.proxied_media(self.NEW))
            self.assertEqual([call.args[0] for call in fetch.call_args_list], [self.NEW])
            self.assertEqual(hongguo._hls('https://s3.bfllvip.com/a/index.m3u8'),
                             hls_proxy.proxied_media('https://s3.bfllvip.com/a/index.m3u8'))
        self.assertEqual(security.media_host_source('hn.new-line.example'), 'hongguo')
        self.assertEqual(hls_proxy._media_context(self.NEW), ('https://www.hongguoapp.cn/', 'chrome131'))
        self.assertEqual(self.counts('hongguo_hosts'), (1, 0))

    def test_a_host_whose_playlist_names_media_we_refuse_is_not_accepted(self):
        trials.switch('hongguo_hosts', True)
        for playlist in (b'#EXTM3U\n#EXTINF:5,\nhttps://ts.other.example:65/a/0.ts\n', b'', b'<html>blocked</html>'):
            with self.subTest(playlist=playlist), patch.object(security, '_assert_not_private'), self.serve(playlist):
                self.assertEqual(hongguo._hls(self.NEW), '')
        self.assertEqual(self.counts('hongguo_hosts'), (0, 3))
        self.assertEqual(security.media_host_source('hn.new-line.example'), '')
        self.assertNotIn('hn.new-line.example', security._extra_media_hosts)

    def test_the_trial_still_refuses_what_is_not_a_public_https_playlist(self):
        trials.switch('hongguo_hosts', True)
        refused = ['http://hn.new-line.example/a/index.m3u8', 'https://hn.new-line.example/a/page.html',
                   'https://hn.new-line.example:8443/a/index.m3u8', 'https://user@hn.new-line.example/a/index.m3u8',
                   'https://127.0.0.1/a/index.m3u8', 'https://localhost/a/index.m3u8']
        for url in refused:
            with self.subTest(url=url):
                self.assertEqual(hongguo._hls(url), '')
        self.assertEqual(self.counts('hongguo_hosts')[0], 0)


if __name__ == '__main__':
    unittest.main()
