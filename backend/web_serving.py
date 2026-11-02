"""How the built front-end and JSON answers are delivered: compressed, and assets kept for a year."""
from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles

# Media is already compressed or streamed; the multi-source search sends one line per site as it
# finishes, and gzip would hold those lines back in its buffer.
UNCOMPRESSED = ("/api/hls", "/api/img", "/api/offline/media/", "/api/search/multi")


class Compressed:
    """Pure ASGI wrapper: gzip for pages, scripts and JSON, nothing else."""

    def __init__(self, app):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not scope["path"].startswith(UNCOMPRESSED):
            return await self.gzip(scope, receive, send)
        await self.app(scope, receive, send)


class ImmutableAssets(StaticFiles):
    """Vite names every file under /assets by its content hash: a browser may keep it for a year."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
