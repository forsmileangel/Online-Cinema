from __future__ import annotations

import re
import time
import logging
from urllib.parse import parse_qs, quote, urljoin

from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.requests import Request
from urllib.parse import urlparse

from . import next_buffer
from . import http_client, seg_cache, lan
from .security import (
    UnsafeURL,
    SiteBusy,
    touch_media_host,
    assert_hls_url,
    assert_https_url,
    hls_allowed_hosts,
    is_chinaq_cdn,
    is_dramaq_cdn,
    is_gimy_cdn,
    remember_media_host,
    media_host_source,
)

_URI_ATTR = re.compile(r'URI="([^"]+)"')
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def proxied_media(url: str, origin: str = "", *, nesthub: bool = False, web: bool = False) -> str:
    assert_hls_url(url)
    path = "/api/hls?u=" + quote(url, safe="")
    if nesthub:
        path += "&nesthub=1"
        if web:
            path += "&web=1"
    if origin:
        return origin.rstrip("/") + path
    return path


def _playlist_media_url(url: str, parent_host: str) -> str:
    source = media_host_source(parent_host)
    try:
        validated = assert_hls_url(url)
    except UnsafeURL:
        if not (is_chinaq_cdn(parent_host) or is_dramaq_cdn(parent_host)):
            raise
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if not host or not path.endswith((".m3u8", ".ts", ".m4s", ".key", ".jpeg", ".jpg", ".mp4")):
            raise
        assert_https_url(url, {host})
        remember_media_host(host, source=source)
        validated = assert_hls_url(url)
    if source:
        # Carry the source Referer across nested playlists, keys and segments,
        # including hosts that were already allowed by the static policy.
        remember_media_host(urlparse(validated).hostname or "", source=source)
    return validated


def rewrite_playlist(text: str, base_url: str, origin: str = "", *, nesthub: bool = False, web: bool = False) -> str:
    if len(text) > 2_000_000:
        raise UnsafeURL("playlist too large")
    if nesthub:
        _validate_nesthub_playlist(text)
    parent_host = (urlparse(base_url).hostname or "").lower()
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            abs_url = urljoin(base_url, stripped)
            _playlist_media_url(abs_url, parent_host)
            out.append(proxied_media(abs_url, origin, nesthub=nesthub, web=web))
            continue
        if "URI=" in line:

            def _repl(match: re.Match[str]) -> str:
                abs_url = urljoin(base_url, match.group(1))
                _playlist_media_url(abs_url, parent_host)
                return f'URI="{proxied_media(abs_url, origin, nesthub=nesthub, web=web)}"'

            out.append(_URI_ATTR.sub(_repl, line))
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def _media_context(url: str) -> tuple[str, str | None]:
    host = (urlparse(url).hostname or "").lower()
    if host == "bahamut.akamaized.net":
        return "https://ani.gamer.com.tw/", "chrome131"
    if media_host_source(host) == "mmov":
        return "https://hk.mmov.io/", "chrome131"
    if any(part in host for part in ("bfllvip", "fengbao", "baofeng", "ppqrrs", "10cong", "wangwangzyvod", "hongguoapp")):
        return "https://www.hongguoapp.cn/", "chrome131"
    if is_gimy_cdn(host) or "gimyai" in host or host.endswith("gimy.tw"):
        return "https://gimyai.tw/", "chrome131"
    if is_chinaq_cdn(host) or "chinaq" in host:
        return "https://chinaq.fun/", "chrome131"
    if is_dramaq_cdn(host) or host.endswith("dramasq.io"):
        return "https://dramasq.io/", "chrome131"
    return (f"https://{host}/" if host else "https://www.hongguoapp.cn/"), "chrome131"


def _read_playlist(upstream_url: str, deadline: float) -> tuple[str, str]:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("播放清單讀取逾時，請重試")
    saved = next_buffer.cached(upstream_url)
    if saved is not None:
        touch_media_host(upstream_url)
        return saved.decode("utf-8", "replace"), upstream_url
    referer, impersonate = _media_context(upstream_url)
    response = http_client.fetch_bytes(upstream_url, referer=referer, allowed_hosts=hls_allowed_hosts(upstream_url),
                                       timeout=min(left, 10), impersonate=impersonate, redirect_validator=_playlist_media_url)
    try:
        raw = response.content
        base_url = str(response.url)
    finally:
        http_client.close_response(response)
    if time.monotonic() >= deadline:
        raise TimeoutError("播放清單讀取逾時，請重試")
    if len(raw) > 2_000_000 or not raw.lstrip().startswith(b"#EXTM3U"):
        raise UnsafeURL("invalid playlist")
    return raw.decode("utf-8", "replace"), base_url


