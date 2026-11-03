"""Disk cache for /api/img covers: one upstream fetch per image, a 304 for a browser that has it.

Images are served from their own small thread pool so a burst of covers on a long listing never
takes the request threads that playlists and segments need, and at most six upstream fetches run at
once. A cached file is reused for a day and the least recently used ones go first when the cache
passes 256 MB or 2000 files. Files are named ``<url hash>.<content hash>.<ext>``, so the ETag (the
content hash) survives a restart without hashing anything again.
"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from starlette.requests import Request
from starlette.responses import Response

from . import settings

CACHE_BYTES = 256 * 1024 * 1024
MAX_ITEMS = 2000
TTL = 86400.0
UPSTREAM_LIMIT = 8
# A cover that could not be fetched is not asked for again for this long: the page's own retry and
# the next page view get the same answer at once instead of waiting on the dead host again.
FAILURE_TTL = 60.0
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/avif": "avif", "image/bmp": "bmp"}
MIME = {ext: mime for mime, ext in EXT.items()}

pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="img")
_upstream = threading.BoundedSemaphore(UPSTREAM_LIMIT)
_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}
_failed: dict[str, tuple[float, Exception]] = {}
# url hash -> (path, size, fetched at, last used); the directory is listed once, then kept here.
_index: dict[str, tuple[Path, int, float, float]] = {}
_scanned_dir: Path | None = None


def _directory() -> Path:
    path = settings.app_dir() / "img-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _scan() -> None:
    """Fill the index from the cache directory (once per directory; tests move it around)."""
    global _scanned_dir
    directory = _directory()
    if _scanned_dir == directory:
        return
    _index.clear()
    for path in directory.iterdir():
        parts = path.name.split(".")
        if len(parts) != 3 or len(parts[0]) != 64 or len(parts[1]) != 16 or parts[2] not in MIME:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        _index[parts[0]] = (path, stat.st_size, stat.st_mtime, stat.st_mtime)
    _scanned_dir = directory


def _etag(path: Path) -> str:
    """The content hash in the file name: the same bytes keep the same tag across fetches and restarts."""
    return f'"{path.name.split(".")[1]}"'


def _lookup(url: str) -> tuple[bytes, str, str] | None:
    key = _key(url)
    with _lock:
        _scan()
        entry = _index.get(key)
        if not entry:
            return None
        path, size, fetched, _ = entry
        if time.time() - fetched > TTL:
            _index.pop(key, None)
            path.unlink(missing_ok=True)
            return None
        _index[key] = (path, size, fetched, time.time())
    try:
        data = path.read_bytes()
    except OSError:
        with _lock:
            _index.pop(key, None)
        return None
    return data, MIME[path.suffix[1:]], _etag(path)


def _store(url: str, data: bytes, mime: str) -> str:
    key = _key(url)
    ext = EXT.get(mime)
    if not ext:
        raise ValueError("unsupported image type")
    with _lock:
        _scan()
        # Evict expired and least recently used files until the new one fits.
        now = time.time()
        total = sum(entry[1] for entry in _index.values())
        for old_key, (path, size, fetched, used) in sorted(_index.items(), key=lambda item: item[1][3]):
            if now - fetched <= TTL and total + len(data) <= CACHE_BYTES and len(_index) < MAX_ITEMS:
                break
            _index.pop(old_key, None)
            path.unlink(missing_ok=True)
            total -= size
        path = _directory() / f"{key}.{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
        old = _index.pop(key, None)
        if old and old[0] != path:
            old[0].unlink(missing_ok=True)
        temporary = path.with_suffix(".part")
        temporary.write_bytes(data)
        temporary.replace(path)
        _index[key] = (path, len(data), path.stat().st_mtime, now)
    return _etag(path)


def fetch(url: str, loader: Callable[[str], Response]) -> tuple[bytes, str, str]:
    """The image, its type and ETag: from disk, or fetched once even when many tabs ask at the same time."""
    found = _lookup(url)
    if found:
        return found
    key = _key(url)
    while True:
        with _lock:
            failure = _failed.get(key)
            if failure and failure[0] > time.monotonic():
                raise failure[1]
            waiting = _inflight.get(key)
            if waiting is None:
                _inflight[key] = done = threading.Event()
                break
        waiting.wait(30)
        found = _lookup(url)
        if found:
            return found
    try:
        with _upstream:
            fetched = loader(url)
        data, mime = bytes(fetched.body), fetched.media_type or ""
        etag = _store(url, data, mime)
        return data, mime, etag
    except Exception as error:
        with _lock:
            if len(_failed) >= 512:
                _failed.clear()
            _failed[key] = (time.monotonic() + FAILURE_TTL, error)
        raise
    finally:
        with _lock:
            _inflight.pop(key, None)
        done.set()


def _serve(url: str, loader: Callable[[str], Response], if_none_match: str) -> Response:
    data, mime, etag = fetch(url, loader)
    headers = {"Cache-Control": "private, max-age=86400", "ETag": etag}
    if if_none_match and etag in [tag.strip() for tag in if_none_match.split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type=mime, headers=headers)


async def respond(request: Request, url: str, loader: Callable[[str], Response]) -> Response:
    """Serve one cover on the image pool; ``loader`` fetches and decodes it on a miss."""
    return await asyncio.get_running_loop().run_in_executor(
        pool, _serve, url, loader, request.headers.get("if-none-match", ""))


def clear() -> None:
    """Forget the index (tests)."""
    global _scanned_dir
    with _lock:
        _index.clear()
        _failed.clear()
        _scanned_dir = None
