import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend import pools


class PoolsTests(unittest.TestCase):
    def test_map_in_keeps_the_order_and_raises_the_first_failure(self):
        self.assertEqual(pools.map_in(pools.io, lambda n: n * 2, [3, 1, 2]), [6, 2, 4])

        def boom(n):
            if n == 1:
                raise ValueError("one")
            return n

        with self.assertRaisesRegex(ValueError, "one"):
            pools.map_in(pools.io, boom, [3, 1, 2])

    def test_each_done_gives_up_on_time_and_cancels_what_never_started(self):
        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown, wait=True, cancel_futures=True)
        release = threading.Event()

        def task(n):
            if n == "slow":
                release.wait(2)
            return n

        finished = dict(pools.each_done(pool, task, ["slow", "second", "third"], timeout=0.2))
        self.assertEqual(finished, {})
        release.set()
        time.sleep(0.1)
        # The slow one kept its worker until it finished; the queued ones were cancelled before they ran,
        # so the pool is free again and nothing from the first call runs late.
        results = {item: future.result() for item, future in pools.each_done(pool, task, ["again"], timeout=2)}
        self.assertEqual(results, {"again": "again"})

    def test_fanout_tasks_may_wait_for_io_tasks(self):
        def leaf(n):
            return n + 1

        def branch(n):
            return pools.map_in(pools.io, leaf, [n, n + 10])

        self.assertEqual(pools.map_in(pools.fanout, branch, [1, 2]), [[2, 12], [3, 13]])


if __name__ == "__main__":
    unittest.main()