def dlna_media_url(proxy_url: str, deadline: float, *, validate: bool = False) -> str:
    # LG can stall on an early, long-distance seek while its master playlist
    # switches renditions. A muxed media playlist keeps the same encoded media
    # and timeline while avoiding that native ABR/seek interaction.
    parsed = urlparse(proxy_url)
    urls = parse_qs(parsed.query).get("u", [])
    if parsed.path != "/api/hls" or len(urls) != 1:
        return proxy_url
    upstream_url = assert_hls_url(urls[0])
    if not urlparse(upstream_url).path.lower().endswith(".m3u8"):
        return proxy_url
    text, base_url = _read_playlist(upstream_url, deadline)
    if validate:
        rewrite_playlist(text, base_url)
    lines = text.splitlines()
    external_audio = set()
    for line in lines:
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = {key: value.strip('"') for key, value in _ATTR.findall(line)}
            if attrs.get("TYPE") == "AUDIO" and attrs.get("URI"):
                external_audio.add(attrs.get("GROUP-ID"))
    variants = []
    pending = None
    for line in lines:
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = {key: value.strip('"') for key, value in _ATTR.findall(line)}
        elif line and not line.startswith("#") and pending is not None:
            attrs, pending = pending, None
            codecs = attrs.get("CODECS", "").lower().split(",")
            if attrs.get("AUDIO") in external_audio:
                continue  # Selecting video alone would drop its separate audio.
            if attrs.get("CODECS") and not any(codec.strip().startswith(("avc1.", "avc3.")) for codec in codecs):
                continue
            resolution = re.fullmatch(r"(\d+)x(\d+)", attrs.get("RESOLUTION", ""))
            pixels = int(resolution[1]) * int(resolution[2]) if resolution else 0
            bandwidth = attrs.get("BANDWIDTH", "0")
            variants.append(((pixels, int(bandwidth) if bandwidth.isdigit() else 0), urljoin(base_url, line)))
    if not variants:
        return proxy_url
    chosen = max(variants, key=lambda variant: variant[0])[1]
    _playlist_media_url(chosen, (urlparse(base_url).hostname or "").lower())
    if validate:
        text, child_base = _read_playlist(chosen, deadline)
        rewrite_playlist(text, child_base)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
    return proxied_media(chosen, origin)


def _validate_nesthub_playlist(text: str) -> None:
    for line in text.splitlines():
        attrs = dict(_ATTR.findall(line))
        if (line.startswith(("#EXT-X-MAP:", "#EXT-X-BYTERANGE:"))
                or (line.startswith("#EXT-X-KEY:") and attrs.get("METHOD") != "NONE")):
            raise ValueError("此來源的分段格式尚不支援 Nest Hub 720p 相容模式，請改選其他來源")


def nesthub_media_url(proxy_url: str, deadline: float, *, strict: bool = False) -> str:
    parsed = urlparse(proxy_url)
    urls = parse_qs(parsed.query).get("u", [])
    if parsed.path != "/api/hls" or len(urls) != 1:
        return proxy_url
    upstream = assert_hls_url(urls[0])
    if strict and not urlparse(upstream).path.lower().endswith(".m3u8"):
        raise ValueError("此來源暫不支援 720p 轉換，請使用來源畫質")
    text, base = _read_playlist(upstream, deadline)
    variants, pending = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = {key: value.strip('"') for key, value in _ATTR.findall(line)}
        elif line and not line.startswith("#") and pending is not None:
            resolution = re.fullmatch(r"(\d+)x(\d+)", pending.get("RESOLUTION", ""))
            codecs = pending.get("CODECS", "").lower()
            if resolution and not pending.get("AUDIO") and (not codecs or "avc1." in codecs or "avc3." in codecs):
                variants.append((int(resolution[1]), int(resolution[2]), urljoin(base, line)))
            pending = None
    compatible = [v for v in variants if v[0] <= 1280 and v[1] <= 720]
    if compatible:
        upstream = max(compatible, key=lambda v: v[0] * v[1])[2]
    elif variants:
        upstream = min(variants, key=lambda v: v[0] * v[1])[2]
    elif "#EXT-X-STREAM-INF:" in text:
        if strict:
            raise ValueError("此來源的影音格式暫不支援 720p 轉換，請使用來源畫質")
        return proxy_url  # Keep separate audio and unsupported codecs intact.
    if variants:
        _playlist_media_url(upstream, (urlparse(base).hostname or "").lower())
        text, base = _read_playlist(upstream, deadline)
    else:
        try:
            _validate_nesthub_playlist(text)
        except ValueError:
            if strict:
                raise ValueError("此來源的分段格式暫不支援 720p 轉換，請使用來源畫質") from None
            return proxy_url  # An unlabelled source may already play natively.
    if strict and "#EXT-X-STREAM-INF:" in text:
        raise ValueError("此來源的多層播放清單暫不支援 720p 轉換，請使用來源畫質")
    # Validate nested URLs before asking the receiver to load the media.
    rewrite_playlist(text, base, nesthub=not compatible)
    return proxied_media(upstream, f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "", nesthub=not compatible)


