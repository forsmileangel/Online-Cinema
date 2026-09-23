"""Small in-memory LRU for HLS segments so seeking back/nearby is instant."""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from queue import Full, Queue

from . import http_client
from .security import CDN_HOSTS, assert_hls_url

_lock = threading.Lock()
_cache: OrderedDict[str, bytes] = OrderedDict()
_MAX_ITEMS = 48
_MAX_EACH = 4_000_000
_SEG_RE = re.compile(r"(video)(\d+)(\.jpeg)$", re.I)
_q: Queue[tuple[str, str]] = Queue(maxsize=24)


def get(url: str) -> bytes | None:
    with _lock:
        data = _cache.get(url)
        if data is not None:
            _cache.move_to_end(url)
        return data


def put(url: str, data: bytes) -> None:
    if not data or len(data) > _MAX_EACH:
        return
    with _lock:
        _cache[url] = data
        _cache.move_to_end(url)
        while len(_cache) > _MAX_ITEMS:
            _cache.popitem(last=False)


def _prefetch_one(url: str, referer: str) -> None:
    if get(url) is not None:
        return
    try:
        assert_hls_url(url)
        r = http_client.fetch_bytes(url, referer=referer, allowed_hosts=CDN_HOSTS, timeout=20)
        try:
            put(url, r.content)
        finally:
            http_client.close_response(r)
    except Exception:
        return


def _worker() -> None:
    while True:
        url, referer = _q.get()
        _prefetch_one(url, referer)


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


def enqueue_next(url: str, referer: str, count: int = 3) -> None:
    m = _SEG_RE.search(url)
    if not m:
        return
    n = int(m.group(2))
    head = url[: m.start()]
    for nxt in range(n + 1, n + 1 + count):
        nxt_url = f"{head}video{nxt}.jpeg"
        if get(nxt_url) is not None:
            continue
        try:
            _q.put_nowait((nxt_url, referer))
        except Full:
            return
