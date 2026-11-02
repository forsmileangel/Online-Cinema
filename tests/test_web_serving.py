import asyncio
import gzip
import tempfile
import unittest
from pathlib import Path

from backend import web_serving

BIG = (b'{"items": "' + b"x" * 4000 + b'"}')


def run(app, path, accept="gzip", body=BIG, ctype=b"application/json", more=False):
    """Push one request through an ASGI app; returns (status, headers, body)."""
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", ctype)]})
        if more:
            for piece in (body[:10], body[10:]):
                await send({"type": "http.response.body", "body": piece, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await send({"type": "http.response.body", "body": body})

    target = app(inner) if app is not None else inner
    messages = []
    scope = {"type": "http", "method": "GET", "path": path, "root_path": "", "query_string": b"", "scheme": "http",
             "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1), "headers": [(b"accept-encoding", accept.encode())]}

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    asyncio.run(target(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, data


class CompressedTests(unittest.TestCase):
    def test_json_answers_are_gzipped_for_a_browser_that_accepts_it(self):
        status, headers, data = run(web_serving.Compressed, "/api/home")
        self.assertEqual((status, headers.get("content-encoding")), (200, "gzip"))
        self.assertEqual(gzip.decompress(data), BIG)
        self.assertLess(len(data), len(BIG) // 4)

    def test_media_and_the_line_by_line_search_pass_through_untouched(self):
        for path in ("/api/hls", "/api/img", "/api/offline/media/abc", "/api/search/multi"):
            with self.subTest(path=path):
                status, headers, data = run(web_serving.Compressed, path, body=BIG, more=path.endswith("multi"))
                self.assertEqual((status, headers.get("content-encoding"), data), (200, None, BIG))

    def test_small_answers_and_clients_without_gzip_are_left_alone(self):
        _, headers, data = run(web_serving.Compressed, "/api/health", body=b'{"ok": true}')
        self.assertEqual((headers.get("content-encoding"), data), (None, b'{"ok": true}'))
        _, headers, data = run(web_serving.Compressed, "/api/home", accept="identity")
        self.assertEqual((headers.get("content-encoding"), data), (None, BIG))


class ImmutableAssetsTests(unittest.TestCase):
    def test_hashed_assets_may_be_kept_for_a_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
            app = web_serving.ImmutableAssets(directory=tmp)
            messages = []
            scope = {"type": "http", "method": "GET", "path": "/index-abc123.js", "root_path": "", "query_string": b"",
                     "scheme": "http", "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1), "headers": []}

            async def receive():
                return {"type": "http.request", "body": b""}

            async def send(message):
                messages.append(message)

            asyncio.run(app(scope, receive, send))
            start = next(m for m in messages if m["type"] == "http.response.start")
            headers = {k.decode(): v.decode() for k, v in start["headers"]}
            self.assertEqual(start["status"], 200)
            self.assertEqual(headers["cache-control"], "public, max-age=31536000, immutable")


if __name__ == "__main__":
    unittest.main()
