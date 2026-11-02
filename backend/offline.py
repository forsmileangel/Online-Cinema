"""Explicit episode downloads, shared by the two apps; only complete files play."""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import msvcrt
import os
import posixpath
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from . import hls_proxy, http_client, sites, next_buffer, catalog
from .models import VideoDetail
from .security import (HostUnresolvable, SourceUnavailable, UnsafeURL, SiteBusy, assert_hls_url,
                       hls_allowed_hosts)

ROOT = Path(r'D:\AI工作區\離線影片')
# Only user-selected metadata is queued; the single worker downloads serially.
_queue = queue.Queue()
_worker_lock = threading.Lock()
_started = False
_maintenance_started = False
_cleanup_check_at = 0.0
_lifecycle_lock = threading.RLock()
_playback_parts = {}


def identity(source, video_id, episode):
    return hashlib.sha256(json.dumps([source, video_id, episode or ''], ensure_ascii=False).encode()).hexdigest()


def _folder(key):
    if not re.fullmatch(r'[0-9a-f]{64}', key):
        raise ValueError('無效的下載識別碼')
    path = ROOT / key
    # Never follow an externally replaced folder/junction outside the library.
    if path.resolve().parent != ROOT.resolve():
        raise ValueError('無效的下載資料夾')
    return path


def _read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _write(path, value):
    temporary = path.with_name(f'delete.{os.getpid()}.{threading.get_ident()}.tmp') if path.name == 'delete.json' else path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    # Windows readers/virus scanners can briefly hold the destination open.
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.05 * (attempt + 1))


def _claim(folder):
    for attempt in range(2):
        folder.mkdir(parents=True, exist_ok=True)
        try:
            handle = (folder / 'worker.lock').open('a+b')
            break
        except FileNotFoundError:
            # The empty-folder sweep may remove the folder between mkdir and open.
            if attempt:
                raise
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        handle.close()
        return None


def _release(handle):
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def media_path(key):
    folder = _folder(key)
    record = _read(folder / 'complete.json')
    if (folder / 'delete.json').exists():
        raise FileNotFoundError('影片正在刪除')
    path = folder / 'video.mp4'
    try:
        if (record and record.get('size', 0) > 0 and path.resolve().parent == folder.resolve()
                and path.stat().st_size == record['size']):
            return path
    except OSError:
        pass
    raise FileNotFoundError('沒有完整的本地影片，請重新下載')


def media_url(key):
    return f'/api/offline/media/{key}.mp4'


