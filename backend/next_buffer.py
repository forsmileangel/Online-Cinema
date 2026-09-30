"""Low-priority, bounded disk cache for the next ten minutes of one HLS episode."""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse

from . import http_client, settings
from .security import UnsafeURL, assert_hls_url, hls_allowed_hosts

TARGET_SECONDS = 600.0
CACHE_BYTES = 1024 * 1024 * 1024
CACHE_TTL = 7200
MAX_SEGMENT = 32 * 1024 * 1024
_disk_lock = threading.RLock()
_lock = threading.RLock()
_jobs = {}
_started = False
_foreground = 0
_last_foreground = 0.0


def _directory():
    path = settings.app_dir() / 'next-episode-cache'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(url):
    return _directory() / (hashlib.sha256(url.encode()).hexdigest() + '.bin')


def cached(url):
    with _disk_lock:
        path = _path(url)
        try:
            if time.time() - path.stat().st_mtime > CACHE_TTL:
                path.unlink(missing_ok=True)
                return None
            data = path.read_bytes()
            os.utime(path, None)
            return data
        except FileNotFoundError:
            return None


def _store(url, data):
    if not data or len(data) > MAX_SEGMENT:
        raise ValueError('分段過大，已停止預載')
    with _disk_lock:
        files = sorted(_directory().glob('*.bin'), key=lambda p: p.stat().st_mtime)
        now, total = time.time(), sum(p.stat().st_size for p in files)
        for path in files:
            stat = path.stat()
            if now - stat.st_mtime > CACHE_TTL or total + len(data) > CACHE_BYTES:
                total -= stat.st_size
                path.unlink(missing_ok=True)
        path = _path(url)
        temporary = path.with_suffix('.part')
        temporary.write_bytes(data)
        temporary.replace(path)


def foreground(active):
    global _foreground, _last_foreground
    with _lock:
        _foreground = max(0, _foreground + (1 if active else -1))
        _last_foreground = time.monotonic()


@dataclass
class Job:
    owner: str
    url: str
    height: int
    created: float = field(default_factory=time.monotonic)
    touched: float = field(default_factory=time.monotonic)
    ready: bool = False
    phase: str = 'waiting'
    seconds: float = 0
    target: float = TARGET_SECONDS
    error: str = ''
    items: list | None = None
    index: int = 0
    size: int = 0


def _snapshot(job):
    if job.phase == 'complete' and job.items:
        now = time.time()
        try:
            available = all(now - _path(url).stat().st_mtime < CACHE_TTL for url, _ in job.items)
        except OSError:
            available = False
        if not available:
            job.phase, job.seconds, job.error = 'error', 0, '預載暫存已釋放，請重試預載'
    return dict(phase=job.phase, seconds=job.seconds, target=job.target, error=job.error)


def update(owner, proxy, ready, height=0, retry=False):
    global _started
    if not re.fullmatch(r'[A-Za-z0-9:-]{1,80}', owner):
        raise ValueError('invalid buffer owner')
    parsed = urlparse(proxy)
    urls = parse_qs(parsed.query).get('u', [])
    if parsed.scheme or parsed.netloc or parsed.path != '/api/hls' or len(urls) != 1:
        raise UnsafeURL('invalid buffer source')
    url = assert_hls_url(urls[0])
    with _lock:
        now = time.monotonic()
        for key, old in list(_jobs.items()):
            if now - old.touched > 60:
                del _jobs[key]
        job = _jobs.get(owner)
        if not job or job.url != url or job.height != height or retry or now - job.created >= CACHE_TTL:
            if owner not in _jobs and len(_jobs) >= 8:
                return dict(phase='waiting', seconds=0, target=600, error='預載工作已滿')
            job = _jobs[owner] = Job(owner, url, height)
        if not urlparse(url).path.lower().endswith('.m3u8'):
            job.phase, job.error = 'unsupported', '此片源暫不支援下一集預載'
        job.touched, job.ready = now, bool(ready)
        if job.phase not in ('complete', 'error', 'unsupported'):
            job.phase = 'loading' if ready else 'waiting'
        if not _started:
            _started = True
            threading.Thread(target=_worker, name='next-episode-buffer', daemon=True).start()
        return _snapshot(job)


def cancel(owner):
    with _lock:
        _jobs.pop(owner, None)


def status(owner):
    with _lock:
        job = _jobs.get(owner)
        return _snapshot(job) if job else dict(phase='waiting', seconds=0, target=600, error='')


def _allowed(job):
    with _lock:
        return (_jobs.get(job.owner) is job and job.ready and time.monotonic() - job.touched < 12
                and not _foreground and time.monotonic() - _last_foreground > 1)


class Deferred(Exception):
    pass


