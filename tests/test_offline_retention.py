import os
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request
from backend import offline, main
from tests import test_offline

DAY = 86400


class OfflineRetentionTests(unittest.TestCase):
    setUp = test_offline.OfflineTests.setUp
    ready = test_offline.OfflineTests.ready

    def complete(self, episode, completed_at=None):
        key = self.ready(episode)
        path = offline._folder(key) / 'complete.json'
        record = offline._read(path)
        if completed_at is not None:
            offline._write(path, {**record, 'completed_at': completed_at})
        return key

    def clean(self, now):
        with patch.object(offline.time, 'time', return_value=now), patch.object(offline, '_cleanup_check_at', 0):
            offline._cleanup_expired()

    def test_only_complete_unwatched_files_older_than_fourteen_days_are_removed(self):
        now = time.time()
        old = self.complete('1', now - 15 * DAY)
        boundary = self.complete('2', now - 14 * DAY)
        recent = self.complete('3', now - 13 * DAY)
        pending = offline.enqueue('gimy', 'test', '4')
        try:
            self.clean(now)
            self.assertIsNone(offline.status(old))
            self.assertEqual(offline.status(boundary)['phase'], 'complete')
            self.assertEqual(offline.status(recent)['phase'], 'complete')
            self.assertEqual(offline.status(pending['id'])['phase'], 'queued')
            self.assertEqual(offline.cleanup_policy()['deleted'], 1)
            offline.cancel(pending['id'])
            self.clean(now + 2 * 3600)
            self.assertEqual(offline.status(pending['id'])['phase'], 'cancelled')
        finally:
            while not offline._queue.empty():
                _, handle = offline._queue.get(); offline._release(handle); offline._queue.task_done()

    def test_actual_playback_and_media_get_refresh_age_but_head_and_metadata_do_not(self):
        now = time.time()
        key = self.complete('1', now - 30 * DAY)
        folder = offline._folder(key)
        offline.fetch_detail('gimy', 'test', '1')
        main.offline_media(key, Request({'type': 'http', 'method': 'HEAD'}))
        self.assertFalse((folder / 'last-played').exists())
        offline.playback('gimy', 'test', '1')
        self.assertTrue((folder / 'last-played').exists())
        self.clean(now)
        self.assertEqual(offline.status(key)['phase'], 'complete')
        os.utime(folder / 'last-played', (now - 20 * DAY, now - 20 * DAY))
        main.offline_media(key, Request({'type': 'http', 'method': 'GET'}))
        self.clean(now + 3601)
        self.assertEqual(offline.status(key)['phase'], 'complete')

    def test_legacy_files_get_one_persisted_fourteen_day_grace_period(self):
        now = time.time()
        key = self.complete('1')
        record = offline._folder(key) / 'complete.json'
        os.utime(record, (now - 100 * DAY, now - 100 * DAY))
        self.clean(now)
        baseline = offline.cleanup_policy()['initialized_at']
        self.assertEqual(offline.status(key)['phase'], 'complete')
        self.clean(baseline + 14 * DAY)
        self.assertEqual(offline.status(key)['phase'], 'complete')
        self.clean(baseline + 15 * DAY)
        self.assertIsNone(offline.status(key))

    def test_disable_is_shared_and_survives_restart_until_explicitly_enabled(self):
        now = time.time()
        key = self.complete('1', now - 30 * DAY)
        result = main.offline_cleanup(main.OfflineCleanupIn(enabled=False))
        self.assertFalse(result['enabled'])
        self.clean(now)
        self.assertEqual(offline.status(key)['phase'], 'complete')
        self.assertFalse(offline._read(offline.ROOT / 'retention.json')['enabled'])
        self.assertFalse(offline.cleanup_policy()['enabled'])
        main.offline_cleanup(main.OfflineCleanupIn(enabled=True))
        self.clean(now + 60)
        self.assertIsNone(offline.status(key))

    def test_playback_between_selection_and_deletion_preserves_the_movie(self):
        now = time.time()
        key = self.complete('1', now - 30 * DAY)
        finish = offline._finish_delete
        def play_before_delete(key):
            offline.touch_played(key)
            return finish(key)
        with patch.object(offline, '_finish_delete', side_effect=play_before_delete):
            self.clean(now)
        self.assertEqual(offline.status(key)['phase'], 'complete')
        self.assertFalse((offline._folder(key) / 'delete.json').exists())

    def test_pending_cleanup_respects_disable_and_locked_files_keep_retry_metadata(self):
        now = time.time()
        key = self.complete('1', now - 30 * DAY)
        folder = offline._folder(key)
        handle = offline._claim(folder)
        try:
            self.clean(now)
            self.assertEqual(offline.status(key)['phase'], 'deleting')
        finally: offline._release(handle)
        offline.cleanup_policy(False)
        offline._maintenance_once()
        self.assertEqual(offline.status(key)['phase'], 'complete')
        offline.cleanup_policy(True)
        from pathlib import Path
        unlink = Path.unlink
        def locked(path, *args, **kwargs):
            if path == folder / 'video.mp4': raise PermissionError('playing')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', locked): self.clean(now + 60)
        self.assertEqual(offline.status(key)['phase'], 'delete_error')
        self.assertTrue((folder / 'video.mp4').exists())
        self.assertTrue((folder / 'complete.json').exists())


if __name__ == '__main__':
    unittest.main()
