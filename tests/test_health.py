import unittest
from unittest.mock import patch

from backend import cast_session, main, offline


class FakeSession:
    def __init__(self, phase):
        self.phase = phase

    def get(self):
        return {"phase": self.phase}


class HealthTests(unittest.TestCase):
    def setUp(self):
        main._version_cache = "abc1234"
        self.addCleanup(setattr, main, "_version_cache", None)

    def test_health_reports_version_and_what_a_restart_would_interrupt(self):
        with patch.object(cast_session, "_sessions", {"a": FakeSession("playing"), "b": FakeSession("stopped")}), \
                patch.object(offline, "active_count", return_value=2):
            body = main.health()
        self.assertTrue(body["ok"])
        self.assertEqual(body["version"], "abc1234")
        self.assertEqual(body["busy"], {"casts": 1, "offline_jobs": 2})
        if hasattr(main, "mirrors"):
            self.assertIn("mirror", body)

    def test_finished_sessions_do_not_count(self):
        sessions = {phase: FakeSession(phase) for phase in ("stopped", "ended", "replaced", "error")}
        with patch.object(cast_session, "_sessions", sessions):
            self.assertEqual(cast_session.active_count(), 0)
        sessions = {phase: FakeSession(phase) for phase in ("loading", "waking", "playing", "paused", "reconnecting")}
        with patch.object(cast_session, "_sessions", sessions):
            self.assertEqual(cast_session.active_count(), 5)

    def test_only_jobs_under_way_count_as_busy(self):
        statuses = {
            "a" * 64: {"phase": "downloading"}, "b" * 64: {"phase": "complete"}, "c" * 64: {"phase": "queued"},
            "d" * 64: {"phase": "error"}, "e" * 64: {"phase": "cancelled"}, "f" * 64: {"phase": "retrying"},
        }

        class Folder:
            def __init__(self, name):
                self.name = name

            def is_dir(self):
                return True

        class Root:
            def exists(self):
                return True

            def iterdir(self):
                return [Folder(name) for name in statuses] + [Folder("retention.json")]

        with patch.object(offline, "ROOT", Root()), patch.object(offline, "status", lambda key: statuses[key]):
            self.assertEqual(offline.active_count(), 3)

    @unittest.skipUnless(hasattr(main, "mirrors"), "MissAV mirrors are only in Midnight Cinema")
    def test_health_and_settings_never_probe_for_a_mirror(self):
        from backend import mirrors
        with patch.object(mirrors, "_current", ""), patch.object(mirrors, "detect", side_effect=AssertionError("probed")):
            self.assertEqual(main.health()["mirror"], "")
            self.assertEqual(mirrors.cached_host(), "")
        with patch.object(mirrors, "_current", "missav.ai"):
            self.assertEqual(mirrors.cached_host(), "missav.ai")

    @unittest.skipUnless(hasattr(main, "mirrors"), "MissAV mirrors are only in Midnight Cinema")
    def test_concurrent_callers_share_one_detection(self):
        import threading
        from backend import mirrors
        calls = []
        started = threading.Event()

        def detect():
            calls.append(1)
            started.wait(2)
            mirrors._current = "missav.ai"
            return "missav.ai"

        with patch.object(mirrors, "_current", ""), patch.object(mirrors, "detect", detect):
            results = []
            threads = [threading.Thread(target=lambda: results.append(mirrors.current_host())) for _ in range(4)]
            for t in threads:
                t.start()
            started.set()
            for t in threads:
                t.join(5)
        self.assertEqual(results, ["missav.ai"] * 4)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
