"""Small in-memory LRU for HLS segments so seeking back/nearby is instant."""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from queue import Full, Queue
from urllib.parse import urlparse

from . import http_client
from .security import SiteBusy, assert_hls_url, hls_allowed_hosts

_lock = threading.Lock()
_cache: OrderedDict[str, bytes] = OrderedDict()
_bytes = 0
_MAX_BYTES = 160 * 1024 * 1024
_MAX_EACH = 16 * 1024 * 1024
_SEG_RE = re.compile(r"(video)(\d+)(\.jpeg)$", re.I)
_q: Queue[str] = Queue(maxsize=24)
# One download per URL: browser retries and prefetch wait for the first copy.
_inflight: dict[str, threading.Event] = {}
# Segment order of recent media playlists, keyed by their first segment.
_PLAYLISTS = 6
_playlists: OrderedDict[str, list[str]] = OrderedDict()
_position: dict[str, tuple[str, int]] = {}
# A rejected/throttled host is never prefetched again until it cools down.
_COOLDOWN = 300
_cooldown: dict[str, float] = {}


def get(url: str) -> bytes | None:
    with _lock:
        data = _cache.get(url)
        if data is not None:
            _cache.move_to_end(url)
        return data


def put(url: str, data: bytes) -> None:
    global _bytes
    if not data or len(data) > _MAX_EACH:
        return
    with _lock:
        old = _cache.pop(url, None)
        if old is not None:
            _bytes -= len(old)
        _cache[url] = data
        _bytes += len(data)
        while _bytes > _MAX_BYTES and _cache:
            _, gone = _cache.popitem(last=False)
            _bytes -= len(gone)


@contextmanager
def fetching(url: str):
    """Yield True when this caller owns the download; store before leaving."""
    with _lock:
        owner = None if url in _inflight else threading.Event()
        if owner is not None:
            _inflight[url] = owner
    try:
        yield owner is not None
    finally:
        if owner is not None:
            with _lock:
                _inflight.pop(url, None)
            owner.set()


def wait(url: str, timeout: float) -> bytes | None:
    """Wait for an in-flight download of the same URL, if there is one."""
    with _lock:
        event = _inflight.get(url)
    if event is None:
        return None
    event.wait(max(0.0, timeout))
    return get(url)


def remember(segments: list[str]) -> None:
    if not segments:
        return
    key = segments[0]
    with _lock:
        _forget(key, _playlists.pop(key, None) or [])
        _playlists[key] = list(segments)
        for index, url in enumerate(segments):
            _position[url] = (key, index)
        while len(_playlists) > _PLAYLISTS:
            _forget(*_playlists.popitem(last=False))


def _forget(key: str, urls: list[str]) -> None:
    for url in urls:
        if _position.get(url, ("",))[0] == key:
            del _position[url]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def pause(url: str, seconds: float = _COOLDOWN) -> None:
    with _lock:
        _cooldown[_host(url)] = time.monotonic() + seconds


def _cooling(url: str) -> bool:
    with _lock:
        return _cooldown.get(_host(url), 0) > time.monotonic()


def _prefetch_one(url: str) -> None:
    if get(url) is not None or _cooling(url):
        return
    from . import hls_proxy
    with fetching(url) as owner:
        if not owner:
            return
        try:
            assert_hls_url(url)
            referer, impersonate = hls_proxy._media_context(url)
            r = http_client.fetch_bytes(url, referer=referer, allowed_hosts=hls_allowed_hosts(url), timeout=6, stream=True,
                                        impersonate=impersonate, redirect_validator=hls_proxy._playlist_media_url)
            try:
                put(url, hls_proxy._read_segment(r, time.monotonic() + 20))
            finally:
                http_client.close_response(r)
        except SiteBusy:
            pause(url)
        except Exception:
            return


def _worker() -> None:
    while True:
        _prefetch_one(_q.get())


_started = False
_start_lock = threading.Lock()


def start_workers() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
        for _ in range(2):
            threading.Thread(target=_worker, name="hls-prefetch", daemon=True).start()


def _following(url: str, count: int) -> list[str]:
    with _lock:
        hit = _position.get(url)
        if hit:
            key, index = hit
            return _playlists[key][index + 1:index + 1 + count]
    m = _SEG_RE.search(url)
    if not m:
        return []
    n = int(m.group(2))
    head = url[: m.start()]
    return [f"{head}video{nxt}.jpeg" for nxt in range(n + 1, n + 1 + count)]


def enqueue_next(url: str, count: int = 3) -> None:
    if _cooling(url):
        return
    for nxt_url in _following(url, count):
        if get(nxt_url) is not None:
            continue
        with _lock:
            if nxt_url in _inflight:
                continue
        try:
            _q.put_nowait(nxt_url)
        except Full:
            return