def _prepare(job):
    from . import hls_proxy
    url = job.url
    manifests = []
    deadline = time.monotonic() + 20
    for _ in range(4):
        if not _allowed(job):
            raise Deferred()
        text, base = hls_proxy._read_playlist(url, deadline)
        hls_proxy.rewrite_playlist(text, base)
        if '#EXT-X-MEDIA:' in text and re.search(r'TYPE=AUDIO[^\n]*URI=', text):
            raise ValueError('分離音軌片源暫不支援下一集預載')
        variants, pending = [], None
        for line in text.splitlines():
            if line.startswith('#EXT-X-STREAM-INF:'):
                pending = dict(hls_proxy._ATTR.findall(line))
            elif line.strip() and not line.startswith('#') and pending is not None:
                resolution = re.fullmatch(r'\d+x(\d+)', pending.get('RESOLUTION', '').strip('"'))
                variants.append((int(resolution[1]) if resolution else 0, urljoin(base, line.strip())))
                pending = None
        manifests.append((url, base, text))
        if not variants:
            break
        eligible = [v for v in variants if 0 < v[0] <= job.height] if job.height else []
        # An auto/unknown rendition cannot be predicted; warm the highest one.
        url = max(eligible or variants)[1]
        hls_proxy._playlist_media_url(url, urlparse(base).hostname or '')
    else:
        raise ValueError('播放清單層數過多，暫不支援預載')
    if '#EXT-X-ENDLIST' not in text or '#EXT-X-BYTERANGE' in text:
        raise ValueError('直播或範圍分段片源暫不支援下一集預載')
    items, seconds, duration = [], 0.0, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(('#EXT-X-KEY:', '#EXT-X-MAP:')):
            attrs = {k: v.strip('"') for k, v in hls_proxy._ATTR.findall(line)}
            if line.startswith('#EXT-X-KEY:') and attrs.get('METHOD') not in ('NONE', 'AES-128'):
                raise ValueError('此加密格式暫不支援下一集預載')
            if attrs.get('BYTERANGE'):
                raise ValueError('範圍分段片源暫不支援下一集預載')
            if attrs.get('URI'):
                items.append((hls_proxy._playlist_media_url(urljoin(base, attrs['URI']), urlparse(base).hostname or ''), 0))
        elif line.startswith('#EXTINF:'):
            duration = float(line.split(':', 1)[1].split(',')[0])
            if not 0 < duration <= 600:
                raise ValueError('無效的分段長度')
        elif line and not line.startswith('#'):
            if duration is None:
                raise ValueError('缺少分段長度')
            items.append((hls_proxy._playlist_media_url(urljoin(base, line), urlparse(base).hostname or ''), duration))
            seconds += duration
            duration = None
            if seconds >= TARGET_SECONDS:
                break
    if not items or not seconds:
        raise ValueError('沒有可預載的影片分段')
    for original, base, text in manifests:
        # Normalize relative URLs when an upstream manifest redirected.
        if original != base:
            text = '\n'.join(urljoin(base, line.strip()) if line.strip() and not line.startswith('#') else
                             hls_proxy._URI_ATTR.sub(lambda m: 'URI="' + urljoin(base, m[1]) + '"', line)
                             for line in text.splitlines()) + '\n'
        _store(original, text.encode())
    job.items, job.target = items, seconds


def _step(job):
    from . import hls_proxy
    if job.items is None:
        _prepare(job)
        return
    url, duration = job.items[job.index]
    if not _allowed(job):
        raise Deferred()
    data = cached(url)
    if data is None:
        assert_hls_url(url)
        referer, impersonate = hls_proxy._media_context(url)
        response = http_client.fetch_bytes(url, referer=referer, allowed_hosts=hls_allowed_hosts(url),
                                           timeout=8, stream=True, impersonate=impersonate,
                                           redirect_validator=hls_proxy._playlist_media_url)
        try:
            if response.status_code != 200:
                raise ValueError('預載分段回應不完整')
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if not _allowed(job):
                    raise Deferred()
                if len(data) + len(chunk) > MAX_SEGMENT:
                    raise ValueError('分段過大，已停止預載')
                data.extend(chunk)
            if bytes(data).lstrip().lower().startswith((b'<!doctype', b'<html')):
                raise ValueError('來源回傳錯誤頁，已停止預載')
            if job.size + len(data) > CACHE_BYTES * 0.8:
                raise ValueError('已達預載容量上限，保留已下載的部分')
            _store(url, data)
        finally:
            http_client.close_response(response)
    job.size += len(data)
    job.seconds += duration
    job.index += 1
    if job.index == len(job.items):
        job.phase = 'complete'


def _work(job):
    if job.phase in ('complete', 'error', 'unsupported') or not _allowed(job):
        return
    try:
        _step(job)
    except Deferred:
        pass
    except Exception as exc:
        job.phase, job.error = 'error', str(exc)
        # Never automatically retry a denied/throttled source.


def _worker():
    while True:
        with _lock:
            jobs = list(_jobs.values())
        for job in jobs:
            _work(job)
        time.sleep(0.25)
