import unittest
from unittest.mock import Mock, patch

from backend import catalog, hls_proxy, sites, security
from backend.sites import anigamer as ani
from backend.security import SourceUnavailable


class AnigamerTests(unittest.TestCase):
    def setUp(self):
        ani._jobs.clear()
        ani._ready.clear()
        ani._memo.clear()

    def tearDown(self):
        for job in ani._jobs.values():
            job['session'].close()
        ani._jobs.clear()
        ani._ready.clear()
        ani._memo.clear()

    def job(self, started=None):
        session = Mock()
        ani._jobs['test'] = dict(session=session, device='anonymous', sn='2', video_id='1', sid='3',
                                started=started, expires=200, ready=False)
        return session

    def test_source_and_cdn_context(self):
        self.assertEqual(catalog.normalize_source('anigamer'), 'anigamer')
        self.assertIs(sites.get('anigamer'), ani)
        self.assertEqual(hls_proxy._media_context('https://bahamut.akamaized.net/a.m3u8')[0], ani.ORIGIN + '/')

    def test_key_extension_is_limited_to_the_anigamer_cdn(self):
        with patch.object(security, '_assert_not_private'):
            key='https://bahamut.akamaized.net/a/key.m3u8key'
            self.assertEqual(security.assert_hls_url(key),key)
            with self.assertRaises(security.UnsafeURL):
                security.assert_hls_url('https://example.com/a/key.m3u8key')

    def test_cards_deduplicate_and_filter_invalid_ids(self):
        with patch.object(ani, '_cover', return_value='poster'):
            cards = ani._cards([{'animeSn':12,'title':'動畫'}, {'animeSn':12}, {'animeSn':'../x'}])
        self.assertEqual([(c.id,c.title,c.source) for c in cards], [('12','動畫','anigamer')])

    def test_episode_must_belong_to_series(self):
        info = {'video':{'videoSn':2},'anime':{'episodes':{'0':[{'videoSn':2,'episode':1}]}}}
        with patch.object(ani, '_data', return_value=info):
            self.assertEqual(ani._info('1')[1], '2')
            with self.assertRaises(SourceUnavailable):
                ani._info('1', '99')

    def test_completion_cannot_skip_start_or_wait(self):
        with patch.object(ani.time, 'monotonic', return_value=100):
            session=self.job()
            with self.assertRaises(SourceUnavailable):
                ani.ad_event('test', 'complete')
            ani._jobs['test']['started']=80
            with self.assertRaises(SourceUnavailable):
                ani.ad_event('test', 'complete')
            session.get.assert_not_called()

    def test_start_and_completion_are_idempotent_and_cache_ready_source(self):
        source={'data':{'srcUseCases':[{'deviceType':1,'src':{'playlist':'https://bahamut.akamaized.net/a.m3u8'}}]}}
        with patch.object(ani.time,'monotonic',return_value=100), patch.object(ani,'_json',return_value=source), \
             patch.object(ani,'assert_https_url'), patch.object(ani,'proxied_media',return_value='/api/hls?u=valid'):
            session=self.job()
            ani.ad_event('test','start')
            ani.ad_event('test','start')
            self.assertEqual(session.get.call_count,1)
            ani._jobs['test']['started']=60
            self.assertTrue(ani.ad_event('test','complete')['ready'])
            self.assertTrue(ani.ad_event('test','complete')['ready'])
            self.assertEqual(session.get.call_count,2)
            self.assertEqual(ani._ready[('1','2')][1],'/api/hls?u=valid')

    def test_upstream_failure_never_creates_ready_source(self):
        with patch.object(ani.time,'monotonic',return_value=100), patch.object(ani,'_json',side_effect=SourceUnavailable('拒絕')):
            self.job(started=60)
            with self.assertRaises(SourceUnavailable):
                ani.ad_event('test','complete')
            self.assertEqual(ani._ready,{})

    def test_cancel_and_expiry_close_anonymous_sessions(self):
        with patch.object(ani.time,'monotonic',return_value=100):
            session=self.job()
            ani.ad_event('test','cancel')
            session.close.assert_called_once()
            session=self.job()
            ani._jobs['test']['expires']=99
            ani._purge()
            session.close.assert_called_once()
            self.assertFalse(ani._jobs)


if __name__ == '__main__':
    unittest.main()
