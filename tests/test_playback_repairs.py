"""Targeted regressions for the September source/casting repair."""
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import quote

from starlette.requests import Request
from backend import cast, cast_session, db, hls_proxy, http_client, main, security, sites
from backend.models import Card, Episode, VideoDetail
from backend.sites import chinaq, gimy

FIXTURES = Path(__file__).parent / 'fixtures'
HAS_DRAMA = any(s['id'] == 'dramaq' for s in sites.available())
STREAM = 'https://s2.bfllvip.com/neutral/1.m3u8'

def fixture(name):
    return (FIXTURES/name).read_text(encoding='utf8')

def detail(ep='1'):
    return VideoDetail(id='12345', title='Neutral', cover='', resolved_episode_id=ep,
        playlist='/api/hls?u=' + quote(STREAM.replace('/1.', '/' + ep + '.'), safe=''),
        episodes=[Episode(id=n, title='Episode '+n, playlist='/api/hls?u='+quote(STREAM.replace('/1.', '/'+n+'.'), safe='')) for n in ['1','2','3']])

class SeriesRepairTests(unittest.TestCase):
    def test_each_new_source_supports_switch_and_browserless_next(self):
        for source in ['chinaq','gimy'] + (['dramaq'] if HAS_DRAMA else []):
            with self.subTest(source=source), ExitStack() as stack:
                stack.enter_context(patch.object(security, '_assert_not_private'))
                resolver=Mock(fetch_video=Mock(side_effect=lambda vid, ep=None: detail(ep or '1')))
                stack.enter_context(patch.object(cast_session.sites, 'get', return_value=resolver))
                stack.enter_context(patch.object(cast, 'lan_media_origin', return_value='http://192.168.1.10:6970'))
                stack.enter_context(patch.object(cast, 'check_media_origin'))
                stack.enter_context(patch.object(cast, 'session_content', return_value=''))
                play=stack.enter_context(patch.object(cast, 'play', side_effect=lambda url,*args,**kw: dict(content_id=url, playing=True, paused=False, idle=False, buffering=False, current_time=0, duration=100)))
                session=cast_session.PlaybackSession(dict(uuid='fake',source=source,video_id='12345',episode_id='1',autoplay_next=True))
                session.save=Mock();session.prefetch=Mock()
                session.load('1')
                self.assertEqual(session.episode_ids, ['1','2','3'])
                session.handle('episode', {'episode_id':'2'})
                self.assertEqual(session.get()['episode_id'],'2')
                self.assertIn('Episode 2', play.call_args.args[2])
                idle={**session.previous,'playing':False,'idle':True,'idle_reason':'FINISHED'}
                with patch.object(cast, 'status', return_value=idle):
                    session.tick()
                self.assertEqual(session.get()['episode_id'],'3')
                self.assertEqual(resolver.fetch_video.call_count,3)

    def test_history_selects_before_resolution_and_explicit_episode_does_not_reuse_seconds(self):
        for requested,history,expected,seconds in [(None,{'episode_id':'2','position_sec':42},'2',42),
                ('3',{'episode_id':'2','position_sec':42},'3',0),(None,None,'1',0)]:
            with self.subTest(requested=requested), patch.object(db,'get_history_item',return_value=history), patch.object(db,'is_favorite',return_value=False):
                resolver=Mock(fetch_video=Mock(side_effect=lambda vid,ep=None: detail(ep or '1')))
                with patch.object(main.sites,'get',return_value=resolver):
                    result=main.video_on_source('chinaq','12345',requested)
                self.assertEqual(result.resolved_episode_id,expected)
                self.assertEqual(result.position_sec,seconds)
                self.assertEqual(resolver.fetch_video.call_args.kwargs['ep'], requested or (history or {}).get('episode_id'))

    def test_provider_rejection_does_not_trigger_immediate_session_retry(self):
        session=cast_session.PlaybackSession(dict(uuid='fake',source='gimy',video_id='12345'))
        session._load=Mock(side_effect=security.SiteBusy('Gimy',429,10))
        with self.assertRaises(security.SiteBusy): session.load('1')
        self.assertEqual(session._load.call_count,1)

    @unittest.skipUnless(HAS_DRAMA,'DramaQ is only in Online Cinema')
    def test_dramaq_resolves_first_before_latest_and_does_not_invent_future_episode(self):
        from backend.sites import dramaq
        with patch.object(dramaq,'_get',side_effect=lambda path:fixture('dramaq_detail.html') if path.startswith('/detail/') else fixture('dramaq_play.html')), patch.object(dramaq,'_plays',return_value=['https://hn.bfvvs.com/neutral/index.m3u8']) as plays, patch.object(security,'_assert_not_private'):
            result=dramaq.fetch_video('202690895')
        self.assertEqual(plays.call_args.args[1],'ep1')
        self.assertEqual(result.resolved_episode_id,'ep1')
        self.assertEqual(result.episodes[0].title,'第1集')
        self.assertEqual(result.episodes[-1].id,'ep12')
        self.assertTrue(all(not t.browsable for t in result.genres))

class CdnRepairTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(security,'_extra_media_hosts',{});p.start();self.addCleanup(p.stop)

    def test_domain_boundaries_and_private_addresses(self):
        with patch.object(security,'_assert_not_private'):
            for host in ['ukubf.com','a.ukubf3.com','vv.jisuzyv.com']:
                security.assert_hls_url('https://'+host+'/neutral.m3u8')
            for host in ['unrelatedukubf.com','unrelatedjisuzyv.com','jisuzyv.com.example.org'] + (['unrelatedbfvvs.com'] if HAS_DRAMA else []):
                with self.assertRaises(security.UnsafeURL): security.assert_hls_url('https://'+host+'/neutral.m3u8')
        with patch.object(security.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(security.UnsafeURL): security.assert_hls_url('https://vv.jisuzyv.com/neutral.m3u8')

    def test_dynamic_host_renews_on_success_only_and_expires_when_idle(self):
        url='https://rotating.example.org/a.ts'
        with patch.object(security,'_assert_not_private'),patch.object(security.time,'monotonic',return_value=100) as clock:
            security.remember_media_host('rotating.example.org')
            clock.return_value=3600
            security.assert_hls_url(url)
            self.assertEqual(security._extra_media_hosts['rotating.example.org'],3700)
            response=Mock(status_code=200,url=url,headers={})
            with patch.object(http_client,'media_session',return_value=Mock(request=Mock(return_value=response))):
                http_client.fetch_bytes(url,referer='https://chinaq.fun/',allowed_hosts={'rotating.example.org'})
            clock.return_value=3800
            security.assert_hls_url(url)
            clock.return_value=7201
            with self.assertRaises(security.UnsafeURL): security.assert_hls_url(url)

    def test_lg_and_browser_allow_the_same_verified_cross_host_child(self):
        parent='https://vv.jisuzyv.com/master.m3u8'
        child='https://rotating.example.org/variant.m3u8'
        text='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2"\n'+child+'\n'
        response=Mock(content=text.encode(),url=parent)
        with patch.object(security,'_assert_not_private'), patch.object(http_client,'fetch_bytes',return_value=response):
            url=hls_proxy.dlna_media_url('http://local.test/api/hls?u='+quote(parent,safe=''),time.monotonic()+30)
            self.assertIn(quote(child,safe=''),url)
            security._extra_media_hosts.clear()
            rewritten=hls_proxy.rewrite_playlist(text,parent)
            self.assertIn(quote(child,safe=''),rewritten)
            with self.assertRaises(security.UnsafeURL):
                hls_proxy._playlist_media_url('http://127.0.0.1/a.m3u8','vv.jisuzyv.com')

    def test_structured_upstream_status_and_retry_header_reach_the_api(self):
        for status,word in [(403,'拒絕連線'),(429,'請求過多'),(503,'暫時無法服務')]:
            response=Mock(status_code=status,url='https://gimyai.tw/find/',headers={'retry-after':'12'} if status==429 else {})
            with patch.object(http_client,'final_url_still_allowed'),patch.object(http_client,'media_session',return_value=Mock(get=Mock(return_value=response))):
                with self.assertRaises(security.SiteBusy) as raised:
                    http_client.fetch_html_hosts(response.url,{'gimyai.tw'},impersonate='chrome131')
            error=main._err(raised.exception)
            self.assertEqual(error.status_code,status)
            self.assertIn(word,error.detail)
            self.assertNotIn('十幾秒',error.detail)
            if status==429:self.assertEqual(error.headers['Retry-After'],'12')

    def test_proxy_does_not_retry_rejected_source(self):
        request=Request({'type':'http','method':'GET','path':'/api/hls','scheme':'http','query_string':b'','server':('localhost',6970),'headers':[]})
        with patch.object(security,'_assert_not_private'),patch.object(http_client,'fetch_bytes',side_effect=security.SiteBusy('CDN',429,30)) as fetch:
            with self.assertRaises(security.SiteBusy):hls_proxy.serve_media(request,STREAM)
        self.assertEqual(fetch.call_count,1)

class GimyRepairTests(unittest.TestCase):
    def setUp(self):
        validate = patch.object(gimy, "dlna_media_url")
        validate.start(); self.addCleanup(validate.stop)
        gimy._hot_memo=None;gimy._search_memo.clear();gimy._search_links.clear();gimy._search_inflight.clear();gimy._search_cooldown=None

    def test_bad_or_timed_out_first_route_uses_second(self):
        for failure in ['cdn','timeout']:
            calls=[]
            def get(path,**kwargs):
                calls.append(path)
                if '/detail/' in path:return fixture('gimy_detail.html')
                if '-2-' in path:
                    if failure=='timeout':raise TimeoutError('first route')
                    return '<script>var player_data={"encrypt":0,"url":"https://unlisted.example.org/a.m3u8"};</script>'
                return fixture('gimy_play.html')
            with self.subTest(failure=failure),patch.object(gimy,'_get',side_effect=get),patch.object(security,'_assert_not_private'):
                result=gimy.fetch_video('485760',ep='1')
            self.assertEqual(result.resolved_episode_id,'1')
            self.assertEqual(calls[-1],'/play/485760-7-1.html')
            self.assertEqual(result.genres[0].kind,'cn')
            self.assertEqual(result.genres[0].slug,'13')
            with patch.object(gimy,'_get',return_value=fixture('gimy_list.html')):
                self.assertTrue(gimy.browse(result.genres[0].kind).items)

    def test_route_rejection_stops_at_first_candidate(self):
        with patch.object(gimy,'_get',side_effect=[fixture('gimy_detail.html'),security.SiteBusy('Gimy',403)]) as get:
            with self.assertRaises(security.SiteBusy):gimy.fetch_video('485760')
        self.assertEqual(get.call_count,2)

    def test_route_count_and_total_deadline_are_bounded(self):
        html='<h1>Neutral</h1>'+''.join(f'<div class="episodes-route" data-route-sid="{n}"><a class="ep" href="/play/12345-{n}-1.html">1</a></div>' for n in range(1,6))
        with patch.object(gimy,'_get',side_effect=lambda path,**kw:html if '/detail/' in path else '') as get:
            with self.assertRaises(security.UnsafeURL):gimy.fetch_video('12345')
        self.assertEqual(get.call_count,4)
        with patch.object(gimy.time,'monotonic',side_effect=[0,46]),patch.object(gimy,'_get',return_value=html) as get:
            with self.assertRaises(TimeoutError):gimy.fetch_video('12345')
        self.assertEqual(get.call_count,1)

    def test_busy_search_makes_one_request_and_does_not_cache_false_empty(self):
        with patch.object(gimy,'_get',side_effect=security.SiteBusy('Gimy',403)) as get:
            for _ in range(2):
                with self.assertRaises(security.SiteBusy):gimy.search('Neutral')
        self.assertEqual(get.call_count,1)
        self.assertIsNone(gimy._hot_memo)

    def test_busy_search_uses_existing_catalog_with_notice_without_extra_requests(self):
        gimy._remember_cards([Card(id='12345',title='Neutral',cover='',source='gimy')])
        with patch.object(gimy,'_get',side_effect=security.SiteBusy('Gimy',429,10)) as get:
            result=gimy.search('Neutral')
        self.assertEqual(get.call_count,1)
        self.assertIn('僅搜尋已載入片單',result.notice)
        self.assertEqual(result.items[0].id,'12345')
        self.assertEqual(len(gimy._hot_memo[1]),1)
        # Search cooldown is not a global playback gate.
        with patch.object(gimy,'_get',side_effect=lambda path,**kw:fixture('gimy_detail.html') if '/detail/' in path else fixture('gimy_play.html')),patch.object(security,'_assert_not_private'):
            self.assertTrue(gimy.fetch_video('485760').playlist)

    def test_search_follows_real_next_link_and_never_reuses_page_one(self):
        first=fixture('gimy_list.html')+'<a href="/find/custom-page-two.html?wd=Neutral" rel="next">下一頁</a>'
        second=fixture('gimy_list.html').replace('485760','999999')
        with patch.object(gimy,'_get',side_effect=[first,second]) as get,patch.object(security,'_assert_not_private'):
            a=gimy.search('Neutral');b=gimy.search('Neutral',2);c=gimy.search('Neutral',3)
        self.assertTrue(a.has_next);self.assertFalse(b.has_next)
        self.assertNotEqual(a.items[0].id,b.items[0].id)
        self.assertEqual(get.call_args_list[1].args[0],'/find/custom-page-two.html?wd=Neutral')
        self.assertEqual(get.call_count,2);self.assertEqual(c.items,[])

    def test_concurrent_identical_searches_share_one_upstream_request(self):
        entered=threading.Event();release=threading.Event()
        def get(path):
            entered.set();self.assertTrue(release.wait(2));return fixture('gimy_list.html')
        with patch.object(gimy,'_get',side_effect=get) as fetch,patch.object(security,'_assert_not_private'),ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(gimy.search,'Neutral');self.assertTrue(entered.wait(2))
            b=pool.submit(gimy.search,'Neutral');release.set()
            self.assertEqual(a.result().items,b.result().items)
        self.assertEqual(fetch.call_count,1)

    def test_chinaq_metadata_tags_are_not_broken_links(self):
        with patch.object(chinaq,'_get',return_value=fixture('chinaq_show.html')),patch.object(chinaq,'_qplays',return_value=[STREAM]),patch.object(chinaq,'dlna_media_url'),patch.object(security,'_assert_not_private'):
            result=chinaq.fetch_video('cn-202659534')
        self.assertTrue(result.genres)
        self.assertTrue(all(not t.browsable for t in result.genres))

if __name__=='__main__':unittest.main()
