import asyncio
import threading
import time
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
                patch.object(offline, "active_count", return_value=2) as count:
            body = asyncio.run(main.health())
            fresh = asyncio.run(main.health(fresh=True))
        self.assertTrue(body["ok"])
        self.assertEqual(body["version"], "abc1234")
        self.assertEqual(body["busy"], {"casts": 1, "offline_jobs": 2})
        self.assertEqual(fresh["busy"], {"casts": 1, "offline_jobs": 2})
        self.assertEqual([c.args for c in count.call_args_list], [(), (True,)], "?fresh=1 counts now, the plain check does not")
        if hasattr(main, "mirrors"):
            self.assertIn("mirror", body)

    def test_health_answers_at_once_while_the_download_folder_is_slow(self):
        # The supervisor restarts a server that does not answer; a busy disk must not look like a hung server.
        release = threading.Event()

        def slow_count():
            release.wait(10)
            return 1

        with patch.object(offline, "_active_known", (time.monotonic(), 0)), patch.object(offline, "_active_counting", False), \
                patch.object(offline, "ACTIVE_COUNT_TTL", 0), patch.object(offline, "_count_active", slow_count):
            started = time.monotonic()
            body = asyncio.run(main.health())
            took = time.monotonic() - started
            release.set()
            for _ in range(100):
                if not offline._active_counting:
                    break
                time.sleep(0.02)
            self.assertEqual(offline._active_known[1], 1, "the background recount lands")
        self.assertLess(took, 3, "the count takes 10 s here; the answer must not wait for it")
        self.assertEqual(body["busy"]["offline_jobs"], 0, "the last known count, not a wait")

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

        with patch.object(offline, "ROOT", Root()), patch.object(offline, "status", lambda key: statuses[key]), \
                patch.object(offline, "_active_known", None), patch.object(offline, "_active_counting", False):
            self.assertEqual(offline.active_count(fresh=True), 3)
            # Cached for a while: a job that just finished still counts until the next recount.
            statuses["a" * 64] = {"phase": "complete"}
            self.assertEqual(offline.active_count(), 3)
            self.assertEqual(offline.active_count(fresh=True), 2)

    def test_the_first_plain_count_starts_in_the_background(self):
        with patch.object(offline, "_active_known", None), patch.object(offline, "_active_counting", False), \
                patch.object(offline, "_count_active", return_value=4):
            self.assertEqual(offline.active_count(), 0, "nothing known yet: no wait")
            for _ in range(100):
                if offline._active_known:
                    break
                time.sleep(0.02)
            self.assertEqual(offline.active_count(), 4)

    @unittest.skipUnless(hasattr(main, "mirrors"), "MissAV mirrors are only in Midnight Cinema")
    def test_health_and_settings_never_probe_for_a_mirror(self):
        from backend import mirrors
        with patch.object(mirrors, "_current", ""), patch.object(mirrors, "detect", side_effect=AssertionError("probed")):
            self.assertEqual(asyncio.run(main.health())["mirror"], "")
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
