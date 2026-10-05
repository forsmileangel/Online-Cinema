import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import hls_proxy, next_buffer, offline, security
from backend.models import Episode, VideoDetail

URL = 'https://offline.example/index.m3u8'
MP4 = '/api/hls?u=https%3A%2F%2Foffline.example%2Fsource.mp4'


class OfflinePriorityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for obj, name, value in [(offline, 'ROOT', Path(tmp.name)), (offline, '_queue', queue.Queue()),
                                 (offline, '_started', True), (offline, '_maintenance_started', True),
                                 (offline, '_playback_parts', {}), (security, '_extra_media_hosts', {}),
                                 (security, '_extra_media_sources', {})]:
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(security, '_assert_not_private'); p.start(); self.addCleanup(p.stop)
        p = patch.object(next_buffer.settings, 'app_dir', return_value=Path(tmp.name) / 'cache'); p.start(); self.addCleanup(p.stop)
        security.remember_media_host('offline.example', source='mmov')
        self.work = offline.ROOT / 'parts'
        self.work.mkdir()

    def download_episode(self, idle, segments=2, download=None, sleep=None, progress=None):
        state = {'active': 0, 'peak': 0, 'started': [], 'done': []}
        lock = threading.Lock()
        def playback_idle(seconds):
            self.assertEqual(seconds, offline.PLAYBACK_IDLE)
            return idle()
        def fetch(remote, *args, **kwargs):
            name = remote.rsplit('/', 1)[-1]
            with lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
                state['started'].append(name)
            try:
                if download:
                    download(name)
                with lock:
                    state['done'].append(name)
            finally:
                with lock:
                    state['active'] -= 1
        playlist = '#EXTM3U\n' + ''.join(f'#EXTINF:10,\ns{i}.ts\n' for i in range(segments)) + '#EXT-X-ENDLIST\n'
        with patch.object(hls_proxy, '_read_playlist', return_value=(playlist, URL)), \
                patch.object(offline, '_download', side_effect=fetch), \
                patch.object(next_buffer, 'playback_idle', side_effect=playback_idle), \
                patch.object(offline.time, 'sleep', side_effect=sleep or (lambda seconds: None)):
            offline._hls(URL, self.work, offline.ROOT, 720, progress or Mock())
        return state

    def test_no_segment_starts_while_playback_is_buffering(self):
        lock, flag = threading.Lock(), {'idle': False, 'sleeps': 0}
        def sleep(seconds):
            with lock:
                flag['sleeps'] += 1
                flag['idle'] = flag['sleeps'] >= 3
        def download(name):
            self.assertTrue(flag['idle'], 'a segment started while media was buffering')
        state = self.download_episode(lambda: flag['idle'], 4, download, sleep)
        self.assertEqual(sorted(state['done']), [f's{i}.ts' for i in range(4)])

    def test_playing_media_leaves_one_connection_that_cannot_stall_forever(self):
        clock, lock = [0.0], threading.Lock()
        def sleep(seconds):
            with lock:
                clock[0] += seconds
        with patch.object(offline.time, 'monotonic', side_effect=lambda: clock[0]):
            state = self.download_episode(lambda: False, 6, lambda name: threading.Event().wait(.02), sleep)
        self.assertEqual(state['peak'], 1)
        self.assertEqual(sorted(state['done']), [f's{i}.ts' for i in range(6)])

    def test_idle_downloads_use_three_connections(self):
        barrier, lock, first = threading.Barrier(offline.PARALLEL_SEGMENTS, timeout=5), threading.Lock(), []
        def download(name):
            with lock:
                first.append(name)
                wait = len(first) <= offline.PARALLEL_SEGMENTS
            if wait:
                barrier.wait()
        progress = Mock()
        state = self.download_episode(lambda: True, 6, download, progress=progress)
        self.assertEqual(state['peak'], offline.PARALLEL_SEGMENTS)
        self.assertEqual(sorted(state['done']), [f's{i}.ts' for i in range(6)])
        self.assertEqual(progress.call_args.args, (100.0,))

    def test_rejection_stops_new_requests_but_keeps_segments_in_flight(self):
        lock, started, finished = threading.Lock(), [], []
        def download(name):
            with lock:
                started.append(name)
            if name == 's1.ts':
                threading.Event().wait(.05)
                raise offline.RetryLater('source is rate limiting')
            threading.Event().wait(.3)
            with lock:
                finished.append(name)
        with self.assertRaises(offline.RetryLater):
            self.download_episode(lambda: True, 6, download)
        self.assertIn('s1.ts', started)
        self.assertLessEqual(len(started), offline.PARALLEL_SEGMENTS)
        self.assertEqual(sorted(finished), sorted(name for name in started if name != 's1.ts'))

    def test_failed_connection_stops_sibling_retries(self):
        offline._lane.stop = stop = threading.Event()
        self.addCleanup(setattr, offline._lane, 'stop', None)
        stop.set()
        with patch.object(offline.http_client, 'fetch_bytes') as fetch, self.assertRaises(offline._LaneStopped):
            offline._download(URL.replace('index.m3u8', 'a.ts'), offline.ROOT / 'a.ts', offline.ROOT)
        fetch.assert_not_called()
        with self.assertRaises(offline._LaneStopped):
            offline._wait_retry(offline.ROOT, 5)

    def test_cancel_is_honoured_while_waiting_for_playback(self):
        def sleep(seconds):
            (offline.ROOT / 'cancel').write_text('stop', encoding='utf-8')
        with self.assertRaises(offline.Cancelled):
            self.download_episode(lambda: False, 3, sleep=sleep)

    def test_progress_status_writes_are_throttled(self):
        job = offline.enqueue('gimy', 'test', '1')
        _, handle = offline._queue.get()
        detail = VideoDetail(id='test', source='gimy', title='本地測試', cover='', playlist=MP4,
                             resolved_episode_id='1', episodes=[Episode(id='1', title='第1集', playlist=MP4)])
        body = [b'x'] * 1000
        response = Mock(status_code=200, headers={'content-length': str(len(body)), 'etag': '"v1"'},
                        iter_content=lambda **kw: iter(body))
        writes = []
        original = offline._write
        def counted(path, value):
            if path.name == 'status.json':
                writes.append(value.get('progress'))
            original(path, value)
        def convert(input_path, output, *args):
            output.write_bytes(input_path.read_bytes())
        try:
            with patch.object(offline, 'fetch_detail', return_value=detail), \
                    patch.object(offline.http_client, 'fetch_bytes', return_value=response), \
                    patch.object(offline, '_convert', side_effect=convert), \
                    patch.object(offline, '_write', side_effect=counted):
                offline._run(job)
        finally:
            offline._release(handle)
        self.assertEqual(offline.status(job['id'])['phase'], 'complete')
        self.assertLess(len(writes), 150)
        self.assertIn(100, writes)
        self.assertEqual(writes, sorted(writes))


if __name__ == '__main__':
    unittest.main()
