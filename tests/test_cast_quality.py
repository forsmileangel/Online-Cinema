import unittest

from backend.cast_quality import Governor

PLAYING = dict(playing=True, buffering=False, paused=False)


def buffering(position=100.0):
    return dict(playing=False, buffering=True, paused=False, current_time=position)


class GovernorTests(unittest.TestCase):
    def run_stall(self, governor, start, seconds, ahead, position=100.0):
        verdict = None
        for step in range(int(seconds) + 1):
            verdict = governor.observe(buffering(position), ahead, start + step) or verdict
        governor.observe({**PLAYING, "current_time": position}, ahead, start + seconds + 1)
        return verdict

    def test_startup_buffering_and_short_waits_never_count(self):
        governor = Governor(0)
        self.assertIsNone(self.run_stall(governor, 1, 15, {"lead_seconds": 120, "ratio": 3}))
        self.assertIsNone(self.run_stall(governor, 40, 2, {"lead_seconds": 120, "ratio": 3}))
        self.assertIsNone(self.run_stall(governor, 60, 2, {"lead_seconds": 120, "ratio": 3}))

    def test_two_stalls_with_read_ahead_in_hand_blame_the_receiver(self):
        governor = Governor(0)
        ahead = {"lead_seconds": 90, "ratio": 4}
        self.assertIsNone(self.run_stall(governor, 30, 4, ahead))
        self.assertEqual(self.run_stall(governor, 100, 4, ahead), "receiver")
        # Decided once per episode.
        self.assertIsNone(self.run_stall(governor, 150, 12, ahead))

    def test_long_stall_with_a_slow_source_blames_upstream(self):
        governor = Governor(0)
        self.assertEqual(self.run_stall(governor, 30, 11, {"lead_seconds": 4, "ratio": 0.6}), "upstream")

    def test_stalls_far_apart_or_while_catching_up_do_not_decide(self):
        governor = Governor(0)
        ahead = {"lead_seconds": 90, "ratio": 4}
        self.assertIsNone(self.run_stall(governor, 30, 4, ahead))
        self.assertIsNone(self.run_stall(governor, 300, 4, ahead))  # More than 3 minutes later.
        governor = Governor(0)
        # Low lead but downloading faster than real time: still filling, no verdict yet.
        self.assertIsNone(self.run_stall(governor, 30, 11, {"lead_seconds": 5, "ratio": 2.5}))
        self.assertIsNone(self.run_stall(governor, 30, 11, None))

    def test_seeks_and_playback_jumps_get_a_grace_period(self):
        governor = Governor(0)
        governor.seeked(100)
        self.assertIsNone(self.run_stall(governor, 101, 8, {"lead_seconds": 90, "ratio": 4}))
        governor.observe({**PLAYING, "current_time": 500}, None, 130)  # A remote seek far ahead.
        self.assertIsNone(self.run_stall(governor, 131, 8, {"lead_seconds": 90, "ratio": 4}, position=500))
        governor.stop()
        self.assertIsNone(self.run_stall(governor, 200, 30, {"lead_seconds": 90, "ratio": 4}, position=500))


if __name__ == "__main__":
    unittest.main()
