import json
import sqlite3
import threading
import time
import unittest
from unittest.mock import Mock, patch

from backend import cast, cast_session, db, dlna


class ReconnectTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        for target, name, value in ((db, "connect", self.conn),):
            mock = patch.object(target, name, return_value=value)
            mock.start(); self.addCleanup(mock.stop)
        for target, name in ((cast_session, "_sessions"), (cast, "_active")):
            mock = patch.object(target, name, {})
            mock.start(); self.addCleanup(mock.stop)
        self.session = cast_session.PlaybackSession(dict(uuid="tv", source="gimy", video_id="123", episode_id="2", autoplay_next=True))
        self.session.episode_ids = ["1", "2", "3"]
        self.session.epoch = 2
        self.content = "http://192.168.1.2:6970/api/hls?u=media&cast_session=" + self.session.id + "-2"
        cast_session._sessions["tv"] = self.session
        self.session.publish(content_id=self.content, title="Example", phase="paused", paused=True, current_time=60, duration=100)
        self.receiver = dlna.Renderer("tv", "PHILIPS", "192.168.1.3", "http://192.168.1.3/control")
        self.state = dict(uuid="tv", content_id=self.content, playing=False, paused=True, buffering=False, idle=False, current_time=65, duration=100)

    def test_checkpoint_restores_identity_episodes_and_single_worker(self):
        cast_session._sessions.clear()
        with patch.object(cast_session.threading.Thread, "start") as start:
            result = cast_session.get("tv")
            self.assertEqual(result["session_id"], self.session.id)
            self.assertEqual(result["phase"], "reconnecting")
            restored = cast_session._sessions["tv"]
            self.assertEqual(restored.episode_ids, ["1", "2", "3"])
            self.assertEqual(restored.epoch, 2)
            self.assertTrue(result["autoplay_next"])
            cast_session.get("tv")
            start.assert_called_once()

    def test_reconnect_retains_current_progress_without_play_or_seek(self):
        with patch.object(cast, "_cast", return_value=("tv", self.receiver)), patch.object(cast, "_available_status", return_value=self.state), patch.object(self.receiver, "command") as command:
            result = cast.reconnect("tv", self.content, self.session.id, "Example")
        self.assertEqual(result["current_time"], 65)
        self.assertTrue(result["paused"])
        self.assertEqual(cast.session_content("tv", self.session.id), self.content)
        self.assertEqual(self.receiver.title, "Example")
        command.assert_not_called()

    def test_reconnect_rejects_stopped_other_content_and_wrong_marker(self):
        for state in ({**self.state, "idle": True}, {**self.state, "content_id": "other-app"}):
            with self.subTest(state=state), patch.object(cast, "_cast", return_value=("tv", self.receiver)), patch.object(cast, "_available_status", return_value=state):
                with self.assertRaises(RuntimeError):
                    cast.reconnect("tv", self.content, self.session.id)
                self.assertEqual(cast._active, {})
        with self.assertRaises(ValueError):
            cast.reconnect("tv", self.content, "another-session")

    def test_stop_clears_checkpoint_and_stale_worker_cannot_revive_it(self):
        self.session.publish(phase="stopped")
        self.assertEqual(db.get_setting("cast_session:tv"), "")
        replacement = cast_session.PlaybackSession(dict(uuid="tv"))
        cast_session._sessions["tv"] = replacement
        self.session.publish(phase="playing")
        self.assertEqual(db.get_setting("cast_session:tv"), "")

    def test_autoplay_change_is_saved_immediately_and_cancel_clears_it(self):
        self.session.prefetch = Mock()
        self.session.command("autoplay", autoplay_next=False)
        saved = json.loads(db.get_setting("cast_session:tv"))
        self.assertFalse(saved["snapshot"]["autoplay_next"])
        cast_session.cancel("tv")
        self.assertEqual(db.get_setting("cast_session:tv"), "")
        self.assertTrue(self.session.cancelled.is_set())
        self.assertIsNone(cast_session.get("tv"))

    def test_restored_worker_accepts_seek_and_pause_without_reloading_media(self):
        cast_session._sessions.clear()
        with patch.object(cast_session.threading.Thread, "start"):
            cast_session.get("tv")
        restored = cast_session._sessions["tv"]
        current = dict(self.state)
        def control(action, position, *args, **kwargs):
            current.update(current_time=position if action == "seek" else current["current_time"],
                           paused=action != "resume", playing=action == "resume")
            return dict(current)
        def wait_for(predicate):
            deadline = time.monotonic() + 2
            while not predicate() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(predicate())
        with patch.object(cast, "reconnect", return_value=self.state), patch.object(cast, "status", side_effect=lambda _: dict(current)), patch.object(cast, "control", side_effect=control), patch.object(restored, "load") as load, patch.object(restored, "save"):
            worker = threading.Thread(target=restored.run, kwargs={"reconnect": True})
            worker.start()
            try:
                wait_for(lambda: restored.get()["phase"] == "paused")
                cast_session.control("tv", self.session.id, "seek", position_sec=80)
                wait_for(lambda: restored.get()["current_time"] == 80 and not restored.get()["pending_action"])
                cast_session.control("tv", self.session.id, "resume")
                wait_for(lambda: restored.get()["playing"])
                cast_session.control("tv", self.session.id, "pause")
                wait_for(lambda: restored.get()["paused"] and not restored.get()["pending_action"])
                load.assert_not_called()
            finally:
                restored.cancelled.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())

    def test_corrupt_or_wrong_device_checkpoint_is_not_restored(self):
        cast_session._sessions.clear()
        saved = db.get_setting("cast_session:tv")
        db.set_setting("cast_session:other", saved)
        self.assertIsNone(cast_session.get("other"))
        db.set_setting("cast_session:tv", "not-json")
        self.assertIsNone(cast_session.get("tv"))


if __name__ == "__main__":
    unittest.main()
