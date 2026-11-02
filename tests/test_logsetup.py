import io
import logging
import tempfile
import unittest
from pathlib import Path

from backend import logsetup


def access(path, status=200):
    return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                             ("192.168.1.50:5000", "GET", path, "1.1", status), None)


class AccessFilterTests(unittest.TestCase):
    def setUp(self):
        self.filter = logsetup.AccessFilter()

    def test_routine_polling_and_media_lines_are_dropped(self):
        for path in ("/api/cast/session?uuid=abc", "/api/health", "/api/hls?u=https://x/y.m3u8", "/api/img?u=https://x/y.jpg",
                     "/api/offline/media/abc.mp4", "/assets/index-abc.js"):
            with self.subTest(path=path):
                self.assertFalse(self.filter.filter(access(path)))
                self.assertFalse(self.filter.filter(access(path, 304)))

    def test_their_failures_and_everything_else_are_kept(self):
        self.assertTrue(self.filter.filter(access("/api/hls?u=https://x/y.m3u8", 502)))
        self.assertTrue(self.filter.filter(access("/api/health", 500)))
        self.assertTrue(self.filter.filter(access("/api/home?source=hongguo")))
        self.assertTrue(self.filter.filter(access("/api/healthz")))  # not the health check itself

    def test_pairing_codes_searches_and_upstream_urls_are_blanked(self):
        for path, expected in (("/tv?c=ABC234", "/tv?c=…"),
                               ("/connect?c=ABC234&x=1", "/connect?c=…&x=1"),
                               ("/api/search?q=%E7%B8%BD%E8%A3%81&page=2", "/api/search?q=…&page=2"),
                               ("/api/hls?u=https://cdn/x.m3u8?token=1", "/api/hls?u=…"),
                               ("/api/tv/pair?code=ABC234", "/api/tv/pair?code=…"),
                               ("/api/home?source=hongguo", "/api/home?source=hongguo")):
            with self.subTest(path=path):
                record = access(path, 404)
                self.assertTrue(self.filter.filter(record))
                self.assertEqual(record.args[2], expected)
                self.assertEqual(record.getMessage(), f'192.168.1.50:5000 - "GET {expected} HTTP/1.1" 404')

    def test_other_records_pass_untouched(self):
        record = logging.LogRecord("backend.main", logging.ERROR, "", 0, "unexpected %s", ("boom",), None)
        self.assertTrue(self.filter.filter(record))
        self.assertEqual(record.getMessage(), "unexpected boom")


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved = (logging.getLogger().handlers[:], logging.getLogger().level)
        self.access_filters = logging.getLogger("uvicorn.access").filters[:]

        def restore():
            root = logging.getLogger()
            for handler in root.handlers[:]:
                handler.close()
                root.removeHandler(handler)
            for handler in self.saved[0]:
                root.addHandler(handler)
            root.setLevel(self.saved[1])
            logging.getLogger("uvicorn.access").filters[:] = self.access_filters
        self.addCleanup(restore)

    def test_file_gets_everything_with_timestamps_and_the_console_skips_access_lines(self):
        console = io.StringIO()
        logsetup.configure(Path(self.tmp.name) / "logs", console=console)
        logging.getLogger("uvicorn.error").info("Uvicorn running on http://127.0.0.1:6969")
        logging.getLogger("uvicorn.access").handle(access("/api/home?source=hongguo"))
        logging.getLogger("uvicorn.access").handle(access("/api/cast/session?uuid=x"))
        logging.getLogger("backend.main").error("unexpected %s", "boom")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = (Path(self.tmp.name) / "logs" / "app.log").read_text(encoding="utf-8")
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO uvicorn.error: Uvicorn running")
        self.assertIn('"GET /api/home?source=hongguo HTTP/1.1" 200', text)
        self.assertNotIn("cast/session", text)
        self.assertIn("ERROR backend.main: unexpected boom", text)
        out = console.getvalue()
        self.assertIn("Uvicorn running", out)
        self.assertIn("unexpected boom", out)
        self.assertNotIn("/api/home", out)

    def test_configuring_twice_does_not_double_handlers_or_filters(self):
        logsetup.configure(Path(self.tmp.name) / "logs", console=io.StringIO())
        logsetup.configure(Path(self.tmp.name) / "logs", console=io.StringIO())
        self.assertEqual(len(logging.getLogger().handlers), 2)
        self.assertEqual(sum(isinstance(f, logsetup.AccessFilter) for f in logging.getLogger("uvicorn.access").filters), 1)


if __name__ == "__main__":
    unittest.main()