def media_key(url):
    parsed = urlparse(url)
    match = re.fullmatch(r'/api/offline/media/([0-9a-f]{64})\.mp4', parsed.path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or not match:
        return None
    media_path(match[1])
    return match[1]


def status(key):
    folder = _folder(key)
    deletion = _read(folder / 'delete.json')
    if deletion:
        return {**deletion['item'], 'phase': 'delete_error' if deletion.get('error') else 'deleting',
                'error': deletion.get('error', '')}
    record = _read(folder / 'complete.json')
    if record:
        try:
            media_path(key)
            return {**record['item'], 'phase': 'complete', 'progress': 100, 'error': '', 'url': media_url(key), 'size': record['size']}
        except FileNotFoundError:
            pass
    item = _read(folder / 'status.json')
    if not item:
        return None
    if item.get('error'):
        item['error'] = _friendly(item['error'])  # Older records kept raw curl text.
    if item['phase'] in ('queued', 'downloading', 'retrying', 'preparing'):
        handle = _claim(folder)
        if handle:
            _release(handle)
            if (folder / 'cancel').exists():
                return {**item, 'phase': 'cancelled', 'error': _cancel_message(folder)}
            # A deferred retry keeps saying why it waits.
            reason = item.get('error') if item['phase'] == 'retrying' else ''
            return {**item, 'phase': 'retrying', 'error': reason or '已保留進度，等待自動續傳'}
        if (folder / 'cancel').exists():
            return {**item, 'phase': 'cancelling', 'error': '正在取消下載，等待目前連線結束；已下載部分保留'}
    if record:
        return {**item, 'phase': 'error', 'error': '本地檔案遺失或不完整，請重新下載'}
    return item


def listing(sources, source=None, video_id=None):
    _start_maintenance()
    result = []
    if ROOT.exists():
        for path in ROOT.iterdir():
            if not re.fullmatch(r'[0-9a-f]{64}', path.name) or not path.is_dir():
                continue
            try:
                item = status(path.name)
            except (OSError, ValueError):
                continue
            if item and item['source'] in sources and (not source or item['source'] == source) and (not video_id or item['video_id'] == video_id):
                result.append(item)
    return sorted(result, key=lambda item: item.get('created', 0), reverse=True)


ACTIVE = frozenset({'queued', 'preparing', 'downloading', 'retrying', 'cancelling', 'deleting'})


def active_count() -> int:
    """Jobs a restart would interrupt (they resume afterwards, but the restart is better timed around them)."""
    count = 0
    if ROOT.exists():
        for path in ROOT.iterdir():
            if not re.fullmatch(r'[0-9a-f]{64}', path.name) or not path.is_dir():
                continue
            try:
                item = status(path.name)
            except (OSError, ValueError):
                continue
            if item and item.get('phase') in ACTIVE:
                count += 1
    return count


def local_detail(source, video_id, episode=None):
    # The first episode remains the default; opening a library link is explicit.
    candidates = [identity(source, video_id, episode)] if episode else [i['id'] for i in listing({source}, source, video_id) if i['phase'] == 'complete']
    for key in candidates:
        try:
            media_path(key)
            record = _read(_folder(key) / 'complete.json')
            detail = VideoDetail.model_validate(record['detail'])
            selected = episode or (detail.episodes[0].id if detail.episodes else '')
            if record['item']['episode'] != selected:
                continue
            detail.resolved_episode_id = selected or None
            detail.playlist = media_url(key)
            # Never reuse expired online URLs saved when downloading.
            for ep in detail.episodes:
                ep.playlist = ''
            return overlay(detail)
        except (OSError, ValueError, KeyError):
            continue
    return None


def overlay(detail):
    for ep in detail.episodes:
        key = identity(detail.source, detail.id, ep.id)
        try:
            media_path(key)
            ep.playlist = media_url(key)
            if ep.id == detail.resolved_episode_id:
                detail.playlist = ep.playlist
        except FileNotFoundError:
            pass
    if not detail.episodes:
        key = identity(detail.source, detail.id, '')
        try:
            media_path(key)
            detail.playlist = media_url(key)
        except FileNotFoundError:
            pass
    return detail


def fetch_detail(source, video_id, episode=None):
    local = local_detail(source, video_id, episode)
    if local:
        return local
    fetch = sites.get(source).fetch_video
    params = inspect.signature(fetch).parameters.values()
    takes_ep = any(p.name == 'ep' or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
    detail = fetch(video_id, ep=episode) if takes_ep else fetch(video_id)
    detail.source = source
    return overlay(detail)


def enqueue(source, video_id, episode='', height=720, restart=False, title='', episode_title=''):
    _start_maintenance()
    key = identity(source, video_id, episode)
    existing = status(key)
    if existing and existing['phase'] not in ('error', 'cancelled') and not (restart and existing['phase'] == 'retrying'):
        return existing
    folder = _folder(key)
    handle = _claim(folder)
    if handle is None:
        return status(key) or {'id': key, 'phase': 'queued'}
    if (folder / 'delete.json').exists():
        _release(handle)
        return status(key)
    # The page's names show in the queue until the source detail replaces them.
    title, episode_title = title.strip() or video_id, episode_title.strip() or episode
    item = dict(id=key, source=source, video_id=video_id, episode=episode, title=title,
                episode_title=episode_title, height=height, phase='queued', progress=0, error='', created=time.time())
    if existing:
        # Records queued before names were sent still hold the raw ids.
        item.update(title=existing['title'] if existing.get('title') not in (None, '', video_id) else title,
                    episode_title=existing['episode_title'] if existing.get('episode_title') not in (None, '', episode) else episode_title,
                    progress=0 if restart else existing.get('progress', 0),
                    height=height if restart else existing.get('height', height))
    try:
        if restart:
            work = folder / 'parts'
            if work.exists():
                if work.resolve().parent != folder.resolve() or work.is_symlink():
                    raise ValueError('無效的下載資料夾')
                shutil.rmtree(work)
        (folder / 'cancel').unlink(missing_ok=True)
        _write(folder / 'status.json', item)
        _queue.put_nowait((item, handle))
    except Exception:
        _release(handle)
        raise ValueError('無法加入下載佇列，請確認離線資料夾可寫入')
    _start_worker()
    return item


def _start_worker():
    global _started
    with _worker_lock:
        if not _started:
            _started = True
            threading.Thread(target=_worker, name='offline-download', daemon=True).start()


def _remove_queued(key):
    with _queue.mutex:
        entry = next((entry for entry in _queue.queue if entry[0]['id'] == key), None)
        if entry:
            folder = _folder(key)
            _write(folder / 'status.json', {**entry[0], 'phase': 'cancelled', 'error': _cancel_message(folder)})
            _queue.queue.remove(entry)
            _queue.unfinished_tasks -= 1
            _queue.not_full.notify()
            if _queue.unfinished_tasks == 0:
                _queue.all_tasks_done.notify_all()
    if entry:
        _release(entry[1])


def _cancel_message(folder):
    try:
        return (folder / 'cancel').read_text(encoding='utf-8') or '已取消下載；已下載部分保留，可繼續下載'
    except OSError:
        return '已停止下載；已下載部分保留，可繼續下載'


def cancel(key, reason=''):
    item = status(key)
    if not item or item['phase'] not in ('queued', 'downloading', 'retrying', 'preparing', 'cancelling', 'deleting', 'error'):
        return
    folder = _folder(key)
    (folder / 'cancel').write_text(reason, encoding='utf-8')
    _remove_queued(key)


def playback(source, video_id, episode=''):
    """Actual playback only: metadata resolution/prefetch must not call this.

    The download keeps going (its connections yield to the player); the player
    may reuse the segments it has already verified.
    """
    key = identity(source, video_id, episode)
    item = status(key)
    if item and item['phase'] == 'complete':
        touch_played(key)
    if not item or item['phase'] in ('complete', 'deleting', 'delete_error'):
        return
    folder = _folder(key)
    work = folder / 'parts'
    if work.resolve().parent != folder.resolve() or work.is_symlink():
        return
    plan = _read(work / 'plan.json') or {}
    resources = {}
    for remote, filename in plan.get('resources', []):
        if re.fullmatch(r'\d{6}\.(ts|key|mp4)', filename):
            resources[remote] = filename
    if resources:
        with _lifecycle_lock:
            _playback_parts.pop(key, None)
            _playback_parts[key] = (time.monotonic(), resources)
            while len(_playback_parts) > 8:
                _playback_parts.pop(next(iter(_playback_parts)))


def cached_part(url):
    """Reuse exact, complete HLS resources; never expose an unfinished MP4."""
    with _lifecycle_lock:
        entries = list(_playback_parts.items())
    for key, (created, resources) in reversed(entries):
        if time.monotonic() - created > 7200 or url not in resources:
            continue
        try:
            folder = _folder(key)
            work = folder / 'parts'
            path = work / resources[url]
            if ((folder / 'delete.json').exists() or work.resolve().parent != folder.resolve()
                    or work.is_symlink() or path.is_symlink() or path.resolve().parent != work.resolve()):
                continue
            record = _read(path.with_name(path.name + '.download.json')) or {}
            if not record.get('complete') or record.get('url') != url or not 0 < record.get('size', 0) <= next_buffer.MAX_SEGMENT:
                continue
            # Validate the bytes returned, not an earlier hash of a mutable file.
            with path.open('rb') as stream:
                data = stream.read(next_buffer.MAX_SEGMENT + 1)
            if len(data) == record['size'] and hashlib.sha256(data).hexdigest() == record.get('sha256'):
                return data
        except (OSError, ValueError):
            continue
    return None


def _finish_delete(key):
    folder = _folder(key)
    marker = _read(folder / 'delete.json')
    if not marker:
        return True
    handle = _claim(folder)
    if handle is None:
        return False
    try:
        marker = _read(folder / 'delete.json')
        if not marker:
            return True  # Another app already finished while we acquired the lock.
        if 'auto_cutoff' in marker:
            record = _read(folder / 'complete.json')
            policy = _read(ROOT / 'retention.json') or {}
            if not policy.get('enabled') or not record or _last_played(folder, record, marker['auto_baseline']) >= marker['auto_cutoff']:
                (folder / 'delete.json').unlink(missing_ok=True)
                return True  # Recent playback or disabling cleanup cancels deletion.
        work = folder / 'parts'
        if work.resolve().parent != folder.resolve() or work.is_symlink():
            raise ValueError('無效的下載資料夾')
        # Retain the deletion journal AND status until every media file is gone.
        # A Windows file lock can then be retried without orphaning the item.
        for name in ('video.mp4', 'video.part.mp4', 'conversion.txt', 'conversion.log'):
            (folder / name).unlink(missing_ok=True)
        if work.exists():
            shutil.rmtree(work)
        for name in ('complete.json', 'status.json', 'complete.tmp', 'status.tmp', 'cancel', 'last-played'):
            (folder / name).unlink(missing_ok=True)
        (folder / 'delete.json').unlink(missing_ok=True)
        return True
    except Exception as exc:
        message = '檔案仍被使用，請停止播放後重試刪除' if isinstance(exc, PermissionError) else str(exc)
        _write(folder / 'delete.json', {**marker, 'error': message})
        raise ValueError(message) from exc
    finally:
        _release(handle)


def delete(keys, sources):
    deleted, pending, errors = [], [], []
    for key in dict.fromkeys(keys):
        try:
            with _lifecycle_lock:
                folder = _folder(key)
                item = status(key)
                if item is None:
                    deleted.append(key)  # Repeating a successful deletion is safe.
                    continue
                if item['source'] not in sources:
                    raise ValueError('找不到下載項目')
                _write(folder / 'delete.json', {'item': item, 'error': ''})
                cancel(key)
                (deleted if _finish_delete(key) else pending).append(key)
        except Exception as exc:
            errors.append({'id': key, 'error': str(exc)})
    _start_maintenance()
    return {'deleted': deleted, 'pending': pending, 'errors': errors}


def _maintenance_once():
    # Each app releases its own queued locks, including cancellation initiated
    # by the other app. A download blocked on network I/O cannot stall this.
    with _queue.mutex:
        queued = [entry[0]['id'] for entry in _queue.queue]
    for key in queued:
        if (_folder(key) / 'cancel').exists():
            _remove_queued(key)
    if ROOT.exists():
        for folder in ROOT.iterdir():
            if not re.fullmatch(r'[0-9a-f]{64}', folder.name):
                continue
            with _lifecycle_lock:
                marker = _read(_folder(folder.name) / 'delete.json')
                if marker and not marker.get('error'):
                    try:
                        _finish_delete(folder.name)
                    except (OSError, ValueError):
                        pass  # Persistent, visible error; explicit retry required.
    _resume_pending()
    _cleanup_expired()
    _sweep_empty_folders()


_sweep_at = 0.0


def _sweep_empty_folders():
    # A finished deletion leaves its folder with only the lock file behind.
    global _sweep_at
    if time.monotonic() < _sweep_at or not ROOT.exists():
        return
    _sweep_at = time.monotonic() + 600
    for folder in ROOT.iterdir():
        try:
            if (not re.fullmatch(r'[0-9a-f]{64}', folder.name) or folder.is_symlink() or not folder.is_dir()
                    or [path.name for path in _folder(folder.name).iterdir()] != ['worker.lock']):
                continue
            handle = _claim(folder)
            if handle is None:
                continue
            try:
                alone = [path.name for path in folder.iterdir()] == ['worker.lock']
            finally:
                _release(handle)
            if alone:
                # Fails harmlessly if either app opened the lock again meanwhile.
                (folder / 'worker.lock').unlink()
                folder.rmdir()
        except (OSError, ValueError):
            continue


def _resume_pending():
    # Persisted intent survives browser closure and process restarts. The file
    # lock is shared by both apps; never clear cancel/delete markers here.
    pending = []
    if not ROOT.exists():
        return
    for folder in ROOT.iterdir():
        if re.fullmatch(r'[0-9a-f]{64}', folder.name):
            try:
                item = _read(_folder(folder.name) / 'status.json')
                if item and item.get('id') == folder.name:
                    pending.append(item)
            except (OSError, ValueError):
                continue
    for candidate in sorted(pending, key=lambda item: item.get('created', 0)):
        folder = _folder(candidate['id'])
        def eligible(item):
            return (item and item.get('source') in catalog.SOURCES
                    and item.get('id') == identity(item['source'], item.get('video_id'), item.get('episode'))
                    and item.get('phase') in ('queued', 'downloading', 'retrying', 'preparing')
                    and item.get('retry_at', 0) <= time.time()
                    and not any((folder / name).exists() for name in ('cancel', 'delete.json', 'complete.json')))
        if not eligible(candidate):
            continue
        handle = _claim(folder)
        if handle is None:
            continue
        queued = False
        try:
            # Re-read after claiming: another app or the user may have stopped it.
            item = _read(folder / 'status.json')
            if not eligible(item):
                continue
            item.update(phase='queued', error='', retry_at=0)
            _write(folder / 'status.json', item)
            _queue.put_nowait((item, handle))
            queued = True
            _start_worker()
        finally:
            if not queued:
                _release(handle)


def cleanup_policy(enabled=None):
    defaults = dict(enabled=True, days=14, initialized_at=time.time(), last_run=0, deleted=0, errors=0)
    handle = _claim(ROOT)
    if handle is None:
        if enabled is not None:
            raise ValueError('清理設定正在更新，請稍後重試')
        return _read(ROOT / 'retention.json') or defaults
    try:
        policy = _read(ROOT / 'retention.json')
        changed = policy is None or enabled is not None
        policy = policy or defaults
        if enabled is not None:
            policy.update(enabled=bool(enabled), last_run=0)
        if changed:
            _write(ROOT / 'retention.json', policy)
        return policy
    finally:
        _release(handle)


def touch_played(key):
    folder = _folder(key)
    handle = _claim(folder)
    if handle is None:
        return
    try:
        if not _read(folder / 'complete.json'):
            return
        marker = folder / 'last-played'
        if marker.is_symlink():
            raise ValueError('無效的觀看紀錄路徑')
        marker.touch()
    finally:
        _release(handle)


def _last_played(folder, record, baseline):
    # Legacy files have no reliable episode-level history: grant a fresh 14 days.
    last = record.get('completed_at') or baseline
    marker = folder / 'last-played'
    if marker.is_symlink():
        return time.time()  # Do not follow or delete through a replaced marker.
    if marker.exists():
        last = max(last, marker.stat().st_mtime)
    return last


def _cleanup_expired():
    global _cleanup_check_at
    if time.monotonic() < _cleanup_check_at:
        return
    _cleanup_check_at = time.monotonic() + 60
    cleanup_policy()  # Initialize the shared policy before inspecting legacy files.
    handle = _claim(ROOT)
    if handle is None:
        return
    try:
        policy = _read(ROOT / 'retention.json')
        now = time.time()
        if not policy or not policy['enabled'] or now - policy['last_run'] < 3600:
            return
        cutoff = now - 14 * 86400
        deleted, errors = 0, 0
        for candidate in ROOT.iterdir():
            if not re.fullmatch(r'[0-9a-f]{64}', candidate.name):
                continue
            try:
                folder = _folder(candidate.name)
                record = _read(folder / 'complete.json')
                if (not record or (folder / 'delete.json').exists()
                        or _last_played(folder, record, policy['initialized_at']) >= cutoff):
                    continue
                item = status(candidate.name)
                if not item or item['phase'] != 'complete':
                    continue
                _write(folder / 'delete.json', dict(item=item, error='', auto_cutoff=cutoff,
                                                   auto_baseline=policy['initialized_at']))
                if _finish_delete(candidate.name) and status(candidate.name) is None:
                    deleted += 1
            except (OSError, ValueError):
                errors += 1
        _write(ROOT / 'retention.json', {**policy, 'last_run': now, 'deleted': deleted, 'errors': errors})
    finally:
        _release(handle)


MAINTENANCE_SECONDS = 15
_wake = threading.Event()


def _start_maintenance():
    """Start the maintenance thread, or nudge it: a new or deleted job is looked at now, not on the next pass."""
    global _maintenance_started
    _wake.set()
    with _worker_lock:
        if _maintenance_started:
            return
        _maintenance_started = True
        def maintain():
            while True:
                # Every pass lists the whole offline folder; once a second it kept two processes busy for nothing.
                _wake.wait(MAINTENANCE_SECONDS)
                _wake.clear()
                try:
                    _maintenance_once()
                except Exception:
                    logging.getLogger(__name__).exception('Offline cleanup failed')
        threading.Thread(target=maintain, name='offline-cleanup', daemon=True).start()


class Cancelled(Exception):
    pass


class _LaneStopped(Exception):
    """Another segment connection of the same episode failed or was cancelled."""


_lane = threading.local()


def _lane_check():
    # A failed sibling connection ends new requests and retries, but the segment
    # already being received is allowed to finish.
    stop = getattr(_lane, 'stop', None)
    if stop is not None and stop.is_set():
        raise _LaneStopped()


class LowDisk(ValueError):
    pass


def _check(folder):
    if (folder / 'cancel').exists() or (folder / 'delete.json').exists():
        raise Cancelled()
    if shutil.disk_usage(folder).free < 512 * 1024 * 1024:
        raise LowDisk('硬碟剩餘空間不足 512 MB，已停止下載')


def free_bytes():
    try:
        return shutil.disk_usage(ROOT if ROOT.exists() else ROOT.anchor).free
    except OSError:
        return None


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadInterrupted(ValueError):
    pass


class RetryLater(ValueError):
    def __init__(self, message, delay=300):
        super().__init__(message)
        self.delay = delay


def _transient(error):
    # A DNS failure is refused like other unsafe URLs, but it passes.
    if isinstance(error, (DownloadInterrupted, TimeoutError, ConnectionError, HostUnresolvable)):
        return True
    if isinstance(error, (Cancelled, UnsafeURL, SiteBusy)):
        return False
    requests = http_client.requests
    # 35/60: a CDN node with a broken TLS setup is retried later, never trusted.
    return isinstance(error, requests.RequestsError) and (
        error.code in {5, 6, 7, 16, 18, 28, 35, 52, 55, 56, 60, 92}
        or getattr(getattr(error, 'response', None), 'status_code', None) in {500, 502, 504})


_CURL_CODE = re.compile(r'curl: \((\d+)\)')
_CJK = re.compile(r'[㐀-鿿]')


def _friendly(error):
    """A short Chinese reason; library errors arrive as English curl text."""
    text = error if isinstance(error, str) else str(error)
    if isinstance(error, HostUnresolvable):
        return '片源網域暫時查不到'
    if isinstance(error, DownloadInterrupted):
        return '片源傳輸中斷'
    code = getattr(error, 'code', None) if isinstance(error, http_client.requests.RequestsError) else None
    match = _CURL_CODE.search(text)
    if not code and match:
        code = int(match[1])
    if code in (35, 60):
        return '片源伺服器的安全憑證暫時有問題'
    if code == 6:
        return '片源網域暫時查不到'
    if code == 28:
        return '連線逾時'
    if code:
        return '片源連線失敗'
    if isinstance(error, TimeoutError):
        return '連線逾時'
    if isinstance(error, ConnectionError):
        return '連線中斷'
    return text if _CJK.search(text) else '下載失敗，請重試'


def _wait_retry(folder, delay):
    until = time.monotonic() + delay
    while time.monotonic() < until:
        _lane_check()
        _check(folder)
        time.sleep(min(.25, max(0, until - time.monotonic())))
    _check(folder)


PLAYBACK_IDLE = 2
PLAYBACK_WAIT = 30
PARALLEL_SEGMENTS = 3
SOURCE_RETRIES = 3  # A source that stays down: retried after 10, 20 and 40 minutes.
LATER_LIMIT = 12  # Deferred runs without progress before the download is marked failed.


def _yield_to_playback(folder):
    """Let a playing browser or TV refill its buffer first, without stalling forever."""
    until = time.monotonic() + PLAYBACK_WAIT
    while not next_buffer.playback_idle(PLAYBACK_IDLE) and time.monotonic() < until:
        _check(folder)
        time.sleep(.25)


def _retry(operation, folder, on_retry=None, position=None):
    failures, attempts = 0, 0
    best = position() if position else 0
    while True:
        _check(folder)
        try:
            return operation()
        except Exception as error:
            if isinstance(error, SiteBusy) and error.status_code in (429, 503):
                raise RetryLater(str(error), max(300, error.retry_after or 0)) from error
            if not _transient(error):
                raise
            current = position() if position else 0
            failures = 0 if current > best else failures + 1
            best = max(best, current)
            if failures > 8:
                raise RetryLater(f'連線尚未恢復（{_friendly(error)}）；已保留進度，稍後自動續傳') from error
            attempts += 1
            delay = min(30, 2 ** max(1, failures))
            if on_retry: on_retry(attempts, delay, error)
            _wait_retry(folder, delay)


def _completed(path, url):
    previous = _read(path.with_name(path.name + '.download.json')) or {}
    if path.is_symlink() or not path.exists() or not previous.get('complete') or previous.get('url') != url:
        return False
    stat = path.stat()
    if stat.st_size <= 0 or stat.st_size != previous.get('size'):
        return False
    # The hash was checked when the download finished and its modification time recorded; a file
    # that still has that time is the same file. Only older records without it are hashed again.
    if previous.get('mtime') == stat.st_mtime:
        return True
    return _file_hash(path) == previous.get('sha256')


def _finished_record(path, record):
    """The metadata of a file that is complete now: size, hash and the time that proves it unchanged."""
    stat = path.stat()
    return {**record, 'complete': True, 'size': stat.st_size, 'sha256': _file_hash(path), 'mtime': stat.st_mtime}


def _download(url, path, folder, limit=None, *, resume_partial=False, progress=None, on_retry=None):
    return _retry(lambda: _download_once(url, path, folder, limit, resume_partial=resume_partial, progress=progress),
                  folder, on_retry, lambda: path.stat().st_size if path.exists() else 0)


def _download_once(url, path, folder, limit=None, *, resume_partial=False, progress=None):
    _lane_check()
    assert_hls_url(url)
    _check(folder)
    if path.is_symlink() or path.resolve().parent != path.parent.resolve():
        raise ValueError('無效的下載檔案路徑')
    metadata = path.with_name(path.name + '.download.json')
    previous = _read(metadata) or {}
    size = path.stat().st_size if path.exists() else 0
    if _completed(path, url):
        if progress: progress(100)
        return
    offset = 0
    validator = previous.get('etag', '')
    resumable = (previous.get('url') == url and isinstance(validator, str)
                 and validator.startswith('"') and validator.endswith('"')
                 and isinstance(previous.get('total'), int))
    if resumable and not previous.get('complete') and 0 <= previous.get('size', -1) < size:
        # A crash can leave bytes beyond the last durable checkpoint. Verify
        # that prefix before discarding only its uncommitted tail.
        committed = previous['size']
        digest = hashlib.sha256()
        with path.open('rb') as saved:
            remaining = committed
            while remaining:
                chunk = saved.read(min(1024 * 1024, remaining))
                if not chunk: break
                digest.update(chunk)
                remaining -= len(chunk)
        if not remaining and digest.hexdigest() == previous.get('sha256'):
            with path.open('r+b') as saved:
                saved.truncate(committed)
            size = committed
    if size and (resume_partial or resumable):
        # If-Range requires a strong validator. Never append an unknown/new file.
        if (previous.get('url') != url or not isinstance(validator, str) or not validator.startswith('"') or not validator.endswith('"')
                or not isinstance(previous.get('total'), int) or size > previous['total']
                or previous.get('size') != size or _file_hash(path) != previous.get('sha256')):
            raise ValueError('此片源無法安全續傳，請選「重新下載」')
        offset = size
        if offset == previous['total']:
            _write(metadata, _finished_record(path, previous))
            if progress: progress(100)
            return
    if not offset:
        cached = next_buffer.cached(url)
        if cached is not None:
            if limit and len(cached) > limit:
                raise ValueError('影片分段過大，已停止下載')
            path.write_bytes(cached)
            _write(metadata, _finished_record(path, dict(url=url)))
            if progress: progress(100)
            return
    referer, impersonate = hls_proxy._media_context(url)
    response = http_client.fetch_bytes(url, referer=referer, allowed_hosts=hls_allowed_hosts(url),
                                      timeout=20, stream=True, impersonate=impersonate,
                                      range_header=f'bytes={offset}-' if offset else None,
                                      if_range=validator if offset else None,
                                      redirect_validator=hls_proxy._playlist_media_url)
    record = None
    try:
        expected = response.headers.get('content-length', '')
        etag = response.headers.get('etag', '')
        if offset:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
            if (response.status_code != 206 or not match or int(match[1]) != offset
                    or int(match[3]) != previous['total'] or etag != validator
                    or int(match[2]) < offset or int(match[2]) >= int(match[3])
                    or (expected and (not expected.isdigit() or int(expected) != int(match[2]) - offset + 1))):
                raise ValueError('片源已變更或不支援續傳，請選「重新下載」')
            total, expected_bytes = int(match[3]), int(match[2]) - offset + 1
        else:
            if response.status_code in (500, 502, 504):
                raise DownloadInterrupted('來源暫時故障')
            if response.status_code == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
                if (not match or int(match[1]) != 0 or int(match[2]) >= int(match[3])
                        or (expected and (not expected.isdigit() or int(expected) != int(match[2]) + 1))):
                    raise ValueError('來源回傳無效的下載範圍')
                total, expected_bytes = int(match[3]), int(match[2]) + 1
            elif response.status_code == 200:
                if expected and not expected.isdigit():
                    raise ValueError('來源回傳無效的下載長度')
                total = int(expected) if expected else None
                expected_bytes = total
            else:
                raise ValueError(f'來源拒絕下載（HTTP {response.status_code}）')
        if limit and total and total > limit:
            raise ValueError('影片分段過大，已停止下載')
        digest = hashlib.sha256()
        if offset:
            with path.open('rb') as saved:
                for chunk in iter(lambda: saved.read(1024 * 1024), b''):
                    digest.update(chunk)
        record = dict(url=url, etag=etag, total=total, complete=False, size=offset, sha256=digest.hexdigest())
        size = checkpoint = offset
        with path.open('ab' if offset else 'wb') as out:
            _write(metadata, record)
            if progress and total: progress(round(offset * 100 / total, 1))
            for chunk in response.iter_content(chunk_size=256 * 1024):
                _check(folder)
                if not chunk: continue
                if size == offset and chunk.lstrip().lower().startswith((b'<!doctype', b'<html')):
                    raise ValueError('來源回傳錯誤頁')
                if limit and out.tell() + len(chunk) > limit:
                    raise ValueError('影片分段過大，已停止下載')
                if total and out.tell() + len(chunk) > total:
                    raise ValueError('影片下載長度與來源不符')
                out.write(chunk)
                digest.update(chunk)
                size = out.tell()
                if size - checkpoint >= 4 * 1024 * 1024:
                    out.flush()
                    _write(metadata, {**record, 'size': size, 'sha256': digest.hexdigest()})
                    checkpoint = size
                if progress and total: progress(round(size * 100 / total, 1))
        size = path.stat().st_size
        if not size or (expected_bytes is not None and size - offset != expected_bytes) or (total and size != total):
            raise DownloadInterrupted('影片下載不完整，正在自動續傳')
        record['complete'] = True
    finally:
        http_client.close_response(response)
        if record is not None and path.exists():
            stat = path.stat()
            record.update(size=stat.st_size, sha256=_file_hash(path), mtime=stat.st_mtime)
            _write(metadata, record)


_SEGMENT_TAGS = ('#EXTINF:', '#EXT-X-PROGRAM-DATE-TIME:', '#EXT-X-BITRATE:', '#EXT-X-GAP')


def _is_discontinuity(line):
    return line == '#EXT-X-DISCONTINUITY'


def _without_ads(plan):
    """Drop short clips spliced in from another folder between discontinuities.

    Some CDNs insert an advert served by a different host; it adds nothing to
    the episode and its host failing would stop the download. Anything less
    clear-cut than that is kept unchanged.
    """
    remote = {filename: address for address, filename in plan['resources']}
    lines = plan['lines']
    blocks, segments, seconds, tagged = [], [], 0.0, False
    for index, line in enumerate(lines + ['#EXT-X-DISCONTINUITY']):
        if _is_discontinuity(line):
            blocks.append((segments, tagged))
            segments, tagged = [], False
        elif line.startswith(('#EXT-X-KEY:', '#EXT-X-MAP:')):
            tagged = True
        elif line.startswith('#EXTINF:'):
            seconds = float(line.split(':', 1)[1].split(',')[0])
        elif line and not line.startswith('#'):
            parsed = urlparse(remote.get(line, ''))
            segments.append((seconds, (parsed.hostname or '', posixpath.dirname(parsed.path)), index))
    totals = Counter()
    for segments, _ in blocks:
        for seconds, place, _ in segments:
            totals[place] += seconds
    total = sum(totals.values())
    if len(totals) < 2 or not total:
        return plan
    main, main_seconds = totals.most_common(1)[0]
    if main_seconds < total * .7:
        return plan
    adverts = [segments for segments, tagged in blocks
               if segments and not tagged and all(place != main for _, place, _ in segments)
               and sum(seconds for seconds, _, _ in segments) <= 120]
    # Short episodes can carry the same 17-second advert three times (~16%).
    if not adverts or sum(seconds for segments in adverts for seconds, _, _ in segments) > total * .3:
        return plan
    drop = {index for segments in adverts for _, _, index in segments}
    # Each segment's own tags directly precede its URI line.
    for index in sorted(drop):
        before = index - 1
        while before >= 0 and lines[before].startswith(_SEGMENT_TAGS):
            drop.add(before)
            before -= 1
    kept, pending, started = [], None, False
    for index, line in enumerate(lines):
        if index in drop:
            continue
        if _is_discontinuity(line):
            # One marker where the advert was; none before the first segment.
            pending = line if started else None
            continue
        if pending and (line.startswith('#EXTINF:') or (line and not line.startswith('#'))):
            kept.append(pending)
            pending = None
        if line and not line.startswith('#'):
            started = True
        kept.append(line)
    used = {line for line in kept if line and not line.startswith('#')}
    used.update(name for line in kept if 'URI=' in line for name in hls_proxy._URI_ATTR.findall(line))
    return dict(plan, lines=kept, resources=[[address, filename] for address, filename in plan['resources'] if filename in used])


def _hls(url, work, folder, height, progress, on_retry=None):
    # Resume the exact saved rendition, rather than reselecting a source line.
    plan = _read(work / 'plan.json')
    fresh = plan is None or not any((work / filename).exists() for _, filename in plan['resources'])
    if fresh:
        plan = _hls_plan(url, folder, height, on_retry)
    cleaned = _without_ads(plan)
    # Plans saved before advert removal are cleaned when they resume.
    if fresh or cleaned != plan:
        _write(work / 'plan.json', cleaned)
    plan = cleaned
    lines, resources = plan['lines'], plan['resources']
    filenames = {filename for _, filename in resources}
    if (not resources or len(filenames) != len(resources)
            or any(not re.fullmatch(r'\d{6}\.(ts|key|mp4)', name) for name in filenames)):
        raise ValueError('無效的已保存播放清單')
    duration = 0.0
    for line in lines:
        if line and not line.startswith('#') and line not in filenames:
            raise ValueError('無效的已保存播放清單')
        if 'URI=' in line and (not line.startswith(('#EXT-X-KEY:', '#EXT-X-MAP:'))
                              or any(name not in filenames for name in hls_proxy._URI_ATTR.findall(line))):
            raise ValueError('無效的已保存播放清單')
        if line.startswith('#EXTINF:'):
            seconds = float(line.split(':', 1)[1].split(',')[0])
            if not 0 < seconds <= 600: raise ValueError('無效的分段長度')
            duration += seconds
    if not duration or '#EXT-X-ENDLIST' not in lines:
        raise ValueError('無效的已保存播放清單')
    completed = {filename for remote, filename in resources if _completed(work / filename, remote)}
    pending = [remote for remote, filename in resources if filename not in completed]
    try:
        for remote in pending:
            assert_hls_url(remote)
    except UnsafeURL:
        # Re-establish child-CDN authorization only via a validated live parent.
        # Keep the original plan/rendition and all verified downloaded segments.
        _hls_plan(url, folder, height, on_retry)
        for remote in pending:
            assert_hls_url(remote)
    progress(round(len(completed) * 100 / len(resources), 1))
    _download_segments(work, folder, [(remote, filename) for remote, filename in resources if filename not in completed],
                       completed, len(resources), progress, on_retry)
    playlist = work / 'index.m3u8'
    playlist.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return playlist, duration


def _download_segments(work, folder, pending, completed, total, progress, on_retry):
    """Use a few connections while nothing plays, and a single yielding one while media plays."""
    lock = threading.Lock()
    waiting = list(reversed(pending))
    partial = {}
    stop = threading.Event()
    failures = []

    def report(filename=None, value=0):
        with lock:
            if filename is not None:
                partial[filename] = min(value, 100) / 100
            done = len(completed) + sum(partial.values())
        progress(round(done * 100 / total, 1))

    def lane(index):
        _lane.stop = stop
        try:
            while not stop.is_set():
                with lock:
                    if not waiting:
                        return
                if index:
                    # Extra connections never compete with a playing browser or TV.
                    if not next_buffer.playback_idle(PLAYBACK_IDLE):
                        _lane_check()
                        _check(folder)
                        time.sleep(.25)
                        continue
                else:
                    _yield_to_playback(folder)
                with lock:
                    if not waiting:
                        return
                    remote, filename = waiting.pop()
                _download(remote, work / filename, folder, 256 * 1024 * 1024,
                          progress=lambda value, name=filename: report(name, value), on_retry=on_retry)
                with lock:
                    partial.pop(filename, None)
                    completed.add(filename)
                report()
        except _LaneStopped:
            pass
        except BaseException as error:
            # A refusal, cancellation or bad segment stops every connection;
            # none of them starts another segment or retry afterwards.
            with lock:
                failures.append(error)
            stop.set()
        finally:
            _lane.stop = None

    lanes = [threading.Thread(target=lane, args=(index,), name=f'offline-segment-{index}', daemon=True)
             for index in range(min(PARALLEL_SEGMENTS, len(pending)))]
    for thread in lanes:
        thread.start()
    for thread in lanes:
        thread.join()
    if failures:
        raise failures[0]


def _hls_plan(url, folder, height, on_retry):
    for _ in range(4):
        _check(folder)
        text, base = _retry(lambda: hls_proxy._read_playlist(url, time.monotonic() + 25), folder, on_retry)
        hls_proxy.rewrite_playlist(text, base)
        if re.search(r'#EXT-X-MEDIA:[^\n]*TYPE=AUDIO[^\n]*URI=', text):
            raise ValueError('此片源使用分離音軌，暫不支援整集下載')
        variants, attrs = [], None
        for line in text.splitlines():
            if line.startswith('#EXT-X-STREAM-INF:'):
                attrs = dict(hls_proxy._ATTR.findall(line))
            elif line and not line.startswith('#') and attrs is not None:
                match = re.fullmatch(r'\d+x(\d+)', attrs.get('RESOLUTION', '').strip('"'))
                variants.append((int(match[1]) if match else 0, urljoin(base, line.strip())))
                attrs = None
        if not variants:
            break
        eligible = [v for v in variants if 0 < v[0] <= height] if height else []
        url = hls_proxy._playlist_media_url(max(eligible or variants)[1], urlparse(base).hostname or '')
    else:
        raise ValueError('播放清單層數過多')
    if '#EXT-X-ENDLIST' not in text or '#EXT-X-BYTERANGE' in text:
        raise ValueError('直播或範圍分段片源暫不支援整集下載')
    lines, resources, duration = [], {}, 0.0
    def local(remote, suffix):
        remote = hls_proxy._playlist_media_url(urljoin(base, remote), urlparse(base).hostname or '')
        if remote not in resources:
            resources[remote] = f'{len(resources):06d}{suffix}'
        return resources[remote]
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            seconds = float(line.split(':', 1)[1].split(',')[0])
            if not 0 < seconds <= 600:
                raise ValueError('無效的分段長度')
            duration += seconds
        if line.startswith(('#EXT-X-KEY:', '#EXT-X-MAP:')):
            attrs = {k: v.strip('"') for k, v in hls_proxy._ATTR.findall(line)}
            if (line.startswith('#EXT-X-KEY:') and (attrs.get('METHOD') not in ('NONE', 'AES-128') or attrs.get('KEYFORMAT', 'identity') != 'identity')) or attrs.get('BYTERANGE'):
                raise ValueError('此加密或範圍格式暫不支援整集下載')
            line = hls_proxy._URI_ATTR.sub(lambda m: 'URI="' + local(m[1], '.key' if line.startswith('#EXT-X-KEY:') else '.mp4') + '"', line)
        elif line and not line.startswith('#'):
            line = local(line, '.ts')
        elif 'URI=' in line:
            # Do not leave any URL for FFmpeg to resolve independently.
            raise ValueError('此播放清單格式暫不支援整集下載')
        lines.append(line)
    if not resources or not duration:
        raise ValueError('沒有可下載的影片分段')
    plan = dict(lines=lines, resources=[[remote, filename] for remote, filename in resources.items()])
    return plan


_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
_FIT_720 = "w='min(iw,1280)':h='min(ih,720)':force_original_aspect_ratio=decrease:force_divisible_by=2"
_SCALE_720 = f"scale={_FIT_720}"
# 1080p to 720p on a GTX 1650 / UHD 770 PC, against the former x264 fast
# (2 threads, 2.8x real time): NVENC runs 13-30x with equal SSIM in larger
# files, QSV ~16x and x264 veryfast (4 threads) ~7x at about the same size and
# quality. Scaling on the CPU was the NVENC bottleneck once the PC was busy,
# so the frames are resized on the graphics card (8-bit first, as NVENC needs).
_ENCODERS = {
    'h264_nvenc': (f"format=yuv420p,hwupload_cuda,scale_cuda={_FIT_720}:interp_algo=bicubic",
                   ['-c:v', 'h264_nvenc', '-preset', 'p5', '-rc', 'vbr', '-cq', '25', '-b:v', '0', '-maxrate', '4M',
                    '-bufsize', '8M', '-spatial-aq', '1', '-profile:v', 'high']),
    'h264_qsv': (_SCALE_720, ['-c:v', 'h264_qsv', '-preset', 'medium', '-global_quality', '21', '-pix_fmt', 'nv12']),
    'libx264': (_SCALE_720, ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-pix_fmt', 'yuv420p', '-threads', '4']),
}
_HARDWARE = ('h264_nvenc', 'h264_qsv')
_encoder_lock = threading.Lock()
_encoder_choice = None
_broken_encoders = set()


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _encoder():
    """The fastest H.264 encoder that works here, checked once per process."""
    global _encoder_choice
    with _encoder_lock:
        if _encoder_choice is None or _encoder_choice in _broken_encoders:
            _encoder_choice = 'libx264'
            for name in _HARDWARE:
                if name in _broken_encoders:
                    continue
                command = [_ffmpeg(), '-nostdin', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
                           '-i', 'color=c=black:s=320x240:r=25', '-frames:v', '5', '-vf', _ENCODERS[name][0],
                           *_ENCODERS[name][1], '-f', 'null', '-']
                try:
                    done = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, timeout=15, creationflags=_NO_WINDOW)
                except (OSError, subprocess.SubprocessError):
                    continue
                if done.returncode == 0:
                    _encoder_choice = name
                    break
        return _encoder_choice


def _parse_probe(text):
    info = {}
    match = re.search(r'Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)', text)
    if match:
        info['duration'] = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    video = re.search(r'Stream #[^\n]*?: Video: (\w+)([^\n]*)', text)
    if video:
        info['video'] = video[1]
        size = re.search(r'\b(\d{2,5})x(\d{2,5})\b', video[2])
        pixels = re.search(r', (yuv\w+|nv12|p010\w*|gray\w*)', video[2])
        if size:
            info['height'] = int(size[2])
        if pixels:
            info['pix_fmt'] = pixels[1]
    audio = re.search(r'Stream #[^\n]*?: Audio: (\w+)', text)
    if audio:
        info['audio'] = audio[1]
    return info


def _probe(input_path):
    command = [_ffmpeg(), '-nostdin', '-hide_banner', '-protocol_whitelist', 'file,crypto,data']
    if input_path.suffix == '.m3u8':
        command += ['-allowed_extensions', 'ALL']
    try:
        # Without an output ffmpeg exits with an error after describing the input.
        done = subprocess.run(command + ['-i', str(input_path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=60, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return {}
    return _parse_probe(done.stderr.decode('utf-8', 'replace'))


def _fits(info, height):
    """Already playable at the requested size: repackaging keeps the picture as is."""
    return (info.get('video') == 'h264' and info.get('pix_fmt') == 'yuv420p'
            and 0 < info.get('height', 0) <= height and info.get('audio') in (None, 'aac'))


def _converted_seconds(path):
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 4096))
            stamps = re.findall(rb'out_time_us=(\d+)', stream.read())
    except OSError:
        return None
    return int(stamps[-1]) / 1e6 if stamps else None


def _convert(input_path, output, folder, height, expected_duration, on_progress=None):
    """Make the MP4: repackage when the stream fits, else encode, falling back to x264."""
    info = _probe(input_path)
    duration = expected_duration or info.get('duration', 0)
    attempts = ['copy'] if not height or _fits(info, height) else []
    if height:
        attempts.append(_encoder())
        if attempts[-1] != 'libx264':
            attempts.append('libx264')
    for index, encoder in enumerate(attempts):
        try:
            _encode(input_path, output, folder, height, duration, encoder, on_progress,
                    strict=index < len(attempts) - 1)
        except (LowDisk, Cancelled):
            raise
        except ValueError:
            output.unlink(missing_ok=True)
            if index == len(attempts) - 1:
                raise
            continue
        if encoder == 'libx264' and index and attempts[index - 1] in _HARDWARE:
            # The same input encoded fine in software: the hardware path is broken.
            with _encoder_lock:
                _broken_encoders.add(attempts[index - 1])
        return encoder


def _encode(input_path, output, folder, height, duration, encoder, on_progress=None, strict=False):
    command = [_ffmpeg(), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-protocol_whitelist', 'file,crypto,data']
    if input_path.suffix == '.m3u8':
        command += ['-allowed_extensions', 'ALL']
    command += ['-i', str(input_path), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn']
    if encoder == 'copy':
        command += ['-c', 'copy']
    else:
        scale, video = _ENCODERS[encoder]
        command += ['-vf', scale, *video, '-c:a', 'aac', '-b:a', '128k']
    report = folder / 'conversion.txt'
    report.unlink(missing_ok=True)
    command += ['-movflags', '+faststart', '-progress', str(report), str(output)]
    if on_progress:
        on_progress(0, encoder)
    with (folder / 'conversion.log').open('wb') as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                   creationflags=_NO_WINDOW)
        try:
            deadline = time.monotonic() + 6 * 3600
            reported = time.monotonic()
            while process.poll() is None:
                _check(folder)
                if time.monotonic() > deadline:
                    raise ValueError('影片整理逾時，請重試')
                if on_progress and duration and time.monotonic() - reported >= 1:
                    reported = time.monotonic()
                    done = _converted_seconds(report)
                    if done is not None:
                        on_progress(min(99.9, round(done * 100 / duration, 1)), encoder)
                time.sleep(0.2)
            if process.returncode or not output.exists() or output.stat().st_size == 0:
                raise ValueError('影片格式無法整理成 MP4，請改選 720p 重試')
            stamps = re.findall(r'out_time_us=(\d+)', report.read_text(errors='replace'))
            length = int(stamps[-1]) / 1e6 if stamps else 0
            margin = max(3, duration * .01)
            if not stamps or (duration and length < duration - margin):
                raise ValueError('影片長度不完整，已保留錯誤供重試')
            if strict and duration and length > duration + margin:
                raise ValueError('影片時間軸不連續')  # Re-encoding rebuilds it.
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


def _saved_inputs_complete(work, url):
    if urlparse(url).path.lower().endswith('.mp4'):
        return _completed(work / 'source.mp4', url)
    plan = _read(work / 'plan.json') or {}
    resources = plan.get('resources', [])
    return bool(resources) and all(re.fullmatch(r'\d{6}\.(ts|key|mp4)', name)
                                   and _completed(work / name, remote) for remote, name in resources)


def _run(item):
    folder = _folder(item['id'])
    work = folder / 'parts'
    # Segment connections report progress and retries from their own threads.
    save_lock = threading.RLock()
    def save(**values):
        with save_lock:
            item.update(values)
            _write(folder / 'status.json', item)
    started_at = item.get('progress', 0)
    try:
        _check(folder)
        save(phase='downloading', progress=item.get('progress', 0))
        work.mkdir(exist_ok=True)
        if work.resolve().parent != folder.resolve() or work.is_symlink():
            raise ValueError('無效的下載資料夾')
        def retrying(attempt, delay, error):
            save(phase='retrying', retry_attempt=attempt, retry_at=time.time() + delay,
                 error=f'{_friendly(error)}，正在自動續傳')
        written = [0.0, -1.0]
        def progress(value):
            # Status is polled by the UI; per-chunk rewrites only add disk churn.
            with save_lock:
                now = time.monotonic()
                changed = item.get('phase') != 'downloading' or item.get('error') or item.get('retry_at')
                item.update(phase='downloading', progress=value, error='', retry_at=0)
                if changed or value >= 100 or now - written[0] >= 1 or abs(value - written[1]) >= 1:
                    written[:] = [now, value]
                    save()
        saved = _read(work / 'source.json')
        if saved:
            detail = VideoDetail.model_validate(saved['detail'])
            if detail.source != item['source'] or detail.id != item['video_id']:
                raise ValueError('已保存的下載來源不符')
        else:
            try:
                detail = _retry(lambda: fetch_detail(item['source'], item['video_id'], item['episode'] or None), folder, retrying)
            except SourceUnavailable as exc:
                # Usually a site's lines being down for a while: try again later, a few times.
                tries = item.get('source_retries', 0)
                if tries >= SOURCE_RETRIES:
                    raise
                item['source_retries'] = tries + 1
                raise RetryLater(f'來源暫時無法使用（{exc}），約 {10 * 2 ** tries} 分鐘後自動重試', 600 * 2 ** tries) from exc
            item['source_retries'] = 0
        selected = next((e for e in detail.episodes if e.id == item['episode']), None)
        if detail.episodes and not selected:
            raise ValueError('請指定要下載的集數')
        save(title=detail.title, episode_title=selected.title if selected else '完整影片')
        parsed = urlparse(selected.playlist if selected else detail.playlist)
        urls = parse_qs(parsed.query).get('u', [])
        if parsed.scheme or parsed.netloc or parsed.path != '/api/hls' or len(urls) != 1:
            raise ValueError('此集無可下載的線上片源')
        complete_input = bool(saved) and _saved_inputs_complete(work, urls[0])
        url = urls[0]
        if not complete_input:
            try:
                url = assert_hls_url(url)
            except UnsafeURL:
                if not saved: raise
                # Refresh authorization through the source parser; do not grant
                # a new CDN merely because it exists in a local file.
                _retry(lambda: fetch_detail(item['source'], item['video_id'], item['episode'] or None), folder, retrying)
                url = assert_hls_url(url)
        if not saved: _write(work / 'source.json', {'detail': detail.model_dump()})
        if urlparse(url).path.lower().endswith('.mp4'):
            input_path, duration = work / 'source.mp4', 0
            if not complete_input:
                _download(url, input_path, folder, resume_partial=True, progress=progress, on_retry=retrying)
        else:
            input_path, duration = _hls(url, work, folder, item['height'], progress, on_retry=retrying)
        save(phase='preparing', progress=100, prepare_progress=0, prepare_mode='', later_count=0)
        prepared = [0.0, -1.0]
        def preparing(value, encoder):
            mode = 'copy' if encoder == 'copy' else 'gpu' if encoder in _HARDWARE else 'cpu'
            with save_lock:
                now = time.monotonic()
                changed = item.get('prepare_mode') != mode
                item.update(prepare_progress=value, prepare_mode=mode)
                if changed or now - prepared[0] >= 1 or abs(value - prepared[1]) >= 1:
                    prepared[:] = [now, value]
                    save()
        temporary = folder / 'video.part.mp4'
        _convert(input_path, temporary, folder, item['height'], duration, preparing)
        _check(folder)
        temporary.replace(folder / 'video.mp4')
        save(prepare_progress=100)
        _write(folder / 'complete.json', dict(item=item, detail=detail.model_dump(), size=(folder / 'video.mp4').stat().st_size, completed_at=time.time()))
        save(phase='complete', error='')
    except Cancelled:
        save(phase='cancelled', error=_cancel_message(folder))
    except RetryLater as exc:
        # Count deferrals that made no headway; a dead host must stop eventually.
        later = 0 if item.get('progress', 0) > started_at else item.get('later_count', 0) + 1
        if later > LATER_LIMIT:
            save(phase='error', later_count=0, retry_at=0, error='多次重試仍無法連線，請稍後按「繼續下載」')
        else:
            save(phase='retrying', retry_at=time.time() + exc.delay, later_count=later,
                 retry_attempt=item.get('retry_attempt', 0) + 1, error=str(exc))
    except Exception as exc:
        save(phase='error', error=_friendly(exc))
    finally:
        # Interrupted downloads retain their exact rendition and verified parts.
        if item.get('phase') == 'complete' and work.exists() and work.resolve().parent == folder.resolve() and not work.is_symlink():
            shutil.rmtree(work)
        (folder / 'video.part.mp4').unlink(missing_ok=True)


def _worker():
    while True:
        item, handle = _queue.get()
        try:
            _run(item)
        except Exception:
            # A filesystem error in one task must not kill the whole queue.
            logging.getLogger(__name__).exception('Offline download worker failed')
        finally:
            _release(handle)
            _queue.task_done()
