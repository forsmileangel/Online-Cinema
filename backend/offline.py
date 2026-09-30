"""Explicit episode downloads, shared by the two apps; only complete files play."""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import msvcrt
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from . import hls_proxy, http_client, sites, next_buffer
from .models import VideoDetail
from .security import UnsafeURL, SiteBusy, assert_hls_url, hls_allowed_hosts

ROOT = Path(r'D:\AI工作區\離線影片')
# Only user-selected metadata is queued; the single worker downloads serially.
_queue = queue.Queue()
_worker_lock = threading.Lock()
_started = False


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
    temporary = path.with_suffix('.tmp')
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
    folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / 'worker.lock').open('a+b')
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
    if item['phase'] in ('queued', 'downloading', 'retrying', 'preparing'):
        handle = _claim(folder)
        if handle:
            _release(handle)
            return {**item, 'phase': 'error', 'error': '下載已中斷，請手動繼續下載'}
    if record:
        return {**item, 'phase': 'error', 'error': '本地檔案遺失或不完整，請重新下載'}
    return item


def listing(sources, source=None, video_id=None):
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
    detail = fetch(video_id, ep=episode) if 'ep' in inspect.signature(fetch).parameters else fetch(video_id)
    detail.source = source
    return overlay(detail)


def enqueue(source, video_id, episode='', height=720, restart=False):
    global _started
    key = identity(source, video_id, episode)
    existing = status(key)
    if existing and existing['phase'] not in ('error', 'cancelled'):
        return existing
    folder = _folder(key)
    handle = _claim(folder)
    if handle is None:
        return status(key) or {'id': key, 'phase': 'queued'}
    item = dict(id=key, source=source, video_id=video_id, episode=episode, title=video_id,
                episode_title=episode, height=height, phase='queued', progress=0, error='', created=time.time())
    if existing:
        item.update(title=existing.get('title', video_id), episode_title=existing.get('episode_title', episode),
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
    with _worker_lock:
        if not _started:
            _started = True
            threading.Thread(target=_worker, name='offline-download', daemon=True).start()
    return item


def cancel(key):
    item = status(key)
    if item and item['phase'] in ('queued', 'downloading', 'retrying', 'preparing'):
        (_folder(key) / 'cancel').touch()


def delete(keys, sources):
    deleted, errors = [], []
    for key in dict.fromkeys(keys):
        handle = None
        try:
            folder = _folder(key)
            item = status(key)
            if not item or item['source'] not in sources:
                raise ValueError('找不到下載項目')
            handle = _claim(folder)
            if handle is None:
                raise ValueError('此集仍在下載，請先取消下載再刪除')
            # Remove the movie first: if a player has it locked, retain metadata.
            (folder / 'video.mp4').unlink(missing_ok=True)
            for name in ('complete.json', 'status.json', 'complete.tmp', 'status.tmp', 'cancel',
                         'video.part.mp4', 'conversion.txt', 'conversion.log'):
                (folder / name).unlink(missing_ok=True)
            work = folder / 'parts'
            if work.exists() and work.resolve().parent == folder.resolve() and not work.is_symlink():
                shutil.rmtree(work)
            # Keep the empty lock file/folder so another process cannot bypass it.
            deleted.append(key)
        except Exception as exc:
            errors.append({'id': key, 'error': '檔案仍被使用，請停止播放後重試' if isinstance(exc, PermissionError) else str(exc)})
        finally:
            if handle is not None:
                _release(handle)
    return {'deleted': deleted, 'errors': errors}


class Cancelled(Exception):
    pass


def _check(folder):
    if (folder / 'cancel').exists():
        raise Cancelled()
    if shutil.disk_usage(folder).free < 512 * 1024 * 1024:
        raise ValueError('硬碟剩餘空間不足 512 MB，已停止下載')


def _file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class DownloadInterrupted(ValueError):
    pass


def _transient(error):
    if isinstance(error, (DownloadInterrupted, TimeoutError, ConnectionError)):
        return True
    if isinstance(error, (Cancelled, UnsafeURL, SiteBusy)):
        return False
    requests = http_client.requests
    return isinstance(error, requests.RequestsError) and (
        error.code in {5, 6, 7, 16, 18, 28, 52, 55, 56, 92}
        or getattr(getattr(error, 'response', None), 'status_code', None) in {500, 502, 504})


def _wait_retry(folder, delay):
    until = time.monotonic() + delay
    while time.monotonic() < until:
        _check(folder)
        time.sleep(min(.25, max(0, until - time.monotonic())))
    _check(folder)


def _retry(operation, folder, on_retry=None, position=None):
    failures, attempts = 0, 0
    best = position() if position else 0
    while True:
        _check(folder)
        try:
            return operation()
        except Exception as error:
            if not _transient(error):
                raise
            current = position() if position else 0
            failures = 0 if current > best else failures + 1
            best = max(best, current)
            if failures > 8:
                raise ValueError('自動續傳多次仍無進展；已下載部分保留，可稍後繼續下載') from error
            attempts += 1
            delay = min(30, 2 ** max(1, failures))
            if on_retry: on_retry(attempts, delay, error)
            _wait_retry(folder, delay)


def _completed(path, url):
    previous = _read(path.with_name(path.name + '.download.json')) or {}
    return (not path.is_symlink() and path.exists() and previous.get('complete')
            and previous.get('url') == url and path.stat().st_size > 0
            and path.stat().st_size == previous.get('size') and _file_hash(path) == previous.get('sha256'))


def _download(url, path, folder, limit=None, *, resume_partial=False, progress=None, on_retry=None):
    return _retry(lambda: _download_once(url, path, folder, limit, resume_partial=resume_partial, progress=progress),
                  folder, on_retry, lambda: path.stat().st_size if path.exists() else 0)


def _download_once(url, path, folder, limit=None, *, resume_partial=False, progress=None):
    assert_hls_url(url)
    _check(folder)
    if path.is_symlink() or path.resolve().parent != path.parent.resolve():
        raise ValueError('無效的下載檔案路徑')
    metadata = path.with_name(path.name + '.download.json')
    previous = _read(metadata) or {}
    size = path.stat().st_size if path.exists() else 0
    if (previous.get('complete') and previous.get('url') == url and size > 0
            and size == previous.get('size') and _file_hash(path) == previous.get('sha256')):
        if progress: progress(100)
        return
    offset = 0
    validator = previous.get('etag', '')
    resumable = (previous.get('url') == url and isinstance(validator, str)
                 and validator.startswith('"') and validator.endswith('"')
                 and isinstance(previous.get('total'), int))
    if size and (resume_partial or resumable):
        # If-Range requires a strong validator. Never append an unknown/new file.
        if (previous.get('url') != url or not isinstance(validator, str) or not validator.startswith('"') or not validator.endswith('"')
                or not isinstance(previous.get('total'), int) or size > previous['total']
                or previous.get('size') != size or _file_hash(path) != previous.get('sha256')):
            raise ValueError('此片源無法安全續傳，請選「重新下載」')
        offset = size
        if offset == previous['total']:
            _write(metadata, {**previous, 'complete': True, 'size': size, 'sha256': _file_hash(path)})
            if progress: progress(100)
            return
    if not offset:
        cached = next_buffer.cached(url)
        if cached is not None:
            if limit and len(cached) > limit:
                raise ValueError('影片分段過大，已停止下載')
            path.write_bytes(cached)
            _write(metadata, dict(url=url, complete=True, size=len(cached), sha256=hashlib.sha256(cached).hexdigest()))
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
        record = dict(url=url, etag=etag, total=total, complete=False)
        size = offset
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
                size = out.tell()
                if progress and total: progress(round(size * 100 / total, 1))
        size = path.stat().st_size
        if not size or (expected_bytes is not None and size - offset != expected_bytes) or (total and size != total):
            raise DownloadInterrupted('影片下載不完整，正在自動續傳')
        record['complete'] = True
    finally:
        http_client.close_response(response)
        if record is not None and path.exists():
            record.update(size=path.stat().st_size, sha256=_file_hash(path))
            _write(metadata, record)


def _hls(url, work, folder, height, progress, on_retry=None):
    # Resume the exact saved rendition, rather than reselecting a source line.
    plan = _read(work / 'plan.json')
    if plan is None or not any((work / filename).exists() for _, filename in plan['resources']):
        plan = _hls_plan(url, folder, height, on_retry)
        _write(work / 'plan.json', plan)
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
    for remote, _ in resources:
        assert_hls_url(remote)
    completed = {filename for remote, filename in resources if _completed(work / filename, remote)}
    progress(round(len(completed) * 100 / len(resources), 1))
    for remote, filename in resources:
        if filename in completed: continue
        def segment_progress(value):
            progress(round((len(completed) + min(value, 100) / 100) * 100 / len(resources), 1))
        _download(remote, work / filename, folder, 256 * 1024 * 1024,
                  progress=segment_progress, on_retry=on_retry)
        completed.add(filename)
        progress(round(len(completed) * 100 / len(resources), 1))
    playlist = work / 'index.m3u8'
    playlist.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return playlist, duration


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


def _convert(input_path, output, folder, height, expected_duration):
    import imageio_ffmpeg
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
               '-protocol_whitelist', 'file,crypto,data']
    if input_path.suffix == '.m3u8':
        command += ['-allowed_extensions', 'ALL']
    command += ['-i', str(input_path), '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn']
    if height:
        command += ['-vf', "scale=w='min(iw,1280)':h='min(ih,720)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-pix_fmt', 'yuv420p', '-threads', '2',
                    '-c:a', 'aac', '-b:a', '128k']
    else:
        command += ['-c', 'copy']
    command += ['-movflags', '+faststart', '-progress', str(folder / 'conversion.txt'), str(output)]
    with (folder / 'conversion.log').open('wb') as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 6 * 3600
            while process.poll() is None:
                _check(folder)
                if time.monotonic() > deadline:
                    raise ValueError('影片整理逾時，請重試')
                time.sleep(0.2)
            if process.returncode or not output.exists() or output.stat().st_size == 0:
                raise ValueError('影片格式無法整理成 MP4，請改選 720p 重試')
            stamps = re.findall(r'out_time_us=(\d+)', (folder / 'conversion.txt').read_text(errors='replace'))
            if not stamps or (expected_duration and int(stamps[-1]) / 1e6 < expected_duration - max(3, expected_duration * .01)):
                raise ValueError('影片長度不完整，已保留錯誤供重試')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


