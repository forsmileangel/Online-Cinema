import sqlite3
import unittest
from unittest.mock import patch

from backend import catalog, db, main

# Two real sources of whichever app runs this file.
MINE, OTHER = catalog.SOURCES[0], catalog.SOURCES[1]


class HistoryLibraryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        db._init(self.conn)
        state = patch.object(db, "connect", return_value=self.conn)
        state.start()
        self.addCleanup(state.stop)

    def add(self, source, video_id, updated_at):
        self.conn.execute(
            "INSERT INTO history(video_id,title,cover,position_sec,duration_sec,updated_at) VALUES (?, 'Neutral', '', 30, 120, ?)",
            (f"{source}:{video_id}", updated_at))

    def test_source_filter_is_not_crowded_out_by_newer_rows_of_other_sources(self):
        self.add(MINE, "old-one", 1)
        for n in range(250):
            self.add(OTHER, f"clip{n}", 100 + n)
        self.assertEqual([r["video_id"] for r in db.list_history(source=MINE)], ["old-one"])
        self.assertEqual(len(db.list_history(limit=5000)), 251)  # The 紀錄 page gets every row up to its cap.
        self.assertEqual(len(db.list_history(limit=3)), 3)
        self.assertEqual(main.history(limit=40, source=MINE)["items"][0]["video_id"], "old-one")

    def test_remove_one_entry_keeps_the_same_id_on_other_sources(self):
        self.add(MINE, "same", 1)
        self.add(OTHER, "same", 2)
        main.del_history("same", source=MINE)
        self.assertEqual([(r["source"], r["video_id"]) for r in db.list_history()], [(OTHER, "same")])


if __name__ == "__main__":
    unittest.main()
