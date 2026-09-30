import unittest
from unittest.mock import Mock, patch
from backend import offline, security
from tests import test_offline


class OfflineCdnResumeTests(unittest.TestCase):
    setUp = test_offline.OfflineTests.setUp

    def prepare(self):
        work = offline.ROOT / 'parts'; work.mkdir()
        first_url, second_url = ['https://child.offline.invalid/' + name for name in ('a.ts', 'b.ts')]
        plan = dict(resources=[[first_url, '000000.ts'], [second_url, '000001.ts']], lines=['#EXTM3U', '#EXTINF:10,', '000000.ts', '#EXTINF:10,', '000001.ts', '#EXT-X-ENDLIST'])
        offline._write(work / 'plan.json', plan)
        first = work / '000000.ts'; first.write_bytes(b'already downloaded')
        offline._write(first.with_name(first.name + '.download.json'), dict(url=first_url, complete=True, size=first.stat().st_size, sha256=offline._file_hash(first)))
        return work, plan, second_url

    def test_saved_partial_plan_revalidates_parent_to_restore_child_cdn_after_restart(self):
        work, plan, second_url = self.prepare()
        def validate(url, folder, height, retry):
            self.assertEqual(url, test_offline.URL)
            security.remember_media_host('child.offline.invalid', source='mmov')
            return plan
        with patch.object(offline, '_hls_plan', side_effect=validate) as validate, patch.object(offline, '_download') as download:
            _, duration = offline._hls(test_offline.URL, work, offline.ROOT, 720, Mock())
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(download.call_args.args[0], second_url)
        self.assertEqual((work / '000000.ts').read_bytes(), b'already downloaded')
        self.assertEqual(offline._read(work / 'plan.json'), plan)
        self.assertEqual(duration, 20)

    def test_parent_does_not_authorize_unrelated_saved_hosts(self):
        work, plan, _ = self.prepare()
        with patch.object(offline, '_hls_plan', return_value=plan) as validate, patch.object(offline, '_download') as download, self.assertRaises(security.UnsafeURL):
            offline._hls(test_offline.URL, work, offline.ROOT, 720, Mock())
        self.assertEqual(validate.call_count, 1)
        download.assert_not_called()


if __name__ == '__main__': unittest.main()
