import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from starlette.responses import Response

from backend import img_cache

JPEG = b"\xff\xd8\xff" + b"cover-bytes" * 10


class ImgCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(img_cache.settings, "app_dir", return_value=Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        img_cache.clear()
        self.addCleanup(img_cache.clear)
        self.loader = Mock(side_effect=lambda url: Response(content=JPEG, media_type="image/jpeg"))

    def test_second_request_is_served_from_disk_with_the_same_etag(self):
        first = img_cache.fetch("https://img.example/a.jpg", self.loader)
        second = img_cache.fetch("https://img.example/a.jpg", self.loader)
        self.assertEqual(first, (JPEG, "image/jpeg", first[2]))
        self.assertEqual(second, first)
        self.loader.assert_called_once()
        self.assertEqual(sorted(path.suffix for path in Path(self.tmp.name, "img-cache").iterdir()), [".jpg"])

    def test_a_browser_that_has_the_image_gets_304(self):
        etag = img_cache.fetch("https://img.example/a.jpg", self.loader)[2]
        hit = img_cache._serve("https://img.example/a.jpg", self.loader, etag)
        self.assertEqual((hit.status_code, hit.headers["etag"], hit.body), (304, etag, b""))
        miss = img_cache._serve("https://img.example/a.jpg", self.loader, '"something-else"')
        self.assertEqual((miss.status_code, miss.body, miss.media_type), (200, JPEG, "image/jpeg"))
        self.assertIn("max-age=86400", miss.headers["cache-control"])

    def test_simultaneous_requests_for_one_image_fetch_it_once(self):
        gate = threading.Event()

        def slow(url):
            gate.wait(2)
            return Response(content=JPEG, media_type="image/jpeg")

        loader = Mock(side_effect=slow)
        results = []
        workers = [threading.Thread(target=lambda: results.append(img_cache.fetch("https://img.example/b.jpg", loader))) for _ in range(4)]
        for worker in workers:
            worker.start()
        time.sleep(0.1)
        gate.set()
        for worker in workers:
            worker.join(5)
        self.assertEqual(len(results), 4)
        self.assertEqual({r[0] for r in results}, {JPEG})
        loader.assert_called_once()

    def test_least_recently_used_images_go_first_when_the_cache_is_full(self):
        with patch.object(img_cache, "CACHE_BYTES", len(JPEG) * 2 + 1):
            img_cache.fetch("https://img.example/1.jpg", self.loader)
            img_cache.fetch("https://img.example/2.jpg", self.loader)
            img_cache.fetch("https://img.example/1.jpg", self.loader)  # 1 is now the newer one.
            img_cache.fetch("https://img.example/3.jpg", self.loader)
        kept = {key for key in img_cache._index}
        self.assertIn(img_cache._key("https://img.example/1.jpg"), kept)
        self.assertNotIn(img_cache._key("https://img.example/2.jpg"), kept)
        self.assertEqual(len(list(Path(self.tmp.name, "img-cache").iterdir())), 2)

    def test_an_expired_file_is_fetched_again_and_a_changed_image_gets_a_new_etag(self):
        first = img_cache.fetch("https://img.example/a.jpg", self.loader)
        path = img_cache._index[img_cache._key("https://img.example/a.jpg")][0]
        old = time.time() - img_cache.TTL - 5
        os.utime(path, (old, old))
        img_cache.clear()  # As after a restart: the age comes from the file itself.
        self.loader.side_effect = lambda url: Response(content=JPEG + b"-new", media_type="image/jpeg")
        again = img_cache.fetch("https://img.example/a.jpg", self.loader)
        self.assertEqual(self.loader.call_count, 2)
        self.assertEqual(again[0], JPEG + b"-new")
        self.assertNotEqual(first[2], again[2])
        self.assertEqual(len(list(Path(self.tmp.name, "img-cache").iterdir())), 1)

    def test_the_etag_survives_a_restart(self):
        etag = img_cache.fetch("https://img.example/a.jpg", self.loader)[2]
        img_cache.clear()
        self.assertEqual(img_cache._serve("https://img.example/a.jpg", self.loader, etag).status_code, 304)
        self.loader.assert_called_once()

    def test_a_failed_fetch_caches_nothing_and_frees_the_next_caller(self):
        loader = Mock(side_effect=ValueError("not an image"))
        with self.assertRaises(ValueError):
            img_cache.fetch("https://img.example/bad.jpg", loader)
        with self.assertRaises(ValueError):
            img_cache.fetch("https://img.example/bad.jpg", loader)
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(img_cache._inflight, {})


if __name__ == "__main__":
    unittest.main()
