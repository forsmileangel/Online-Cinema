import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import offline


class CompletedSegmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name, "000001.ts")
        self.path.write_bytes(b"segment-bytes")
        self.url = "https://cdn.example/000001.ts"

    def record(self, **extra):
        stat = self.path.stat()
        data = dict(url=self.url, complete=True, size=stat.st_size, sha256=hashlib.sha256(b"segment-bytes").hexdigest())
        data.update(extra)
        self.path.with_name(self.path.name + ".download.json").write_text(json.dumps(data), encoding="utf-8")
        return data

    def test_a_file_whose_recorded_time_still_matches_is_trusted_without_hashing(self):
        self.record(mtime=self.path.stat().st_mtime)
        with patch.object(offline, "_file_hash", side_effect=AssertionError("hashed")):
            self.assertTrue(offline._completed(self.path, self.url))

    def test_older_records_and_touched_files_are_hashed_again(self):
        self.record()
        with patch.object(offline, "_file_hash", wraps=offline._file_hash) as digest:
            self.assertTrue(offline._completed(self.path, self.url))
            digest.assert_called_once()
        self.record(mtime=self.path.stat().st_mtime - 100)
        self.assertTrue(offline._completed(self.path, self.url))
        self.path.write_bytes(b"segment-bytez")
        self.record(mtime=self.path.stat().st_mtime - 100)
        self.assertFalse(offline._completed(self.path, self.url))

    def test_size_url_and_completion_are_still_required(self):
        self.record(mtime=self.path.stat().st_mtime)
        self.assertFalse(offline._completed(self.path, "https://cdn.example/other.ts"))
        data = self.record(mtime=self.path.stat().st_mtime, complete=False)
        self.assertFalse(offline._completed(self.path, self.url))
        data = self.record(mtime=self.path.stat().st_mtime)
        os.truncate(self.path, 3)
        self.assertFalse(offline._completed(self.path, self.url))
        self.assertIsNotNone(data)

    def test_finished_record_carries_the_modification_time(self):
        record = offline._finished_record(self.path, dict(url=self.url, etag='"x"'))
        self.assertEqual(record["mtime"], self.path.stat().st_mtime)
        self.assertEqual((record["complete"], record["size"], record["etag"]), (True, 13, '"x"'))
        self.path.with_name(self.path.name + ".download.json").write_text(json.dumps(record), encoding="utf-8")
        with patch.object(offline, "_file_hash", side_effect=AssertionError("hashed")):
            self.assertTrue(offline._completed(self.path, self.url))


if __name__ == "__main__":
    unittest.main()
