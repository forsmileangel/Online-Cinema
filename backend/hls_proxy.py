from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, quote, urljoin

from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.requests import Request
from urllib.parse import urlparse

from . import http_client, seg_cache
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
)

_URI_ATTR = re.compile(r'URI="([^"]+)"')
_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def proxied_media(url: str, origin: str = "") -> str:
    assert_hls_url(url)
    path = "/api/hls?u=" + quote(url, safe="")
    if origin:
        return origin.rstrip("/") + path
    return path


def _playlist_media_url(url: str, parent_host: str) -> str:
    try:
        return assert_hls_url(url)
    except UnsafeURL:
        if not (is_chinaq_cdn(parent_host) or is_dramaq_cdn(parent_host)):
            raise
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if not host or not path.endswith((".m3u8", ".ts", ".m4s", ".key", ".jpeg", ".jpg", ".mp4")):
            raise
        assert_https_url(url, {host})
        remember_media_host(host)
        return assert_hls_url(url)


def rewrite_playlist(text: str, base_url: str, origin: str = "") -> str:
    if len(text) > 2_000_000:
        raise UnsafeURL("playlist too large")
    parent_host = (urlparse(base_url).hostname or "").lower()
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            abs_url = urljoin(base_url, stripped)
            _playlist_media_url(abs_url, parent_host)
            out.append(proxied_media(abs_url, origin))
            continue
        if "URI=" in line:

            def _repl(match: re.Match[str]) -> str:
                abs_url = urljoin(base_url, match.group(1))
                _playlist_media_url(abs_url, parent_host)
                return f'URI="{proxied_media(abs_url, origin)}"'

            out.append(_URI_ATTR.sub(_repl, line))
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def _media_context(url: str) -> tuple[str, str | None]:
    host = (urlparse(url).hostname or "").lower()
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


def serve_media(request: Request, raw_url: str) -> Response:
    url = assert_hls_url(raw_url)
    parsed = urlparse(url)
    referer, imp = _media_context(url)
    path = parsed.path.lower()
    playlist = path.endswith(".m3u8")
    range_header = request.headers.get("range")
    if range_header and not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", range_header):
        return Response(status_code=416)
    # MP4 files can be gigabytes. Stream them with Range; only cache HLS segments.
    cacheable = path.endswith((".ts", ".jpeg", ".m4s", ".aac", ".key")) and not range_header and request.method == "GET"
    mime = ("application/vnd.apple.mpegurl" if playlist else "video/mp4" if path.endswith((".mp4", ".m4s"))
            else "video/mp2t" if path.endswith((".ts", ".jpeg")) else "application/octet-stream")
    if cacheable:
        seg_cache.start_workers()
        cached = seg_cache.get(url)
        if cached is not None:
            touch_media_host(url)
            seg_cache.enqueue_next(url, referer)
            return Response(cached, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})

    candidates = [url]
    for attempt in range(3):
        try:
            candidate = assert_hls_url(candidates[min(attempt, len(candidates) - 1)])
            response = http_client.fetch_bytes(candidate, referer=referer, allowed_hosts=hls_allowed_hosts(candidate), timeout=6 if len(candidates) > 1 else 30,
                                               stream=not playlist and not cacheable, impersonate=imp,
                                               method="GET" if playlist else request.method, range_header=None if playlist else range_header,
                                               redirect_validator=_playlist_media_url)
            break
        except SiteBusy:
            raise
        except UnsafeURL:
            raise
        except Exception:
            if attempt == 2:
                raise
    upstream = {k.lower(): v for k, v in response.headers.items()}
    headers = {k: upstream[k] for k in ("content-length", "content-range", "accept-ranges") if k in upstream}
    headers["Cache-Control"] = "no-store" if playlist else "private, max-age=3600"
    if response.status_code == 416 or (request.method == "HEAD" and not playlist):
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
            rewritten = rewrite_playlist(text, str(response.url), str(request.base_url).rstrip("/"))
            body = rewritten.encode()
            # HEAD describes the rewritten GET representation. An empty Response
            # otherwise advertises Content-Length: 0 to LG's startup probe.
            return Response(b"" if request.method == "HEAD" else body, media_type=mime,
                            headers={"Cache-Control": "no-store", "Content-Length": str(len(body))})
        if len(raw) > 30_000_000:
            raise UnsafeURL("segment too large")
        seg_cache.put(url, raw)
        seg_cache.enqueue_next(url, referer)
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
