import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import main, offline, security
from tests import test_offline

URL = test_offline.URL
CONTENT = 'https://cdn.example/20240728/show/1500kb/hls/'
ADVERT = 'https://ads.example/20261009/promo/10152kb/hls/'
HEADER = ['#EXTM3U', '#EXT-X-VERSION:3', '#EXT-X-TARGETDURATION:10', '#EXT-X-MEDIA-SEQUENCE:0']


def playlist(*blocks):
    """blocks: (base, count, seconds) runs; ('disc',) inserts a discontinuity."""
    lines, resources = list(HEADER), []
    for block in blocks:
        if block == ('disc',):
            lines.append('#EXT-X-DISCONTINUITY')
            continue
        base, count, seconds = block
        for _ in range(count):
            name = f'{len(resources):06d}.ts'
            resources.append([f'{base}{name}', name])
            lines += [f'#EXTINF:{seconds},', name]
    return dict(lines=lines + ['#EXT-X-ENDLIST'], resources=resources)


def names(plan, base):
    return [name for address, name in plan['resources'] if address.startswith(base)]


PROBE_720 = '''Input #0, hls, from 'index.m3u8':
  Duration: 00:47:05.00, start: 1.400000, bitrate: 0 kb/s
  Program 0
  Stream #0:0: Video: h264 (Main) ([27][0][0][0] / 0x001B), yuv420p(progressive), 1280x720 [SAR 1:1 DAR 16:9], 25 fps, 25 tbr, 90k tbn
  Stream #0:1: Audio: aac (LC) ([15][0][0][0] / 0x000F), 48000 Hz, stereo, fltp
'''


class AdvertTests(unittest.TestCase):
    def test_inserted_advert_is_dropped_and_episode_breaks_are_kept(self):
        plan = playlist((CONTENT, 30, 10), ('disc',), (CONTENT, 10, 10), ('disc',), (ADVERT, 9, 2),
                        ('disc',), (CONTENT, 20, 10))
        cleaned = offline._without_ads(plan)
        self.assertEqual(names(cleaned, ADVERT), [])
        self.assertEqual(len(cleaned['resources']), 60)
        lines = cleaned['lines']
        self.assertEqual(lines[:len(HEADER)], HEADER)
        self.assertEqual(lines[-1], '#EXT-X-ENDLIST')
        # The episode's own break stays; the two around the advert become one.
        self.assertEqual(lines.count('#EXT-X-DISCONTINUITY'), 2)
        self.assertFalse(any(line in names(plan, ADVERT) for line in lines))
        self.assertEqual(sum(1 for line in lines if line.startswith('#EXTINF:')), 60)

    def test_repeated_adverts_and_a_leading_one_are_all_dropped(self):
        plan = playlist((ADVERT, 9, 2), ('disc',), (CONTENT, 10, 10), ('disc',), (ADVERT, 9, 2),
                        ('disc',), (CONTENT, 10, 10), ('disc',), (ADVERT, 9, 2))
        cleaned = offline._without_ads(plan)
        self.assertEqual(names(cleaned, ADVERT), [])
        segments = [line for line in cleaned['lines'] if line.startswith(('#EXTINF', '#EXT-X-DISC'))]
        self.assertTrue(segments[0].startswith('#EXTINF'))  # No break before the first segment.
        self.assertEqual(cleaned['lines'].count('#EXT-X-DISCONTINUITY'), 1)

    def test_unclear_playlists_are_left_alone(self):
        cases = {
            'long clip': playlist((CONTENT, 60, 10), ('disc',), (ADVERT, 13, 10), ('disc',), (CONTENT, 60, 10)),
            'too much foreign': playlist((CONTENT, 10, 10), ('disc',), (ADVERT, 50, 2), ('disc',), (CONTENT, 10, 10)),
            'mixed block': playlist((CONTENT, 30, 10), (ADVERT, 9, 2), (CONTENT, 30, 10)),
            'single folder': playlist((CONTENT, 30, 10), ('disc',), (CONTENT, 3, 2), ('disc',), (CONTENT, 30, 10)),
        }
        keyed = playlist((CONTENT, 30, 10), ('disc',), (ADVERT, 9, 2), ('disc',), (CONTENT, 30, 10))
        start = keyed['lines'].index('#EXT-X-DISCONTINUITY') + 1
        keyed['lines'].insert(start, '#EXT-X-KEY:METHOD=NONE')
        cases['encrypted block'] = keyed
        for name, plan in cases.items():
            with self.subTest(name):
                self.assertEqual(offline._without_ads(plan), plan)


