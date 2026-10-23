"""Keep the next minutes of a TV's HLS stream downloaded on this computer.

Receivers (Chromecast, LG) buffer only a little and give up on a slow segment.
A request from a known receiver address (``add_receiver``) moves that receiver's
window to the segment just requested, and a few connections fill the segments after it
into ``seg_cache``. A receiver asking for a segment that is still downloading
waits for that copy (``seg_cache.fetching``) instead of starting another.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from . import next_buffer, seg_cache
from .security import SiteBusy

AHEAD_SECONDS = 180.0
WINDOW_BYTES = 300 * 1024 * 1024
WORKERS = 3
MAX_WINDOWS = 3
IDLE_SECONDS = 600
RATE_SECONDS = 60
FAILURE_BACKOFF = 10
_RX = re.compile(r"[A-Za-z0-9.:-]{1,80}")  # A receiver's address is the key.

_lock = threading.Condition(threading.Lock())
_windows: dict[str, "Window"] = {}
_receivers: set[str] = set()
_started = False


@dataclass
class Window:
    rx: str
    key: str
    anchor: int
    touched: float = field(default_factory=time.monotonic)
    convert: bool = False
    web: bool = False
    error: str = ""
    inflight: set = field(default_factory=set)
    failed: dict = field(default_factory=dict)  # url -> monotonic time it may be retried
    fetches: list = field(default_factory=list)  # (start, end, media seconds)


def add_receiver(host: str | None) -> None:
    """Remember a cast device's address: its segment requests are read ahead for."""
    if host:
        _receivers.add(host)


def is_receiver(host: str | None) -> bool:
    return bool(host) and host in _receivers


def valid_marker(rx: str | None) -> str:
    return rx if rx and _RX.fullmatch(rx) else ""


def touch(url: str, rx: str, *, convert: bool = False, web: bool = False) -> None:
    """A receiver asked for this segment: read ahead from here."""
    rx = valid_marker(rx)
    found = seg_cache.position(url)
    if not rx or not found:
        return
    key, index = found
    with _lock:
        window = _windows.get(rx)
        if window is None or window.key != key:
            window = _windows[rx] = Window(rx, key, index)
            while len(_windows) > MAX_WINDOWS:
                oldest = min(_windows.values(), key=lambda w: w.touched)
                del _windows[oldest.rx]
        window.anchor, window.touched = index, time.monotonic()
        window.convert, window.web = convert, web
        _lock.notify_all()
    _start()


def _start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    for number in range(WORKERS):
        threading.Thread(target=_worker, name=f"tv-read-ahead-{number}", daemon=True).start()


def _plan(window: Window):
    """Walk the window: (next url to fetch, its seconds) and how much is already cached."""
    urls, seconds = seg_cache.playlist(window.key)
    ahead = covered = 0.0
    held = 0
    now = time.monotonic()
    for index in range(window.anchor + 1, len(urls)):
        if covered >= AHEAD_SECONDS or held >= WINDOW_BYTES:
            break
        url = urls[index]
        duration = seconds[index] if index < len(seconds) else 0.0
        size = seg_cache.size(url)
        if size is not None:
            held += size
            covered += duration
            continue
        if url in window.inflight or seg_cache.downloading(url) or window.failed.get(url, 0) > now:
            covered += duration
            continue
        if seg_cache.cooling(url):
            return None
        return url, duration
    return None


def _next_job():
    now = time.monotonic()
    for rx in [w.rx for w in _windows.values() if now - w.touched > IDLE_SECONDS]:
        del _windows[rx]
    # The most recently active stream goes first.
    for window in sorted(_windows.values(), key=lambda w: -w.touched):
        job = _plan(window)
        if job:
            return window, job
    return None


def _worker() -> None:
    while True:
        with _lock:
            job = _next_job()
            while job is None:
                _lock.wait(timeout=2)
                job = _next_job()
            window, (url, duration) = job
            window.inflight.add(url)
        try:
            _fetch(window, url, duration)
        finally:
            with _lock:
                window.inflight.discard(url)
                _lock.notify_all()


