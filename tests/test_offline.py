import asyncio
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from starlette.requests import Request
from backend import offline, security, hls_proxy, cast_session, main, next_buffer
from backend.models import VideoDetail, Episode

URL = 'https://offline.example/index.m3u8'
PROXY = '/api/hls?u=https%3A%2F%2Foffline.example%2Findex.m3u8'


class OfflineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for obj, name, value in [(offline, 'ROOT', Path(tmp.name)), (offline, '_queue', queue.Queue(offline._queue.maxsize)),
                                 (offline, '_started', True), (security, '_extra_media_hosts', {}),
                                 (security, '_extra_media_sources', {})]:
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(security, '_assert_not_private'); p.start(); self.addCleanup(p.stop)
        p = patch.object(next_buffer.settings, 'app_dir', return_value=Path(tmp.name) / 'cache'); p.start(); self.addCleanup(p.stop)
        security.remember_media_host('offline.example', source='mmov')
        self.detail = VideoDetail(id='test', source='gimy', title='本地測試', cover='', playlist=PROXY,
                                  resolved_episode_id='1', episodes=[Episode(id=str(i), title=f'第{i}集', playlist=PROXY) for i in (1, 2)])

    def ready(self, episode='1'):
        key = offline.identity('gimy', 'test', episode)
        folder = offline._folder(key); folder.mkdir(exist_ok=True)
        item = dict(id=key, source='gimy', video_id='test', episode=episode, title='本地測試', episode_title=f'第{episode}集', height=720)
        (folder / 'video.mp4').write_bytes(b'0123456789')
        offline._write(folder / 'complete.json', dict(item=item, detail=self.detail.model_dump(), size=10))
        return key

    def test_complete_episode_wins_without_source_network_and_other_episode_remains_online(self):
        key = self.ready()
        with patch.object(offline.sites, 'get', side_effect=AssertionError('origin unavailable')):
            detail = offline.fetch_detail('gimy', 'test', '1')
        self.assertEqual(detail.playlist, offline.media_url(key))
        self.assertEqual(detail.episodes[1].playlist, '')
        self.assertIsNone(offline.local_detail('gimy', 'test', '2'))
        self.assertIsNone(offline.local_detail('mmov', 'test', '1'))
        (offline._folder(key) / 'video.mp4').write_bytes(b'broken')
        self.assertIsNone(offline.local_detail('gimy', 'test', '1'))

    def test_local_api_keeps_matching_episode_resume_position(self):
        self.ready('1')
        with patch.object(main.db, 'get_history_item', return_value={'episode_id':'1','position_sec':123}), patch.object(main.db, 'is_favorite', return_value=True), patch.object(offline.sites, 'get', side_effect=AssertionError('origin unavailable')):
            detail=main.video_on_source('gimy','test',None)
        self.assertEqual((detail.resolved_episode_id,detail.position_sec,detail.favorited),('1',123,True))

    def test_reading_status_does_not_download_and_duplicate_is_locked(self):
        self.assertEqual(offline.listing({'gimy'}), [])
        self.assertTrue(offline._queue.empty())
        first = offline.enqueue('gimy', 'test', '1')
        second = offline.enqueue('gimy', 'test', '1')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(offline._queue.qsize(), 1)
        item, handle = offline._queue.get()
        self.assertEqual(offline.status(item['id'])['phase'], 'queued')
        offline._release(handle)
        self.assertEqual(offline.status(item['id'])['phase'], 'error')

    def test_multi_episode_selection_can_queue_more_than_twenty_without_origin_requests(self):
        try:
            with patch.object(offline, 'fetch_detail') as fetch:
                jobs = [offline.enqueue('gimy', 'test', str(ep), 720) for ep in range(1, 26)]
                duplicate = offline.enqueue('gimy', 'test', '1', 720)
            fetch.assert_not_called()
            self.assertEqual(len(jobs), 25)
            self.assertEqual(offline._queue.qsize(), 25)
            self.assertEqual(duplicate['id'], jobs[0]['id'])
            self.assertEqual(offline.status(jobs[-1]['id'])['phase'], 'queued')
        finally:
            while not offline._queue.empty():
                _, handle = offline._queue.get_nowait()
                offline._release(handle)

    def test_cancelled_queue_never_fetches_source(self):
        item = offline.enqueue('gimy', 'test', '2')
        queued, handle = offline._queue.get()
        try:
            offline.cancel(item['id'])
            with patch.object(offline, 'fetch_detail') as fetch:
                offline._run(queued)
            fetch.assert_not_called()
            self.assertEqual(offline.status(item['id'])['phase'], 'cancelled')
            with self.assertRaises(FileNotFoundError): offline.media_path(item['id'])
        finally: offline._release(handle)

    def test_hls_child_quality_keys_redirects_are_local_and_full_episode_is_downloaded(self):
        work = offline.ROOT / 'parts'; work.mkdir()
        master = '#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080\nhi.m3u8\n#EXT-X-STREAM-INF:RESOLUTION=1280x720\nlo.m3u8\n'
        leaf = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="a.key"\n#EXTINF:400,\nfirst.ts\n#EXTINF:400,\nsecond.ts\n#EXT-X-ENDLIST\n'
        with patch.object(hls_proxy, '_read_playlist', side_effect=[(master, URL), (leaf, 'https://offline.example/moved/lo.m3u8')]) as read, patch.object(offline, '_download') as fetch:
            path, duration = offline._hls(URL, work, offline.ROOT, 720, Mock())
        self.assertTrue(read.call_args.args[0].endswith('lo.m3u8'))
        self.assertEqual(duration, 800)
        self.assertEqual(fetch.call_count, 3)
        self.assertNotIn('https:', path.read_text())
        self.assertIn('000000.key', path.read_text())
        self.assertTrue(fetch.call_args.args[0].endswith('/moved/second.ts'))

    def test_refusal_invalid_html_partial_and_private_sources_never_publish(self):
        for data, length in [(b'<html>denied</html>', '19'), (b'partial', '99')]:
            response = Mock(status_code=200, headers={'content-length': length}, iter_content=lambda **kw: iter([data]))
            with patch.object(offline.http_client, 'fetch_bytes', return_value=response), self.assertRaises(ValueError):
                offline._download(URL, offline.ROOT / 'part', offline.ROOT)
        with patch.object(offline.http_client, 'fetch_bytes', side_effect=security.SiteBusy('source', 429, 30)) as fetch, self.assertRaises(security.SiteBusy):
            offline._download(URL, offline.ROOT / 'part', offline.ROOT)
        self.assertEqual(fetch.call_count, 1)
        with self.assertRaises(security.UnsafeURL):
            offline._download('http://127.0.0.1/file', offline.ROOT / 'part', offline.ROOT)
        self.assertEqual(list(offline.ROOT.glob('*/complete.json')), [])

    def test_unsupported_hls_stops_before_downloading(self):
        examples = ['#EXTM3U\n#EXTINF:10,\na.ts\n',
                    '#EXTM3U\n#EXT-X-BYTERANGE:200@0\n#EXT-X-ENDLIST',
                    '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="secret"\n#EXTINF:10,\na.ts\n#EXT-X-ENDLIST']
        for text in examples:
            with patch.object(hls_proxy, '_read_playlist', return_value=(text, URL)), patch.object(offline, '_download') as fetch, self.assertRaises(ValueError):
                offline._hls(URL, offline.ROOT, offline.ROOT, 720, Mock())
            fetch.assert_not_called()

    def test_offline_media_head_range_cors_and_path_validation(self):
        key = self.ready()
        def serve(method='GET', range_header='', client='127.0.0.1', path=None):
            request = Request(dict(type='http', method=method, path=path or offline.media_url(key), scheme='http',
                                   query_string=b'', server=('127.0.0.1', 6970), client=(client, 12345),
                                   headers=[(b'range', range_header.encode())] if range_header else [], asgi={'spec_version': '2.4'}))
            async def run():
                async def endpoint(req): return main.offline_media(key)
                async def cors(req): return await main.media_cors(req, endpoint)
                response = await main.AccessGuard(main.app).dispatch(request, cors)
                messages = []
                async def send(message): messages.append(message)
                async def receive(): return {'type': 'http.request', 'body': b''}
                await response(request.scope, receive, send)
                return messages
            with patch.object(main.db, 'get_setting', return_value='1'), patch.object(main.tv_session, 'valid_cookie', return_value=False):
                return asyncio.run(run())
        head = serve('HEAD')
        self.assertEqual(head[0]['status'], 200)
        self.assertEqual(dict(head[0]['headers'])[b'content-length'], b'10')
        partial = serve(range_header='bytes=2-5')
        self.assertEqual(partial[0]['status'], 206)
        self.assertEqual(b''.join(m.get('body', b'') for m in partial), b'2345')
        self.assertEqual(dict(partial[0]['headers'])[b'access-control-allow-origin'], b'*')
        self.assertIn(b'DLNA.ORG_OP=01', dict(partial[0]['headers'])[b'contentfeatures.dlna.org'])
        self.assertEqual(serve(range_header='bytes=30-')[0]['status'], 416)
        self.assertEqual(serve(client='192.168.1.22', path='/api/offline')[0]['status'], 401)
        self.assertEqual(serve(client='8.8.8.8')[0]['status'], 403)
        with self.assertRaises(HTTPException): main.offline_media('not-valid')
        with self.assertRaises(ValueError): offline.media_path('../outside')
        self.assertIsNone(offline.media_key('https://other' + offline.media_url(key)))

    def test_managed_cast_uses_local_lan_mp4_without_source_or_hls_resolution(self):
        key = self.ready()
        session = cast_session.PlaybackSession(dict(uuid='test-tv', source='gimy', video_id='test', episode_id='1', autoplay_next=False))
        with patch.object(offline.sites, 'get', side_effect=AssertionError('offline')), patch.object(cast_session.cast, 'lan_media_origin', return_value='http://192.168.1.2:6970'), patch.object(cast_session.cast, 'check_media_origin'), patch.object(cast_session.cast, 'session_content', return_value=''), patch.object(cast_session.cast, 'play', return_value={'playing': True, 'duration': 0}) as play:
            session._load('1')
        self.assertEqual(play.call_args.args[:2], ('http://192.168.1.2:6970' + offline.media_url(key), 'video/mp4'))
        self.assertEqual(session.episode_ids, ['1', '2'])
        self.assertEqual(offline.listing({'dramaq'}), [])

    def test_download_reuses_validated_preloaded_bytes(self):
        next_buffer._store(URL, b'cached-segment')
        destination = offline.ROOT / 'segment.ts'
        with patch.object(offline.http_client, 'fetch_bytes') as fetch:
            offline._download(URL, destination, offline.ROOT)
        fetch.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'cached-segment')

    def test_bulk_delete_only_selected_downloads_and_rejects_active_or_foreign_paths(self):
        first, second = self.ready('1'), self.ready('2')
        unrelated = offline.ROOT / 'user-file.txt'; unrelated.write_text('keep')
        result = offline.delete([first, first, '../user-file.txt'], {'gimy'})
        self.assertEqual(result['deleted'], [first])
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual(offline.status(second)['phase'], 'complete')
        with self.assertRaises(FileNotFoundError): offline.media_path(first)
        job = offline.enqueue('gimy', 'test', '3')
        _, handle = offline._queue.get()
        try:
            self.assertEqual(offline.delete([job['id']], {'gimy'})['deleted'], [])
        finally: offline._release(handle)
        self.assertEqual(offline.delete([second], {'dramaq'})['deleted'], [])

    def test_hls_resume_reuses_verified_segments_and_rejects_changed_playlist(self):
        work = offline.ROOT / 'parts'; work.mkdir()
        leaf = '#EXTM3U\n#EXTINF:10,\na.ts\n#EXTINF:10,\nb.ts\n#EXT-X-ENDLIST\n'
        def response(data):
            return Mock(status_code=200, headers={'content-length': str(len(data))}, iter_content=lambda **kw: iter([data]))
        with patch.object(hls_proxy, '_read_playlist', return_value=(leaf, URL)), patch.object(offline.http_client, 'fetch_bytes', side_effect=[response(b'first'), RuntimeError('interrupted')]):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                offline._hls(URL, work, offline.ROOT, 720, Mock())
        self.assertEqual((work / '000000.ts').read_bytes(), b'first')
        progress = Mock()
        with patch.object(hls_proxy, '_read_playlist', return_value=(leaf, URL)), patch.object(offline.http_client, 'fetch_bytes', return_value=response(b'second')) as fetch:
            _, duration = offline._hls(URL, work, offline.ROOT, 720, progress)
        self.assertEqual(duration, 20)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(fetch.call_args.args[0].endswith('/b.ts'))
        progress.assert_called_with(100)
        with patch.object(hls_proxy, '_read_playlist', return_value=(leaf.replace('a.ts', 'new.ts'), URL)), patch.object(offline.http_client, 'fetch_bytes') as fetch:
            with self.assertRaisesRegex(ValueError, '重新下載'):
                offline._hls(URL, work, offline.ROOT, 720, Mock())
            fetch.assert_not_called()
        self.assertEqual((work / '000000.ts').read_bytes(), b'first')
        # A damaged local segment is fetched again, rather than silently reused.
        (work / '000000.ts').write_bytes(b'xxxxx')
        with patch.object(hls_proxy, '_read_playlist', return_value=(leaf, URL)), patch.object(offline.http_client, 'fetch_bytes', return_value=response(b'first')) as fetch:
            offline._hls(URL, work, offline.ROOT, 720, Mock())
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(fetch.call_args.args[0].endswith('/a.ts'))

    def test_mp4_resume_sends_range_and_if_range_and_publishes_only_complete_input(self):
        path = offline.ROOT / 'source.mp4'
        url = 'https://offline.example/source.mp4'
        def interrupted(**kw):
            yield b'01234'
            raise RuntimeError('connection lost')
        first = Mock(status_code=200, headers={'content-length': '10', 'etag': '"v1"'}, iter_content=interrupted)
        with patch.object(offline.http_client, 'fetch_bytes', return_value=first), self.assertRaises(RuntimeError):
            offline._download(url, path, offline.ROOT, resume_partial=True)
        self.assertEqual(path.read_bytes(), b'01234')
        self.assertFalse(offline._read(path.with_name(path.name + '.download.json'))['complete'])
        response = Mock(url=url, status_code=206, headers={'content-length': '5', 'content-range': 'bytes 5-9/10', 'etag': '"v1"'}, iter_content=lambda **kw: iter([b'56789']))
        session = Mock(); session.request.return_value = response
        progress = Mock()
        with patch.object(offline.http_client, 'media_session', return_value=session):
            offline._download(url, path, offline.ROOT, resume_partial=True, progress=progress)
        headers = session.request.call_args.kwargs['headers']
        self.assertEqual(headers['Range'], 'bytes=5-')
        self.assertEqual(headers['If-Range'], '"v1"')
        self.assertEqual(path.read_bytes(), b'0123456789')
        self.assertTrue(offline._read(path.with_name(path.name + '.download.json'))['complete'])
        progress.assert_called_with(100)
        with patch.object(offline.http_client, 'fetch_bytes') as fetch:
            offline._download(url, path, offline.ROOT, resume_partial=True)
            fetch.assert_not_called()

    def test_mp4_unsafe_resume_keeps_partial_file_and_requires_explicit_restart(self):
        path = offline.ROOT / 'source.mp4'
        path.write_bytes(b'01234')
        metadata = path.with_name(path.name + '.download.json')
        record = dict(url=URL, etag='"v1"', total=10, complete=False, size=5, sha256=offline._file_hash(path))
        offline._write(metadata, record)
        for response in [Mock(status_code=200, headers={'content-length': '10', 'etag': '"v2"'}),
                         Mock(status_code=206, headers={'content-range': 'bytes 4-9/10', 'etag': '"v1"'}),
                         Mock(status_code=206, headers={'content-range': 'bytes 5-9/10', 'etag': '"v2"'})]:
            with patch.object(offline.http_client, 'fetch_bytes', return_value=response), self.assertRaisesRegex(ValueError, '重新下載'):
                offline._download(URL, path, offline.ROOT, resume_partial=True)
            self.assertEqual(path.read_bytes(), b'01234')
        offline._write(metadata, {**record, 'url': URL + '?old=1'})
        with patch.object(offline.http_client, 'fetch_bytes') as fetch, self.assertRaisesRegex(ValueError, '重新下載'):
            offline._download(URL, path, offline.ROOT, resume_partial=True)
        fetch.assert_not_called()
        offline._write(metadata, {**record, 'etag': ''})
        with patch.object(offline.http_client, 'fetch_bytes') as fetch, self.assertRaisesRegex(ValueError, '重新下載'):
            offline._download(URL, path, offline.ROOT, resume_partial=True)
        fetch.assert_not_called()

    def test_cancelled_download_keeps_source_parts_until_explicit_restart(self):
        job = offline.enqueue('gimy', 'test', '1')
        _, handle = offline._queue.get()
        folder = offline._folder(job['id'])
        def interrupted(url, work, folder, height, progress):
            (work / '000000.ts').write_bytes(b'part')
            progress(50)
            raise offline.Cancelled()
        try:
            with patch.object(offline, 'fetch_detail', return_value=self.detail), patch.object(offline, '_hls', side_effect=interrupted):
                offline._run(job)
            self.assertEqual(offline.status(job['id'])['phase'], 'cancelled')
            self.assertTrue((folder / 'parts/000000.ts').exists())
        finally: offline._release(handle)
        retry = offline.enqueue('gimy', 'test', '1')
        self.assertEqual(retry['progress'], 50)
        self.assertTrue((folder / 'parts/000000.ts').exists())
        _, handle = offline._queue.get(); offline._release(handle)
        restarted = offline.enqueue('gimy', 'test', '1', restart=True)
        try:
            self.assertEqual(restarted['progress'], 0)
            self.assertFalse((folder / 'parts').exists())
        finally:
            _, handle = offline._queue.get(); offline._release(handle)

    def test_windows_atomic_replace_retries_brief_file_locks(self):
        original = Path.replace
        calls = []
        def replace(path, target):
            calls.append(target)
            if len(calls) < 3: raise PermissionError('temporarily in use')
            return original(path, target)
        with patch.object(Path, 'replace', autospec=True, side_effect=replace), patch.object(offline.time, 'sleep'):
            offline._write(offline.ROOT / 'status.json', {'ready': True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(offline._read(offline.ROOT / 'status.json'), {'ready': True})


if __name__ == '__main__':
    unittest.main()