def _nesthub_segment(request: Request, url: str, raw: bytes) -> Response:
    from .nesthub import transcode
    data = transcode(url, raw, web=True) if request.query_params.get("web") == "1" else transcode(url, raw)
    headers = {"Cache-Control": "private, max-age=3600", "Accept-Ranges": "bytes"}
    status = 200
    total = len(data)
    value = request.headers.get("range")
    if value:
        start, end = value.removeprefix("bytes=").split("-")
        first = int(start) if start else max(0, total - int(end))
        last = min(total - 1, int(end)) if start and end else total - 1
        if first > last or first >= total:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        data, status = data[first:last + 1], 206
        headers["Content-Range"] = f"bytes {first}-{last}/{total}"
    headers["Content-Length"] = str(len(data))
    return Response(b"" if request.method == "HEAD" else data, status_code=status, media_type="video/mp2t", headers=headers)


def serve_media(request: Request, raw_url: str) -> Response:
    from . import offline
    next_buffer.foreground(True)
    try:
        url = assert_hls_url(raw_url)
        saved = offline.cached_part(url)
        if saved is None:
            saved = next_buffer.cached(url)
        if saved is not None:
            touch_media_host(url)
            path = urlparse(url).path.lower()
            playlist = path.endswith(".m3u8")
            if playlist:
                text = rewrite_playlist(saved.decode("utf-8", "replace"), url, str(request.base_url).rstrip("/"),
                                        nesthub=request.query_params.get("nesthub") == "1", web=request.query_params.get("web") == "1")
                body = text.encode()
                return Response(b"" if request.method == "HEAD" else body, media_type="application/vnd.apple.mpegurl",
                                headers={"Cache-Control": "no-store", "Content-Length": str(len(body))})
            if request.query_params.get("nesthub") == "1":
                return _nesthub_segment(request, url, saved)
            mime = "video/mp4" if path.endswith((".mp4", ".m4s")) else "video/mp2t" if path.endswith((".ts", ".jpeg", ".jpg")) else "application/octet-stream"
            headers = {"Cache-Control": "private, max-age=3600", "Accept-Ranges": "bytes"}
            total, status = len(saved), 200
            raw_range = request.headers.get("range")
            if raw_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", raw_range)
                if not match or not any(match.groups()):
                    return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
                start = int(match[1]) if match[1] else max(0, total - int(match[2]))
                end = min(int(match[2]), total - 1) if match[1] and match[2] else total - 1
                if start > end or start >= total:
                    return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
                saved, status = saved[start:end + 1], 206
                headers["Content-Range"] = f"bytes {start}-{end}/{total}"
            headers["Content-Length"] = str(len(saved))
            return Response(b"" if request.method == "HEAD" else saved, status_code=status, media_type=mime, headers=headers)
        return _serve_media(request, raw_url)
    finally:
        next_buffer.foreground(False)


