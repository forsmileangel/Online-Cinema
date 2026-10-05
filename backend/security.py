"""URL / ID guards: no open proxy, no SSRF, no path tricks."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlparse

from . import settings as S

VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,78}$")
# ChinaQ play_data hosts rotate among MacCMS resource stations.
CHINAQ_CDN_RE = re.compile(
    r"(?:^|\.)(?:ukubf\d*\.com|lzcdn\d+\.com|ffzy-online\d*\.com|ppqrrs\.com|qqqrst\.com|1080pzy\.co|rstu\d*\.com|wgslsw\.com|yhzybf\.com|adfg\d*\.vip)$"
)
GIMY_CDN_RE = re.compile(
    r"(?:^|\.)(?:ryiplay\d*\.com|jisuzyv\.com|xluuss\.com|gsuus\.com|dytt-tvs\.com|xgplay\d*\.com|vvvip-plays\d*\.cc|yaaabc\.com|wsyzym3u8\.com|modujx\d*\.com|zuidazym3u8\.com)$"
)
DRAMASQ_CDN_RE = re.compile(r"(?:^|\.)(?:bfvvs\.com|kuktxu\.com)$")
_EXTRA_MEDIA_TTL = 3600.0
_extra_media_hosts: dict[str, float] = {}
_extra_media_sources: dict[str, str] = {}
_extra_media_lock = threading.RLock()
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
SEARCH_MAX = 50
SLUG_MAX = 80

CDN_HOSTS = {h.lower() for h in S.CDN_HOSTS}
IMAGE_HOSTS = {h.lower() for h in S.IMAGE_HOSTS}
MEDIA_HOSTS = CDN_HOSTS | IMAGE_HOSTS


class UnsafeURL(ValueError):
    pass


class HostUnresolvable(UnsafeURL):
    """Still refused, but a DNS failure is transient and worth retrying."""


class SourceUnavailable(Exception):
    """A known source availability condition with a safe user-facing message."""


class SiteBusy(Exception):
    def __init__(self, site: str = "", status_code: int | None = None, retry_after: int | None = None):
        self.site = site or "來源站"
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(self.site)

    def __str__(self):
        reason = {403: "拒絕連線", 429: "請求過多", 503: "暫時無法服務"}.get(self.status_code, "暫時無法連線")
        wait = f"，來源建議 {self.retry_after} 秒後重試" if self.retry_after is not None else "，請稍後重試"
        return f"{self.site} {reason}{wait}"

    @classmethod
    def from_response(cls, response, site: str = ""):
        raw = str(response.headers.get("retry-after") or "").strip()
        delay = None
        if raw.isdigit():
            delay = min(int(raw), 86400)
        elif raw:
            try:
                until = parsedate_to_datetime(raw)
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                delay = min(86400, max(0, int((until - datetime.now(timezone.utc)).total_seconds())))
            except (ValueError, TypeError, OverflowError):
                pass
        return cls(site or urlparse(str(response.url)).hostname or "來源站", response.status_code, delay)


def safe_video_id(raw: str) -> str:
    vid = (raw or "").strip()
    if not VIDEO_ID_RE.match(vid) or ".." in vid:
        raise UnsafeURL("invalid video id")
    return vid


def safe_search_query(raw: str) -> str:
    q = " ".join((raw or "").replace("\x00", "").split())
    if not q or len(q) > SEARCH_MAX:
        raise UnsafeURL("invalid search query")
    if any(c in q for c in "/\\<>\r\n"):
        raise UnsafeURL("invalid search query")
    return q


def safe_slug(raw: str) -> str:
    s = unquote(raw or "").strip()
    if not s or len(s) > SLUG_MAX or ".." in s or "/" in s or "\\" in s or "\x00" in s:
        raise UnsafeURL("invalid slug")
    if s.startswith(".") or any(c in s for c in "\r\n?#"):
        raise UnsafeURL("invalid slug")
    return s


def safe_kind(raw: str) -> str:
    k = (raw or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", k):
        raise UnsafeURL("unknown category")
    return k


def _host_ok(host: str, allowed: set[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    if not host:
        return False
    if host in allowed:
        return True
    return any(host.endswith("." + a) for a in allowed)


# A media playlist names its CDN once per segment. Hosts with very short DNS
# TTLs are not cached by Windows, so re-resolving each URL can take minutes.
# Only public results are reused; the connection itself resolves again.
_PUBLIC_DNS_TTL = 300.0
_public_hosts: dict[str, float] = {}
_public_hosts_lock = threading.Lock()


def _assert_not_private(host: str) -> None:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ip.is_global:
            raise UnsafeURL("address not allowed")
        return
    with _public_hosts_lock:
        if _public_hosts.get(host, 0) > time.monotonic():
            return
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise HostUnresolvable("host not resolvable") from e
    if not infos:
        raise HostUnresolvable("host not resolvable")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeURL("address not allowed")
    with _public_hosts_lock:
        now = time.monotonic()
        if len(_public_hosts) >= 1024:
            for expired in [key for key, until in _public_hosts.items() if until <= now] or list(_public_hosts)[:256]:
                del _public_hosts[expired]
        _public_hosts[host] = now + _PUBLIC_DNS_TTL


def assert_https_url(url: str, allowed_hosts: set[str]) -> str:
    if not url or len(url) > 4000:
        raise UnsafeURL("invalid url")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeURL("only https")
    if parsed.username or parsed.password:
        raise UnsafeURL("userinfo not allowed")
    if parsed.fragment:
        raise UnsafeURL("fragment not allowed")
    host = (parsed.hostname or "").lower()
    if not _host_ok(host, allowed_hosts):
        raise UnsafeURL("host not allowed")
    if parsed.port not in (None, 443):
        raise UnsafeURL("port not allowed")
    _assert_not_private(host)
    return url


def assert_cdn_url(url: str) -> str:
    return assert_https_url(url, MEDIA_HOSTS)


def assert_image_url(url: str) -> str:
    return assert_https_url(url, IMAGE_HOSTS)


def remember_media_host(host: str, *, source: str = "") -> None:
    h = (host or "").lower().rstrip(".")
    if h:
        now = time.monotonic()
        with _extra_media_lock:
            for expired in [key for key, until in _extra_media_hosts.items() if until <= now]:
                del _extra_media_hosts[expired]
                _extra_media_sources.pop(expired, None)
            _extra_media_hosts[h] = now + _EXTRA_MEDIA_TTL
            if source:
                _extra_media_sources[h] = source


def media_host_source(host: str) -> str:
    h = (host or "").lower().rstrip(".")
    with _extra_media_lock:
        if _extra_media_hosts.get(h, 0) > time.monotonic():
            return _extra_media_sources.get(h, "")
        _extra_media_sources.pop(h, None)
        return ""


def touch_media_host(url: str) -> None:
    """Only successful requests renew an already authorized, unexpired host."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    now = time.monotonic()
    with _extra_media_lock:
        if _extra_media_hosts.get(host, 0) > now:
            _extra_media_hosts[host] = now + _EXTRA_MEDIA_TTL


