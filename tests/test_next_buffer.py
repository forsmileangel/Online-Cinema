import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from starlette.requests import Request
from backend import cast_session, hls_proxy, http_client, next_buffer as nb, security
from backend.models import VideoDetail

URL = 'https://buffer.example/ep2/index.m3u8'
PROXY = '/api/hls?u=https%3A%2F%2Fbuffer.example%2Fep2%2Findex.m3u8'


def request(method='GET', query=b'', headers=None):
    return Request(dict(type='http', method=method, path='/api/hls', scheme='http',
                        server=('127.0.0.1', 6970), headers=headers or [], query_string=query))


class NextBufferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for obj, name, value in [(nb, '_jobs', {}), (nb, '_started', True), (nb, '_foreground', 0),
                                  (nb, '_last_foreground', 0), (security, '_extra_media_hosts', {}), (security, '_extra_media_sources', {})]:
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(nb.settings,'app_dir',return_value=Path(self.tmp.name));p.start();self.addCleanup(p.stop)
        p=patch.object(security,'_assert_not_private');p.start();self.addCleanup(p.stop)
        security.remember_media_host('buffer.example',source='mmov')

    def job(self, ready=True):
        nb.update('test', PROXY, ready)
        return nb._jobs['test']

    def leaf(self, count=70, seconds=10):
        return '#EXTM3U\n'+''.join(f'#EXTINF:{seconds},\n{i}.ts\n' for i in range(count))+'#EXT-X-ENDLIST\n'

    def response(self, data=b'\x47' * 188):
        return Mock(status_code=200, iter_content=Mock(return_value=[data]))

    def test_ten_minutes_are_downloaded_and_survive_memory_cache_eviction(self):
        job=self.job()
        with patch.object(hls_proxy,'_read_playlist',return_value=(self.leaf(),URL)), patch.object(http_client,'fetch_bytes',return_value=self.response()) as fetch:
            for _ in range(61):nb._work(job)
        self.assertEqual((job.seconds,job.phase,job.index),(600,'complete',60))
        self.assertEqual(fetch.call_count,60)
        with patch.object(hls_proxy.seg_cache,'get',return_value=None), patch.object(http_client,'fetch_bytes') as fetch:
            response=hls_proxy.serve_media(request(),'https://buffer.example/ep2/0.ts')
        self.assertEqual(response.body,b'\x47'*188)
        fetch.assert_not_called()

    def test_short_episode_preloads_whole_duration(self):
        job=self.job()
        with patch.object(hls_proxy,'_read_playlist',return_value=(self.leaf(3,8),URL)), patch.object(http_client,'fetch_bytes',return_value=self.response()):
            for _ in range(4):nb._work(job)
        self.assertEqual((job.seconds,job.target,job.phase),(24,24,'complete'))

    def test_buffering_foreground_and_missing_heartbeat_stop_background_reads(self):
        job=self.job(False)
        job.items=[('https://buffer.example/one.ts',10)]
        with patch.object(http_client,'fetch_bytes') as fetch:
            nb._work(job)
            nb.update('test',PROXY,True)
            nb.foreground(True);nb._work(job);nb.foreground(False)
            job.touched=time.monotonic()-13
            nb._work(job)
        fetch.assert_not_called()

    def test_cancellation_during_download_discards_partial_segment(self):
        job=self.job();job.items=[('https://buffer.example/one.ts',10)]
        def chunks(**kwargs):
            yield b'first'
            nb.cancel('test')
            yield b'second'
        response=self.response();response.iter_content.side_effect=chunks
        with patch.object(http_client,'fetch_bytes',return_value=response):nb._work(job)
        self.assertIsNone(nb.cached(job.items[0][0]))
        self.assertEqual(job.seconds,0)
        response.close.assert_called_once()

    def test_rejection_stops_without_auto_retry_or_next_segment(self):
        job=self.job();job.items=[('https://buffer.example/one.ts',10),('https://buffer.example/two.ts',10)]
        with patch.object(http_client,'fetch_bytes',side_effect=security.SiteBusy('Source',429,30)) as fetch:
            nb._work(job);nb._work(job)
            nb.update('test',PROXY,True)
            nb._work(job)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(job.phase,'error')
        nb.update('test',PROXY,True,retry=True)
        self.assertIsNot(nb._jobs['test'],job)

    def test_vod_keys_and_maps_are_included_but_byterange_and_live_are_not(self):
        text='#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.key"\n#EXT-X-MAP:URI="init.m4s"\n#EXTINF:8,\n0.m4s\n#EXT-X-ENDLIST\n'
        job=self.job()
        with patch.object(hls_proxy,'_read_playlist',return_value=(text,URL)):nb._prepare(job)
        self.assertEqual([x[1] for x in job.items],[0,0,8])
        for text in [self.leaf().replace('#EXT-X-ENDLIST',''),self.leaf()+'#EXT-X-BYTERANGE:200@0']:
            with patch.object(hls_proxy,'_read_playlist',return_value=(text,URL)),self.assertRaises(ValueError):nb._prepare(self.job())

    def test_selected_quality_uses_real_child_and_preserves_redirect_base(self):
        job=self.job();job.height=720
        master='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=10,RESOLUTION=1920x1080\nhi.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=5,RESOLUTION=1280x720\nlo.m3u8\n'
        base='https://buffer.example/redirect/lo.m3u8'
        with patch.object(hls_proxy,'_read_playlist',side_effect=[(master,URL),(self.leaf(1),base)]) as read:
            nb._prepare(job)
        self.assertTrue(read.call_args.args[0].endswith('/lo.m3u8'))
        saved=nb.cached('https://buffer.example/ep2/lo.m3u8').decode()
        self.assertIn('https://buffer.example/redirect/0.ts',saved)

    def test_security_is_not_bypassed_by_cached_media(self):
        with self.assertRaises(security.UnsafeURL):nb.update('x','/api/hls?u=http://127.0.0.1/a.m3u8',True)
        nb._store('https://buffer.example/0.ts',b'valid')
        with patch.object(security,'_assert_not_private',side_effect=security.UnsafeURL('private')):
            with self.assertRaises(security.UnsafeURL):hls_proxy.serve_media(request(),'https://buffer.example/0.ts')
        self.assertEqual(nb._foreground,0)

    def test_completed_cache_eviction_is_reported_and_can_be_retried(self):
        job=self.job();url='https://buffer.example/0.ts'
        nb._store(url,b'segment');job.items=[(url,600)];job.index=1;job.seconds=600;job.phase='complete'
        self.assertEqual(nb.status('test')['phase'],'complete')
        nb._path(url).unlink()
        self.assertEqual(nb.status('test')['phase'],'error')
        nb.update('test',PROXY,True,retry=True)
        self.assertEqual(nb.status('test')['seconds'],0)

    def test_disk_ttl_and_byte_limit(self):
        with patch.object(nb,'CACHE_BYTES',10):
            nb._store('one',b'123456');time.sleep(0.01);nb._store('two',b'123456')
        self.assertIsNone(nb.cached('one'));self.assertEqual(nb.cached('two'),b'123456')
        path=nb._path('two');os.utime(path,(time.time()-7201,time.time()-7201))
        self.assertIsNone(nb.cached('two'))

    def test_cached_head_range_and_transcoding_reuse_raw_data(self):
        url='https://buffer.example/0.ts';nb._store(url,b'0123456789')
        with patch.object(http_client,'fetch_bytes') as fetch:
            response=hls_proxy.serve_media(request(headers=[(b'range',b'bytes=2-5')]),url)
            self.assertEqual((response.status_code,response.body),(206,b'2345'))
            response=hls_proxy.serve_media(request('HEAD'),url)
            self.assertEqual((response.body,response.headers['content-length']),(b'','10'))
            with patch.object(hls_proxy,'_nesthub_segment',return_value='converted') as transcode:
                self.assertEqual(hls_proxy.serve_media(request(query=b'nesthub=1&web=1'),url),'converted')
                self.assertEqual(transcode.call_args.args[2],b'0123456789')
        fetch.assert_not_called()

    def test_cast_preload_continues_while_paused_and_stops_on_stop(self):
        session=cast_session.PlaybackSession(dict(uuid='fake',source='gimy',video_id='1',autoplay_next=True))
        detail=VideoDetail(id='1',title='Neutral',cover='',playlist=PROXY)
        session.prefetched=('2',time.monotonic(),detail)
        with patch.object(nb,'update') as update, patch.object(nb,'cancel') as cancel:
            session.snapshot.update(phase='playing',playing=True,current_time=40)
            session.warm_next();self.assertTrue(update.call_args.args[2])
            session.snapshot.update(phase='paused',playing=False,paused=True,current_time=0)
            session.warm_next();self.assertTrue(update.call_args.args[2])
            session.snapshot.update(phase='stopped')
            session.warm_next();cancel.assert_called()


if __name__=='__main__':unittest.main()