class OfflineSpeedAndRecoveryTests(unittest.TestCase):
    setUp = test_offline.OfflineTests.setUp

    def tearDown(self):
        while not offline._queue.empty():
            _, handle = offline._queue.get_nowait()
            offline._release(handle)
            offline._queue.task_done()

    def job(self, episode='1', **values):
        item = offline.enqueue('gimy', 'test', episode)
        item_, handle = offline._queue.get_nowait()
        offline._release(handle); offline._queue.task_done()
        item.update(values)
        offline._write(offline._folder(item['id']) / 'status.json', item)
        return item

    def test_saved_plan_with_advert_resumes_without_it(self):
        base = 'https://offline.example/show/'
        plan = playlist((base, 5, 10), ('disc',), ('https://offline.example/promo/', 3, 2), ('disc',), (base, 5, 10))
        work = offline.ROOT / 'parts'; work.mkdir()
        offline._write(work / 'plan.json', plan)
        (work / '000000.ts').write_bytes(b'started')
        with patch.object(offline, '_hls_plan', side_effect=AssertionError('saved rendition is reused')), \
                patch.object(offline, '_download') as download:
            path, duration = offline._hls(URL, work, offline.ROOT, 720, Mock())
        self.assertEqual(duration, 100)
        fetched = [call.args[0] for call in download.call_args_list]
        self.assertEqual(len(fetched), 10)
        self.assertFalse(any('/promo/' in address for address in fetched))
        saved = offline._read(work / 'plan.json')
        self.assertEqual(len(saved['resources']), 10)
        self.assertNotIn('000005.ts', path.read_text())

    def test_tls_and_dns_failures_are_retried_but_refusals_are_not(self):
        error = offline.http_client.requests.RequestsError
        for retried in (error('Failed to perform, curl: (60) SSL certificate problem', code=60),
                        error('Failed to perform, curl: (35) SSL connect error', code=35),
                        security.HostUnresolvable('dns')):
            self.assertTrue(offline._transient(retried), retried)
        for refused in (security.UnsafeURL('private'), security.SiteBusy('test', 403)):
            self.assertFalse(offline._transient(refused), refused)

    def test_errors_read_in_chinese_including_old_records(self):
        raw = 'Failed to perform, curl: (60) SSL certificate problem: self signed certificate. See https://curl.se/libcurl/c/libcurl-errors.html first for more details.'
        self.assertEqual(offline._friendly(raw), '片源伺服器的安全憑證暫時有問題')
        self.assertEqual(offline._friendly('Failed to perform, curl: (6) Could not resolve host'), '片源網域暫時查不到')
        self.assertEqual(offline._friendly(TimeoutError('timed out')), '連線逾時')
        self.assertEqual(offline._friendly(ValueError('來源拒絕下載（HTTP 403）')), '來源拒絕下載（HTTP 403）')
        self.assertEqual(offline._friendly(ValueError('')), '下載失敗，請重試')
        item = self.job(phase='error', error=raw)
        self.assertEqual(offline.status(item['id'])['error'], '片源伺服器的安全憑證暫時有問題')

    def test_source_outage_is_retried_later_three_times_then_fails(self):
        item = self.job(phase='queued')
        outage = security.SourceUnavailable('MVFFM 暫時找不到可用線路，請稍後重新載入')
        with patch.object(offline, 'fetch_detail', side_effect=outage) as fetch, patch.object(offline.time, 'time', return_value=1000):
            for delay in (600, 1200, 2400):
                offline._run(item)
                state = offline.status(item['id'])
                self.assertEqual((state['phase'], state['retry_at']), ('retrying', 1000 + delay))
                self.assertIn('自動重試', state['error'])
            offline._run(item)
        self.assertEqual(offline.status(item['id'])['phase'], 'error')
        self.assertIn('MVFFM', offline.status(item['id'])['error'])
        self.assertEqual(fetch.call_count, 4)

    def test_deferrals_without_progress_stop_but_progress_resets_the_count(self):
        item = self.job(phase='queued', later_count=offline.LATER_LIMIT, progress=37.5)
        with patch.object(offline, 'fetch_detail', side_effect=TimeoutError('offline')), patch.object(offline, '_wait_retry'):
            offline._run(item)
        state = offline.status(item['id'])
        self.assertEqual(state['phase'], 'error')
        self.assertIn('多次重試', state['error'])

        item = self.job('2', phase='queued', later_count=5, progress=10)
        def advance(url, work, folder, height, progress, on_retry=None):
            progress(50)
            raise offline.RetryLater('稍後續傳', 300)
        with patch.object(offline, 'fetch_detail', return_value=self.detail), patch.object(offline, '_hls', side_effect=advance):
            offline._run(item)
        state = offline.status(item['id'])
        self.assertEqual((state['phase'], state['later_count']), ('retrying', 0))

    def test_encoder_prefers_working_hardware_then_software(self):
        def runner(works):
            return lambda command, **kw: Mock(returncode=0 if any(name in command for name in works) else 1)
        for works, expected in ((('h264_qsv',), 'h264_qsv'), (('h264_nvenc', 'h264_qsv'), 'h264_nvenc'), ((), 'libx264')):
            with self.subTest(expected), patch.object(offline, '_encoder_choice', None), patch.object(offline, '_broken_encoders', set()), \
                    patch.object(offline, '_ffmpeg', return_value='ffmpeg'), patch.object(offline.subprocess, 'run', side_effect=runner(works)) as run:
                self.assertEqual(offline._encoder(), expected)
                self.assertEqual(offline._encoder(), expected)
                self.assertLessEqual(run.call_count, 2)  # Checked once, then remembered.

    def test_conversion_falls_back_to_software_and_remembers_broken_hardware(self):
        info = {'video': 'h264', 'pix_fmt': 'yuv420p', 'height': 1080, 'audio': 'aac', 'duration': 60}
        used = []
        def encode(input_path, output, folder, height, duration, encoder, on_progress=None, strict=False):
            used.append(encoder)
            if encoder == 'h264_nvenc':
                raise ValueError('影片格式無法整理成 MP4，請改選 720p 重試')
        with patch.object(offline, '_broken_encoders', set()) as broken, patch.object(offline, '_probe', return_value=info), \
                patch.object(offline, '_encoder', return_value='h264_nvenc'), patch.object(offline, '_encode', side_effect=encode):
            self.assertEqual(offline._convert(Path('in.m3u8'), offline.ROOT / 'out.mp4', offline.ROOT, 720, 60), 'libx264')
            self.assertEqual(used, ['h264_nvenc', 'libx264'])
            self.assertEqual(broken, {'h264_nvenc'})
        # When software fails too the input is at fault, not the graphics card.
        with patch.object(offline, '_broken_encoders', set()) as broken, patch.object(offline, '_probe', return_value=info), \
                patch.object(offline, '_encoder', return_value='h264_nvenc'), \
                patch.object(offline, '_encode', side_effect=ValueError('影片長度不完整，已保留錯誤供重試')), self.assertRaises(ValueError):
            offline._convert(Path('in.m3u8'), offline.ROOT / 'out.mp4', offline.ROOT, 720, 60)
        self.assertEqual(broken, set())

    def test_streams_that_already_fit_are_repackaged_first(self):
        info = offline._parse_probe(PROBE_720)
        self.assertEqual(info, {'duration': 2825.0, 'video': 'h264', 'height': 720, 'pix_fmt': 'yuv420p', 'audio': 'aac'})
        self.assertTrue(offline._fits(info, 720))
        for change in ({'height': 1080}, {'video': 'hevc'}, {'pix_fmt': 'yuv420p10le'}, {'audio': 'ac3'}):
            self.assertFalse(offline._fits({**info, **change}, 720), change)
        self.assertTrue(offline._fits({k: v for k, v in info.items() if k != 'audio'}, 720))
        used = []
        def encode(input_path, output, folder, height, duration, encoder, on_progress=None, strict=False):
            used.append((encoder, strict))
            if encoder == 'copy':
                raise ValueError('影片時間軸不連續')
        with patch.object(offline, '_probe', return_value=info), patch.object(offline, '_encoder', return_value='libx264'), \
                patch.object(offline, '_encode', side_effect=encode):
            self.assertEqual(offline._convert(Path('in.m3u8'), offline.ROOT / 'out.mp4', offline.ROOT, 720, 0), 'libx264')
        self.assertEqual(used, [('copy', True), ('libx264', False)])
        used.clear()
        with patch.object(offline, '_probe', return_value={**info, 'height': 1080}), patch.object(offline, '_encode', side_effect=encode):
            with self.assertRaises(ValueError):
                offline._convert(Path('in.m3u8'), offline.ROOT / 'out.mp4', offline.ROOT, 0, 0)
        self.assertEqual(used, [('copy', False)])  # Source quality: never re-encoded.

    def test_conversion_progress_and_mode_are_published(self):
        report = offline.ROOT / 'conversion.txt'
        report.write_text('frame=1\nout_time_us=1000000\nprogress=continue\n' * 400 + 'out_time_us=42500000\nprogress=continue\n')
        self.assertEqual(offline._converted_seconds(report), 42.5)
        item = self.job(phase='queued')
        seen = []
        def convert(input_path, output, folder, height, duration, on_progress):
            on_progress(42.0, 'h264_nvenc')
            seen.append(offline._read(folder / 'status.json'))
            output.write_bytes(b'mp4')
            return 'h264_nvenc'
        with patch.object(offline, 'fetch_detail', return_value=self.detail), \
                patch.object(offline, '_hls', return_value=(offline.ROOT / 'index.m3u8', 20)), patch.object(offline, '_convert', side_effect=convert):
            offline._run(item)
        self.assertEqual((seen[0]['phase'], seen[0]['prepare_progress'], seen[0]['prepare_mode']), ('preparing', 42.0, 'gpu'))
        self.assertEqual(offline.status(item['id'])['phase'], 'complete')

    def test_queue_shows_page_names_before_the_source_is_read(self):
        item = offline.enqueue('gimy', 'test', '3', title='本地測試', episode_title='第3集')
        self.assertEqual((item['title'], item['episode_title']), ('本地測試', '第3集'))
        self.assertEqual(offline.status(item['id'])['title'], '本地測試')
        _, handle = offline._queue.get_nowait(); offline._release(handle); offline._queue.task_done()
        # A record queued before names were sent picks them up when resumed.
        old = self.job('4', phase='cancelled', title='test', episode_title='4')
        again = offline.enqueue('gimy', 'test', '4', title='本地測試', episode_title='第4集')
        self.assertEqual((again['id'], again['title'], again['episode_title']), (old['id'], '本地測試', '第4集'))
        with patch.object(offline, 'enqueue', return_value={}) as enqueue:
            main.offline_download(main.OfflineIn(source='gimy', video_id='test', episode='5', title='本地測試', episode_title='第5集'))
        self.assertEqual(enqueue.call_args.kwargs, {'restart': False, 'title': '本地測試', 'episode_title': '第5集'})

    def test_sweep_removes_only_folders_left_with_a_lock(self):
        empty = offline.ROOT / ('a' * 64)
        offline._release(offline._claim(empty))
        busy = offline.ROOT / ('b' * 64)
        offline._release(offline._claim(busy))
        (busy / 'status.json').write_text('{}')
        other = offline.ROOT / 'notes'
        offline._release(offline._claim(other))
        held = offline.ROOT / ('c' * 64)
        handle = offline._claim(held)
        try:
            offline._sweep_empty_folders()
        finally:
            offline._release(handle)
        self.assertFalse(empty.exists())
        self.assertTrue(busy.exists() and other.exists() and held.exists())
        offline._sweep_empty_folders()  # Throttled: another app may still be using it.
        self.assertTrue(held.exists())

    def test_claim_recovers_when_the_folder_is_removed_meanwhile(self):
        folder = offline.ROOT / ('d' * 64)
        real_open, calls = Path.open, []
        def flaky(path, *args, **kwargs):
            if path.name == 'worker.lock' and not calls:
                calls.append(path)
                raise FileNotFoundError(path)
            return real_open(path, *args, **kwargs)
        with patch.object(Path, 'open', flaky):
            handle = offline._claim(folder)
        self.assertIsNotNone(handle)
        offline._release(handle)
        self.assertEqual(len(calls), 1)

    def test_listing_reports_free_disk_space(self):
        result = main.offline_list()
        self.assertIsInstance(result['free_bytes'], int)
        self.assertGreater(result['free_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