def is_gimy_cdn(host: str) -> bool:
    return bool(GIMY_CDN_RE.search((host or "").lower().rstrip(".")))


def is_dramaq_cdn(host: str) -> bool:
    return bool(DRAMASQ_CDN_RE.search((host or "").lower().rstrip(".")))


def is_chinaq_cdn(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    if CHINAQ_CDN_RE.search(h) or GIMY_CDN_RE.search(h):
        return True
    with _extra_media_lock:
        if _extra_media_hosts.get(h, 0) > time.monotonic():
            return True
        _extra_media_hosts.pop(h, None)
        _extra_media_sources.pop(h, None)
        return False


def hls_allowed_hosts(url: str) -> set[str]:
    host = (urlparse(url).hostname or "").lower()
    extra = {host} if is_chinaq_cdn(host) or is_dramaq_cdn(host) else set()
    return CDN_HOSTS | extra


def assert_hls_url(url: str) -> str:
    parsed = urlparse(assert_https_url(url, hls_allowed_hosts(url)))
    path = parsed.path.lower()
    if parsed.hostname == "bahamut.akamaized.net" and path.endswith(".m3u8key"):
        return url
    if path.endswith((".m3u8", ".jpeg", ".jpg", ".ts", ".m4s", ".key", ".mp4")):
        return url
    raise UnsafeURL("media path not allowed")


def final_url_still_allowed(url: str, allowed_hosts: set[str]) -> str:
    return assert_https_url(url, allowed_hosts)