def _fetch(window: Window, url: str, duration: float) -> None:
    from . import hls_proxy
    started = time.monotonic()
    # Offline downloads and next-episode warm-up yield while the TV is filled.
    next_buffer.foreground(True)
    try:
        with seg_cache.fetching(url) as owner:
            if not owner or seg_cache.size(url) is not None:
                return
            referer, impersonate = hls_proxy._media_context(url)
            data = hls_proxy._fetch_segment(url, referer, impersonate, time.monotonic() + 30)
            seg_cache.put(url, data)
        with _lock:
            window.error = ""
            window.fetches.append((started, time.monotonic(), duration))
            del window.fetches[:-64]
        if window.convert:
            from .nesthub import transcode
            transcode(url, data, web=window.web)  # The receiver gets the 720p copy at once.
    except SiteBusy as exc:
        with _lock:
            window.error = str(exc)  # _fetch_segment already paused this host.
    except Exception as exc:
        with _lock:
            window.error = str(exc) or "預先緩衝失敗"
            window.failed[url] = time.monotonic() + FAILURE_BACKOFF
    finally:
        next_buffer.foreground(False)


def _ratio(fetches: list, now: float) -> float | None:
    """Media seconds downloaded per second spent downloading, over the last minute."""
    recent = [(max(start, now - RATE_SECONDS), end, media) for start, end, media in fetches if end > now - RATE_SECONDS]
    if not recent:
        return None
    busy, edge = 0.0, None
    for start, end, _ in sorted(recent):
        if edge is None or start > edge:
            busy += end - start
            edge = end
        elif end > edge:
            busy += end - edge
            edge = end
    media = sum(media for _, _, media in recent)
    return media / busy if busy > 0 else None


def status(rx: str) -> dict | None:
    """How far ahead of the receiver this computer holds the stream."""
    with _lock:
        window = _windows.get(valid_marker(rx) or "\0")
        if window is None:
            return None
        urls, seconds = seg_cache.playlist(window.key)
        lead = 0.0
        held = 0
        for index in range(window.anchor + 1, len(urls)):
            size = seg_cache.size(urls[index])
            if size is None:
                break
            held += size
            lead += seconds[index] if index < len(seconds) else 0.0
        end = window.anchor + 1 >= len(urls) or all(seg_cache.size(u) is not None for u in urls[window.anchor + 1:])
        return dict(lead_seconds=round(lead, 1), bytes=held, ratio=_ratio(window.fetches, time.monotonic()),
                    full=end or lead >= AHEAD_SECONDS - 1 or held >= WINDOW_BYTES, error=window.error)


def prime(rx: str, upstream: str, position: float, *, convert: bool = False, web: bool = False,
          wait: float = 8.0) -> dict | None:
    """Fill a media playlist from a playback position before a receiver loads it."""
    from . import hls_proxy
    rx = valid_marker(rx)
    if not rx:
        return None
    text, base = hls_proxy._read_playlist(upstream, time.monotonic() + 10)
    segments: list[str] = []
    seconds: list[float] = []
    hls_proxy.rewrite_playlist(text, base, segments=segments, durations=seconds)
    if not segments:
        return None  # A master playlist: the receiver's own requests open the window.
    seg_cache.remember(segments, seconds)
    index, elapsed = 0, 0.0
    for index, duration in enumerate(seconds):
        if elapsed + duration > position:
            break
        elapsed += duration
    with _lock:
        # The anchor sits just before the segment at the position, so that one is fetched first.
        _windows[rx] = Window(rx, segments[0], index - 1, convert=convert, web=web)
        while len(_windows) > MAX_WINDOWS:
            oldest = min(_windows.values(), key=lambda w: w.touched)
            del _windows[oldest.rx]
        _lock.notify_all()
    _start()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        state = status(rx)
        if not state or state["full"] or state["lead_seconds"] >= 20:
            return state
        time.sleep(0.25)
    return status(rx)


def forget(rx: str) -> None:
    with _lock:
        _windows.pop(valid_marker(rx) or "\0", None)
