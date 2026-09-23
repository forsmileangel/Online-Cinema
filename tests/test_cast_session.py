import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

from backend import cast, cast_devices, cast_session, db, main
from backend.models import Episode, VideoDetail


def state(**values):
    return dict(uuid="tv", content_id="media", playing=True, paused=False, idle=False,
                buffering=False, current_time=99, duration=100, idle_reason="", **values)


class SessionTests(unittest.TestCase):
    def session(self):
        session = cast_session.PlaybackSession(dict(uuid="tv", source="hongguo", video_id="123", episode_id="1", autoplay_next=True))
        session.previous = state()
        session.snapshot.update(session.previous, episode_id="1", phase="playing")
        session.episode_ids = ["1", "2", "3"]
        session.save = Mock()
        return session

    def test_three_episodes_advance_without_browser_and_end_once(self):
        session = self.session()
        def load(episode):
            session.snapshot["episode_id"] = episode
            session.previous = state()
        session.load = Mock(side_effect=load)
        idle = {**state(), "playing": False, "idle": True, "current_time": 0}
        with patch.object(cast, "status", return_value=idle):
            session.tick(); session.tick(); session.tick()
        self.assertEqual([c.args[0] for c in session.load.call_args_list], ["2", "3"])
        self.assertEqual(session.get()["phase"], "ended")

    def test_pause_manual_stop_error_or_replacement_never_advance(self):
        for previous, current in [
            ({**state(), "playing": False, "paused": True}, {**state(), "idle": True, "playing": False}),
            (state(), {**state(), "idle": True, "playing": False, "idle_reason": "CANCELLED"}),
            (state(), {**state(), "idle": True, "playing": False, "idle_reason": "ERROR"}),
            (state(), {**state(), "content_id": "amberbox"}),
            ({**state(), "current_time": 5}, {**state(), "idle": True, "playing": False}),
        ]:
            self.assertFalse(cast_session.natural_end(previous, current, "media"))
        session = self.session(); session.load = Mock()
        with patch.object(cast, "status", return_value={**state(), "content_id": "amberbox"}):
            session.tick()
        self.assertEqual(session.get()["phase"], "replaced")
        session.load.assert_not_called()

    def test_stop_during_resolution_invalidates_play_guard(self):
        session = self.session()
        session.command("stop")
        self.assertFalse(session.valid())
        with patch.object(cast, "play") as play:
            session.load("2")
        play.assert_not_called()

    def test_stop_during_episode_load_targets_latest_owned_transport(self):
        session = self.session()
        with patch.object(cast, "session_content", return_value="new-owned-episode"), patch.object(cast, "control", return_value={**state(), "idle": True, "playing": False}) as control:
            session.handle("stop", {})
        self.assertEqual(control.call_args.args[3], "new-owned-episode")

    def test_failed_next_is_retryable_and_does_not_loop(self):
        session = self.session()
        session.load = Mock(side_effect=ValueError("bad episode"))
        with patch.object(cast, "status", return_value={**state(), "playing": False, "idle": True}):
            session.tick()
        self.assertEqual(session.get()["phase"], "error")
        self.assertIn("bad episode", session.get()["error"])

    def test_load_refreshes_once_and_never_retries_forever(self):
        session = self.session()
        session._load = Mock(side_effect=[ValueError("expired"), None])
        session.prefetch = Mock()
        session.load("2")
        self.assertEqual(session._load.call_count, 2)

    def test_session_replacement_cancels_old_worker_and_stale_commands(self):
        old = self.session()
        with patch.object(cast_session, "_sessions", {"tv": old}), patch.object(threading.Thread, "start"):
            new = cast_session.start(dict(uuid="tv", source="hongguo", video_id="123"))
            self.assertTrue(old.cancelled.is_set())
            self.assertNotEqual(old.id, new["session_id"])
            with self.assertRaises(ValueError):
                cast_session.control("tv", old.id, "stop")


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start(); self.addCleanup(p.stop); self.addCleanup(self.conn.close)

    def test_known_offline_receiver_keeps_identity_mac_and_updates_host(self):
        device = dict(uuid="lg", name="LG", host="192.168.1.11", kind="dlna")
        with patch.object(cast_devices, "learn_mac", return_value="10:20:30:40:50:60"):
            self.assertTrue(cast_devices.remember([device])[0]["online"])
            offline = cast_devices.remember([])[0]
            self.assertFalse(offline["online"])
            self.assertTrue(offline["can_wake"])
            self.assertEqual(cast_devices.remember([{**device, "host": "192.168.1.12"}])[0]["host"], "192.168.1.12")

    def test_magic_packet_and_mac_validation(self):
        packet = cast_devices.magic_packet("10-20-30-40-50-60")
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[6:], bytes.fromhex("102030405060") * 16)
        for bad in ("", "FF:FF:FF:FF:FF:FF", "00:00:00:00:00:00", "../bad"):
            with self.assertRaises(ValueError): cast_devices.normalize_mac(bad)

    def test_wake_requires_known_mac_and_can_be_cancelled(self):
        with self.assertRaises(ValueError): cast_devices.wake_and_wait("missing", lambda: True)
        with patch.object(cast_devices, "learn_mac", return_value="10:20:30:40:50:60"):
            cast_devices.remember([dict(uuid="lg", name="LG", host="192.168.1.11", kind="dlna")])
        with patch.object(cast_devices.socket, "socket") as sock:
            with self.assertRaises(RuntimeError): cast_devices.wake_and_wait("lg", lambda: False)
            sock.assert_not_called()

    def test_history_preserves_episode_and_legacy_seconds_are_not_misapplied(self):
        db.upsert_history("123", "Example", "", 42, 100, "hongguo", "3")
        self.assertEqual(db.get_history_item("123", "hongguo")["episode_id"], "3")
        detail = VideoDetail(id="123", title="Example", cover="", playlist="/api/hls?u=neutral", resolved_episode_id="3", episodes=[Episode(id="3", title="3", playlist="/api/hls?u=neutral")])
        with patch.object(main.sites, "get", return_value=Mock(fetch_video=Mock(return_value=detail))):
            self.assertEqual(main.video_on_source("hongguo", "123", None).position_sec, 42)
            db.upsert_history("123", "Example", "", 90, 100, "hongguo")
            detail.position_sec = 0
            result = main.video_on_source("hongguo", "123", None)
            self.assertEqual(result.position_sec, 0)

    def test_existing_database_schema_migrates(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("CREATE TABLE history(video_id TEXT PRIMARY KEY,title TEXT,cover TEXT,position_sec REAL,duration_sec REAL,updated_at REAL)")
            connection.execute("INSERT INTO history VALUES ('hongguo:123','Example','',42,100,1)")
            db._init(connection)
            self.assertEqual(tuple(connection.execute("SELECT position_sec,episode_id FROM history").fetchone()), (42, None))
        finally:
            connection.close()