def _run(item):
    folder = _folder(item['id'])
    work = folder / 'parts'
    def save(**values):
        item.update(values)
        _write(folder / 'status.json', item)
    try:
        _check(folder)
        save(phase='downloading', progress=item.get('progress', 0))
        work.mkdir(exist_ok=True)
        if work.resolve().parent != folder.resolve() or work.is_symlink():
            raise ValueError('無效的下載資料夾')
        def retrying(attempt, delay, error):
            save(phase='retrying', retry_attempt=attempt, retry_at=time.time() + delay,
                 error='連線逾時或下載不完整，正在自動續傳')
        def progress(value):
            save(phase='downloading', progress=value, error='', retry_at=0)
        saved = _read(work / 'source.json')
        if saved:
            detail = VideoDetail.model_validate(saved['detail'])
            if detail.source != item['source'] or detail.id != item['video_id']:
                raise ValueError('已保存的下載來源不符')
        else:
            detail = _retry(lambda: fetch_detail(item['source'], item['video_id'], item['episode'] or None), folder, retrying)
        selected = next((e for e in detail.episodes if e.id == item['episode']), None)
        if detail.episodes and not selected:
            raise ValueError('請指定要下載的集數')
        save(title=detail.title, episode_title=selected.title if selected else '完整影片')
        parsed = urlparse(selected.playlist if selected else detail.playlist)
        urls = parse_qs(parsed.query).get('u', [])
        if parsed.scheme or parsed.netloc or parsed.path != '/api/hls' or len(urls) != 1:
            raise ValueError('此集無可下載的線上片源')
        try:
            url = assert_hls_url(urls[0])
        except UnsafeURL:
            if not saved: raise
            # Refresh authorization through the source parser; do not grant a
            # new CDN merely because its hostname exists in a local file.
            _retry(lambda: fetch_detail(item['source'], item['video_id'], item['episode'] or None), folder, retrying)
            url = assert_hls_url(urls[0])
        if not saved: _write(work / 'source.json', {'detail': detail.model_dump()})
        if urlparse(url).path.lower().endswith('.mp4'):
            input_path, duration = work / 'source.mp4', 0
            _download(url, input_path, folder, resume_partial=True, progress=progress, on_retry=retrying)
        else:
            input_path, duration = _hls(url, work, folder, item['height'], progress, on_retry=retrying)
        save(phase='preparing', progress=100)
        temporary = folder / 'video.part.mp4'
        _convert(input_path, temporary, folder, item['height'], duration)
        _check(folder)
        temporary.replace(folder / 'video.mp4')
        _write(folder / 'complete.json', dict(item=item, detail=detail.model_dump(), size=(folder / 'video.mp4').stat().st_size))
        save(phase='complete', error='')
    except Cancelled:
        save(phase='cancelled', error='已停止下載；已下載部分保留，可繼續下載')
    except Exception as exc:
        save(phase='error', error=str(exc) or '下載失敗，請重試')
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
