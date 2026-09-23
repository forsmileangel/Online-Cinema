import sqlite3
import unittest
from unittest.mock import patch

from backend import catalog, db, sites


class SourceTests(unittest.TestCase):
    def test_only_three_sources_are_available_and_unknown_settings_fall_back(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        db._init(conn)
        for source in ("missav", "hongguo", "gimy"):
            conn.execute("INSERT INTO favorites VALUES (?, 'Neutral', '', 1)", (source + ":clip",))
            conn.execute("INSERT INTO history(video_id,title,cover,position_sec,duration_sec,updated_at) VALUES (?, 'Neutral', '', 30, 120, 1)", (source + ":clip",))
        with patch.object(db, "connect", return_value=conn):
            db.set_setting("source", "missav")
            self.assertEqual(catalog.current_source(), "hongguo")
            self.assertEqual({item["source"] for item in db.list_favorites()}, {"hongguo", "gimy"})
            self.assertEqual({item["source"] for item in db.list_history()}, {"hongguo", "gimy"})
            self.assertEqual([item["video_id"] for item in db.list_history(source="hongguo")], ["clip"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0], 3)
        self.assertEqual({item["id"] for item in sites.available()}, {"hongguo", "chinaq", "gimy", "dramaq"})


if __name__ == "__main__":
    unittest.main()