def _serve_media(request: Request, raw_url: str) -> Response:
    url = assert_hls_url(raw_url)
    parsed = urlparse(url)
    referer, imp = _media_context(url)
    path = parsed.path.lower()
    playlist = path.endswith(".m3u8")
    nesthub = request.query_params.get("nesthub") == "1"
    convert_segment = nesthub and not playlist
    if convert_segment and not path.endswith((".ts", ".jpeg", ".jpg")):
        raise ValueError("Nest Hub 相容模式需要 MPEG-TS 影片分段，請改選其他來源")
    range_header = request.headers.get("range")
    if range_header and not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", range_header):
        return Response(status_code=416)
    if convert_segment:
        range_header = None  # Ranges refer to the converted representation.
    # MP4 files can be gigabytes. Stream them with Range; only cache HLS segments.
    cacheable = convert_segment or (path.endswith((".ts", ".jpeg", ".m4s", ".aac", ".key")) and not range_header and request.method == "GET")
    mime = ("application/vnd.apple.mpegurl" if playlist else "video/mp4" if path.endswith((".mp4", ".m4s"))
            else "video/mp2t" if path.endswith((".ts", ".jpeg")) else "application/octet-stream")
    if cacheable:
        seg_cache.start_workers()
        cached = seg_cache.get(url)
        if cached is not None:
            touch_media_host(url)
            seg_cache.enqueue_next(url, referer)
            if convert_segment:
                return _nesthub_segment(request, url, cached)
            return Response(cached, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})

    # Finish segment retries before the browser starts another copy of the same
    # request. Never splice partial bytes from different connections.
    deadline = time.monotonic() + (18 if cacheable else 30)
    interfaces: list[str | None] = [None]
    for attempt in range(3):
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("影片分段下載逾時，請重試")
        interface = interfaces[min(attempt, len(interfaces) - 1)]
        try:
            response = http_client.fetch_bytes(url, referer=referer, allowed_hosts=hls_allowed_hosts(url), timeout=min(left, 6) if cacheable else left,
                                               stream=not playlist and not cacheable, impersonate=imp,
                                               method="GET" if playlist or convert_segment else request.method, range_header=None if playlist else range_header,
                                               redirect_validator=_playlist_media_url, interface=interface)
            break
        except SiteBusy:
            raise
        except UnsafeURL:
            raise
        except Exception as error:
            if attempt == 2:
                raise
            if cacheable and http_client.is_network_failure(error):
                if attempt == 0:
                    ips = lan.download_interfaces()
                    if len(ips) > 1:
                        interfaces = [None, ips[1], ips[0]]
                logging.getLogger(__name__).warning("Segment download retry host=%s attempt=%s interface=%s cause=%s",
                    parsed.hostname, attempt + 2, interfaces[min(attempt + 1, len(interfaces) - 1)] or "default", type(error).__name__)
            elif cacheable:
                raise
    upstream = {k.lower(): v for k, v in response.headers.items()}
    headers = {k: upstream[k] for k in ("content-length", "content-range", "accept-ranges") if k in upstream}
    headers["Cache-Control"] = "no-store" if playlist else "private, max-age=3600"
    if response.status_code == 416 or (request.method == "HEAD" and not playlist and not convert_segment):
        http_client.close_response(response)
        return Response(status_code=response.status_code, media_type=mime, headers=headers)

    if playlist or cacheable:
        try:
            raw = response.content
        finally:
            http_client.close_response(response)
        if playlist:
            text = raw.decode("utf-8", "replace")
            if not text.lstrip().startswith("#EXTM3U"):
                raise UnsafeURL("invalid playlist")
            # Absolute nested playlists, AES keys and map URIs work on both receivers.
            rewritten = rewrite_playlist(text, str(response.url), str(request.base_url).rstrip("/"), nesthub=nesthub, web=request.query_params.get("web") == "1")
            body = rewritten.encode()
            # HEAD describes the rewritten GET representation. An empty Response
            # otherwise advertises Content-Length: 0 to LG's startup probe.
            return Response(b"" if request.method == "HEAD" else body, media_type=mime,
                            headers={"Cache-Control": "no-store", "Content-Length": str(len(body))})
        if len(raw) > 30_000_000:
            raise UnsafeURL("segment too large")
        seg_cache.put(url, raw)
        seg_cache.enqueue_next(url, referer)
        if convert_segment:
            return _nesthub_segment(request, url, raw)
        return Response(raw, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})

    def chunks():
        try:
            renewed = time.monotonic()
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if time.monotonic() - renewed >= 60:
                    touch_media_host(url)
                    renewed = time.monotonic()
                yield chunk
        finally:
            http_client.close_response(response)
    return StreamingResponse(chunks(), status_code=response.status_code,
                             media_type=mime if mime != "application/octet-stream" else upstream.get("content-type", mime), headers=headers,
                             background=BackgroundTask(http_client.close_response, response))
