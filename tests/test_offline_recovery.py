import queue
import unittest
from unittest.mock import Mock, patch

from backend import offline, security
from tests import test_offline

URL = test_offline.URL


class OfflineRecoveryTests(unittest.TestCase):
    setUp = test_offline.OfflineTests.setUp

    def tearDown(self):
        while not offline._queue.empty():
            _, handle = offline._queue.get_nowait()
            offline._release(handle)
            offline._queue.task_done()

    def interrupted(self, episode='1', phase='downloading', **values):
        item = offline.enqueue('gimy', 'test', episode)
        _, handle = offline._queue.get_nowait()
        offline._release(handle); offline._queue.task_done()
        item.update(phase=phase, progress=37.5, **values)
        offline._write(offline._folder(item['id']) / 'status.json', item)
        return item

    def test_restart_recovers_selected_jobs_in_order_without_browser_or_origin(self):
        first = self.interrupted('1', created=10)
        second = self.interrupted('2', phase='queued', created=20)
        work = offline._folder(first['id']) / 'parts'; work.mkdir()
        (work / 'kept.ts').write_bytes(b'kept')
        with patch.object(offline, 'fetch_detail') as fetch:
            offline._maintenance_once()
            offline._maintenance_once()
        fetch.assert_not_called()
        self.assertEqual([entry[0]['id'] for entry in offline._queue.queue], [first['id'], second['id']])
        self.assertEqual(offline.status(first['id'])['progress'], 37.5)
        self.assertEqual((work / 'kept.ts').read_bytes(), b'kept')

    def test_network_outage_defers_then_resumes_without_exhausting_download(self):
        job = offline.enqueue('gimy', 'test', '1')
        item, handle = offline._queue.get_nowait()
        operation = Mock(side_effect=TimeoutError('offline'))
        try:
            with patch.object(offline, 'fetch_detail', operation), patch.object(offline, '_wait_retry'), patch.object(offline.time, 'time', return_value=1000):
                offline._run(item)
            state = offline.status(job['id'])
            self.assertEqual(state['phase'], 'retrying')
            self.assertEqual(state['retry_at'], 1300)
            self.assertEqual(operation.call_count, 9)
        finally:
            offline._release(handle); offline._queue.task_done()
        # Another episode can run while this one cools down; retry survives restart.
        second = offline.enqueue('gimy', 'test', '2')
        with patch.object(offline.time, 'time', return_value=1299): offline._maintenance_once()
        self.assertEqual([entry[0]['id'] for entry in offline._queue.queue], [second['id']])
        with patch.object(offline.time, 'time', return_value=1300): offline._maintenance_once()
        self.assertEqual([entry[0]['id'] for entry in offline._queue.queue], [second['id'], job['id']])

    def test_cooldown_honors_retry_after_and_refusal_is_not_retried(self):
        for status in (429, 503):
            operation = Mock(side_effect=security.SiteBusy('test', status, 900))
            with patch.object(offline, '_wait_retry') as wait, self.assertRaises(offline.RetryLater) as error:
                offline._retry(operation, offline.ROOT)
            self.assertEqual(error.exception.delay, 900)
            self.assertEqual(operation.call_count, 1)
            wait.assert_not_called()
        for error in (security.SiteBusy('test', 403), security.UnsafeURL('private')):
            operation = Mock(side_effect=error)
            with patch.object(offline, '_wait_retry') as wait, self.assertRaises(type(error)):
                offline._retry(operation, offline.ROOT)
            self.assertEqual(operation.call_count, 1)
            wait.assert_not_called()

    def test_cancel_playback_delete_and_other_app_lock_prevent_recovery(self):
        first = self.interrupted('1', phase='retrying', retry_at=0)
        second = self.interrupted('2', phase='retrying', retry_at=0)
        third = self.interrupted('3')
        fourth = offline.enqueue('gimy', 'test', '4')
        offline.cancel(first['id'])
        offline.playback('gimy', 'test', '2')
        offline.delete([third['id']], {'gimy'})
        with patch.object(offline, '_queue', queue.Queue()):
            offline._maintenance_once()  # Simulate the other app; held lock must win.
            self.assertTrue(offline._queue.empty())
        self.assertEqual(offline.status(first['id'])['phase'], 'cancelled')
        self.assertEqual(offline.status(second['id'])['phase'], 'cancelled')
        self.assertIsNone(offline.status(third['id']))
        self.assertEqual(offline.status(fourth['id'])['phase'], 'queued')

    def test_terminal_errors_and_unsupported_sources_are_not_resurrected(self):
        self.interrupted('1', phase='error', error='來源拒絕下載（HTTP 403）')
        self.interrupted('2', phase='cancelled')
        third = self.interrupted('3')
        with patch.object(offline.catalog, 'SOURCES', ('hongguo',)):
            offline._maintenance_once()
        self.assertTrue(offline._queue.empty())
        offline.playback('gimy', 'test', '1')
        self.assertTrue((offline._folder(offline.identity('gimy', 'test', '1')) / 'cancel').exists())
        offline._maintenance_once()
        self.assertEqual([entry[0]['id'] for entry in offline._queue.queue], [third['id']])

    def test_crash_tail_rolls_back_only_uncheckpointed_bytes_then_uses_range(self):
        path = offline.ROOT / 'source.mp4'
        path.write_bytes(b'01234')
        record = dict(url=URL, etag='"v1"', total=10, complete=False, size=5, sha256=offline._file_hash(path))
        offline._write(path.with_name(path.name + '.download.json'), record)
        with path.open('ab') as output: output.write(b'uncheckpointed')
        tail = Mock(status_code=206, headers={'content-length': '5', 'content-range': 'bytes 5-9/10', 'etag': '"v1"'}, iter_content=lambda **kw: iter([b'56789']))
        with patch.object(offline.http_client, 'fetch_bytes', return_value=tail) as fetch:
            offline._download(URL, path, offline.ROOT, resume_partial=True)
        self.assertEqual(fetch.call_args.kwargs['range_header'], 'bytes=5-')
        self.assertEqual(fetch.call_args.kwargs['if_range'], '"v1"')
        self.assertEqual(path.read_bytes(), b'0123456789')

    def test_new_partial_download_checkpoints_bytes_before_stream_finishes(self):
        path = offline.ROOT / 'source.mp4'
        metadata = path.with_name(path.name + '.download.json')
        def chunks(**kwargs):
            initial = offline._read(metadata)
            self.assertEqual(initial['size'], 0)
            yield b'x' * (4 * 1024 * 1024)
            checkpoint = offline._read(metadata)
            self.assertEqual(checkpoint['size'], path.stat().st_size)
            self.assertEqual(checkpoint['sha256'], offline._file_hash(path))
            yield b'last'
        response = Mock(status_code=200, headers={'content-length': str(4 * 1024 * 1024 + 4), 'etag': '"v1"'}, iter_content=chunks)
        with patch.object(offline.http_client, 'fetch_bytes', return_value=response):
            offline._download(URL, path, offline.ROOT, resume_partial=True)
        self.assertTrue(offline._read(metadata)['complete'])

    def test_fully_saved_inputs_finish_after_restart_without_cdn_or_origin(self):
        for episode, kind in (('1', 'hls'), ('2', 'mp4')):
            job = offline.enqueue('gimy', 'test', episode)
            item, handle = offline._queue.get_nowait()
            folder = offline._folder(item['id']); work = folder / 'parts'; work.mkdir()
            detail = self.detail.model_copy(deep=True)
            url = URL if kind == 'hls' else URL.replace('index.m3u8', 'source.mp4')
            detail.episodes[int(episode) - 1].playlist = '/api/hls?u=' + url
            offline._write(work / 'source.json', {'detail': detail.model_dump()})
            filename = '000000.ts' if kind == 'hls' else 'source.mp4'
            path = work / filename; path.write_bytes(b'complete local input')
            offline._write(path.with_name(path.name + '.download.json'), dict(url=url, complete=True, size=path.stat().st_size, sha256=offline._file_hash(path)))
            if kind == 'hls':
                offline._write(work / 'plan.json', dict(resources=[[url, filename]], lines=['#EXTM3U', '#EXTINF:10,', filename, '#EXT-X-ENDLIST']))
            def convert(input_path, output, *args): output.write_bytes(b'complete mp4')
            try:
                with patch.object(security, '_extra_media_hosts', {}), patch.object(offline, 'fetch_detail', side_effect=AssertionError('origin must not be contacted')), patch.object(offline.http_client, 'fetch_bytes', side_effect=AssertionError('no media download')), patch.object(offline, '_convert', side_effect=convert):
                    offline._run(item)
                self.assertEqual(offline.status(job['id'])['phase'], 'complete', item.get('error'))
            finally:
                offline._release(handle); offline._queue.task_done()


if __name__ == '__main__':
    unittest.main()
