import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import db
from backend import settings as S


class SettingsCacheTests(unittest.TestCase):
    def setUp(self):
        self.real = sqlite3.connect(":memory:", check_same_thread=False)
        self.real.row_factory = sqlite3.Row
        db._init(self.real)
        self.addCleanup(self.real.close)
        self.conn = Mock(wraps=self.real)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        db._settings.clear()
        self.addCleanup(db._settings.clear)

    def test_reads_come_from_memory_until_something_is_written(self):
        db.set_setting("lan_tv", "1")
        self.assertEqual(db.get_setting("lan_tv"), "1")
        self.conn.execute.reset_mock()
        for _ in range(5):
            self.assertEqual(db.get_setting("lan_tv"), "1")
            self.assertEqual(db.get_setting("missing", "default"), "default")
        self.conn.execute.assert_not_called()
        db.set_setting("lan_tv", "")
        self.assertEqual(db.get_setting("lan_tv", "unset"), "")
        db.delete_setting("lan_tv")
        self.assertEqual(db.get_setting("lan_tv", "unset"), "unset")

    def test_a_load_overtaken_by_a_write_is_not_remembered(self):
        db.set_setting("locale", "old")
        written = []

        def read_then_someone_writes(sql, *args):
            rows = self.real.execute(sql, *args).fetchall()
            if sql.startswith("SELECT key") and not written:
                written.append(True)
                db.set_setting("locale", "new")
            return Mock(fetchall=Mock(return_value=rows))

        self.conn.execute.side_effect = read_then_someone_writes
        self.assertEqual(db.get_setting("locale"), "old", "this load began before the write")
        self.conn.execute.side_effect = None
        self.assertEqual(db.get_setting("locale"), "new")

    def test_another_connection_sees_the_write_too(self):
        db.set_setting("locale", "ja")
        self.assertEqual(db.get_setting("locale"), "ja")
        other = Mock(wraps=self.real)
        with patch.object(db, "connect", return_value=other):
            self.assertEqual(db.get_setting("locale"), "ja")
            db.set_setting("locale", "cn")
        self.assertEqual(db.get_setting("locale"), "cn")


class InitOnceTests(unittest.TestCase):
    def test_the_schema_check_runs_once_per_file_however_many_threads_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "app.sqlite")
            with patch.object(S, "db_path", return_value=path), patch.object(db, "_init", wraps=db._init) as init:
                db.reset()
                try:
                    db.connect()

                    def work():
                        db.get_setting("x", "")
                        db.connect().close()  # This thread's connection, so the folder can be removed.
                        db._local.conn = None

                    workers = [threading.Thread(target=work) for _ in range(4)]
                    for worker in workers:
                        worker.start()
                    for worker in workers:
                        worker.join(5)
                    init.assert_called_once()
                    self.assertEqual(db.get_setting("x", "none"), "none")
                finally:
                    db.reset()


if __name__ == "__main__":
    unittest.main()
