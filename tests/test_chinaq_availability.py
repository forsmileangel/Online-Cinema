import unittest
from types import SimpleNamespace
from unittest.mock import patch
from backend import main, security
from backend.sites import chinaq

VID = 'kr-202625953'
TRAILER = '<h1>殭屍校園 第二季</h1><a href="/tv-kr/202625953/yu_gao_pian.html">預告片</a>'
EPISODE = '<h1>show</h1><a href="/tv-kr/202625953/ep1.html">第1集</a>'


class ChinaqAvailabilityTests(unittest.TestCase):
    def test_missing_episodes_do_not_invent_episode_one(self):
        for html, message in ((TRAILER, '只有預告片'), ('<h1>show</h1>', '沒有可播放')):
            with self.subTest(html=html), patch.object(chinaq, '_get', return_value=html), patch.object(chinaq, '_qplays') as fetch:
                with self.assertRaisesRegex(security.SourceUnavailable, message):
                    chinaq.fetch_video(VID)
                fetch.assert_not_called()

    def test_explicit_unlisted_episode_does_not_hit_source(self):
        with patch.object(chinaq, '_get', return_value=EPISODE), patch.object(chinaq, '_qplays') as fetch:
            with self.assertRaisesRegex(security.SourceUnavailable, '尚未提供指定集數'):
                chinaq.fetch_video(VID, ep='99')
            fetch.assert_not_called()

    def test_trailer_link_does_not_hide_published_episodes(self):
        with patch.object(chinaq, '_get', return_value=TRAILER + EPISODE), patch.object(chinaq, '_qplays', return_value=['https://v3.qqqrst.com/x/index.m3u8']), patch.object(chinaq, '_pick_stream', return_value='/api/hls?u=stream'):
            detail=chinaq.fetch_video(VID)
        self.assertEqual(detail.resolved_episode_id, '1')
        self.assertEqual([ep.id for ep in detail.episodes], ['1'])

    def test_api_explains_trailer_only_but_does_not_expose_guard_details(self):
        with patch.object(main.db, 'get_history_item', return_value=None), patch.object(main.sites, 'get', return_value=chinaq), patch.object(chinaq, '_get', return_value=TRAILER), patch.object(chinaq, '_qplays') as fetch:
            with self.assertRaises(main.HTTPException) as caught:
                main.video_on_source('chinaq', VID, ep=None)
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, '此來源目前只有預告片，尚未提供正片集數')
        fetch.assert_not_called()
        self.assertEqual(main._err(security.UnsafeURL('host not allowed'), 502).detail, '請求被拒絕')

    def test_qplays_404_is_availability_not_url_rejection(self):
        response=SimpleNamespace(url='https://chinaq.fun/qplays/202625953/ep1', status_code=404)
        session=SimpleNamespace(get=lambda *args, **kwargs: response)
        with patch.object(chinaq.http_client, 'media_session', return_value=session), patch.object(chinaq, 'final_url_still_allowed'):
            with self.assertRaisesRegex(security.SourceUnavailable, '沒有提供這一集的播放連結'):
                chinaq._qplays('202625953','1')

    def test_unsupported_url_does_not_consume_three_preflight_attempts(self):
        urls=['https://hd.ijycnd.com/play/RdGLN83e/index.m3u8',
              'https://ukzy.ukubf3.com/play/6dBDG6Wa/index.m3u8',
              'https://vv.jisuzyv.com/play/1aKPlQJb/index.m3u8',
              'https://v3.qqqrst.com/202308/21/jvGzK0Wdzh2/video/index.m3u8']
        with patch.object(security,'_assert_not_private'), patch.object(chinaq,'dlna_media_url',side_effect=[security.UnsafeURL('port not allowed'),security.UnsafeURL('port not allowed'),None]) as validate:
            self.assertEqual(chinaq._pick_stream(urls), chinaq.proxied_media(urls[3]))
        self.assertEqual(validate.call_count,3)
        self.assertTrue(all(call.kwargs['validate'] for call in validate.call_args_list))

if __name__=='__main__':
    unittest.main()
