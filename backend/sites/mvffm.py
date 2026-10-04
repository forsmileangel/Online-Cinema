"""MVFFM public catalog and direct streams; source scripts are parsed, never run."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from .. import http_client
from ..hls_proxy import _ATTR, _playlist_media_url, proxied_media
from ..models import Card, Episode, Listing, PickChip, PickGroup, Tag, VideoDetail
from ..security import SiteBusy, SourceUnavailable, UnsafeURL, assert_https_url, remember_media_host, safe_search_query

ORIGIN = "https://www.mvffm.net"
HOSTS = {"www.mvffm.net", "mvffm.net"}
KINDS = {"movie": ("/movies/", "電影"), "tv": ("/drama/", "連續劇"),
         "anime": ("/tvtype/anime/", "動漫")}
GENRES = {"usdrama": "美劇", "krdrama": "韓劇", "cndrama": "陸劇", "twdrama": "台劇", "jpdrama": "日劇"}
_lock = threading.RLock()
_html: OrderedDict[str, tuple[float, str]] = OrderedDict()
_ids: OrderedDict[str, str] = OrderedDict()
_preferred: OrderedDict[str, tuple[float, int]] = OrderedDict()
_cooldowns: dict[str, tuple[float, SiteBusy]] = {}
_inflight: dict[str, Future] = {}
_probes = ThreadPoolExecutor(max_workers=6, thread_name_prefix="mvffm-probe")
_heads = ThreadPoolExecutor(max_workers=3, thread_name_prefix="mvffm-id")


def _store(cache, key, value, limit=128):
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)


def _id(value: str) -> str:
    if not re.fullmatch(r"[1-9]\d{0,11}", str(value or "")):
        raise UnsafeURL("invalid MVFFM id")
    return str(value)


def _source_url(url: str, _parent: str = "") -> str:
    if urlparse(url).hostname not in HOSTS:
        raise UnsafeURL("MVFFM redirect host not allowed")
    return assert_https_url(url, HOSTS)


def _link(raw: str) -> str:
    p = urlparse(urljoin(ORIGIN, raw))
    if p.hostname in {"www.movieffm.net", "movieffm.net"}:
        p = p._replace(netloc="www.mvffm.net")
    if p.scheme != "https" or p.hostname not in HOSTS or p.port not in (None, 443) or p.username or p.password or p.fragment:
        return ""
    return p.geturl()


def _request(url: str, *, method="GET", media=False, deadline=None, sample=False):
    deadline = deadline or time.monotonic() + 12
    host = urlparse(url).hostname or ""
    if media:
        assert_https_url(url, {host})
        remember_media_host(host, source="mvffm")
    else:
        _source_url(url)
    with _lock:
        now = time.monotonic()
        for key in list(_cooldowns):
            if _cooldowns[key][0] <= now:
                del _cooldowns[key]
        if host in _cooldowns:
            raise _cooldowns[host][1]
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("MVFFM 讀取逾時，請重試")
    try:
        return http_client.fetch_bytes(url, referer=ORIGIN + "/", allowed_hosts={host},
            method=method, timeout=min(left, 4 if media else 12), stream=True,
            range_header="bytes=0-32767" if sample else None, impersonate="chrome131",
            redirect_validator=_playlist_media_url if media else _source_url)
    except SiteBusy as exc:
        with _lock:
            _cooldowns[exc.site if media else host] = (time.monotonic() + max(30, exc.retry_after or 0), exc)
        raise


def _read(url: str, *, media=False, deadline=None, sample=False):
    r = _request(url, media=media, deadline=deadline, sample=sample)
    try:
        buf = bytearray()
        for chunk in r.iter_content(chunk_size=16384):
            buf.extend(chunk)
            if deadline and time.monotonic() >= deadline:
                raise TimeoutError("MVFFM 讀取逾時，請重試")
            if sample and len(buf) >= 32768:
                break
            if len(buf) > (2_000_000 if media else 6_000_000):
                raise SourceUnavailable("MVFFM 回應過大")
        return bytes(buf), str(r.url)
    finally:
        http_client.close_response(r)


def _get(path: str) -> str:
    url = urljoin(ORIGIN, path)
    with _lock:
        if _html.get(url, (0, ""))[0] > time.monotonic():
            return _html[url][1]
    raw, _ = _read(url)
    text = raw.decode("utf-8", "replace")
    _store(_html, url, (time.monotonic() + 120, text))
    return text


def _cover(raw: str) -> str:
    if not raw:
        return ""
    url = _link(raw)
    return "/api/img?u=" + quote(url, safe="") if url else ""


def _image(node) -> str:
    img = node.select_one("img")
    return _cover(img.get("data-lazy-src") or img.get("src", "")) if img else ""


def _head_id(url: str) -> str:
    with _lock:
        cached = _ids.get(url)
    if cached:
        return cached
    r = _request(url, method="HEAD", deadline=time.monotonic() + 5)
    try:
        for part in r.headers.get("link", "").split(","):
            m = re.search(r'<([^>]+)>;\s*rel=["\']?shortlink', part)
            if m and _link(m[1]):
                values = parse_qs(urlparse(m[1]).query).get("p", [])
                if len(values) == 1:
                    found = _id(values[0])
                    _store(_ids, url, found, 2048)
                    return found
    finally:
        http_client.close_response(r)
    return ""


def parse_cards(html: str, *, resolve=False) -> list[Card]:
    soup = BeautifulSoup(html, "html.parser")
    pending = []
    for article in soup.select("article.item, .result-item article")[:80]:
        a = article.select_one("h3 a[href], .title a[href]")
        url = _link(a["href"]) if a else ""
        if not url or not re.fullmatch(r"/(?:movies|tvshows)/[^/]+/|/drama/\d+/", urlparse(url).path):
            continue
        m = re.fullmatch(r"post-(?:featured-)?(\d+)", article.get("id", ""))
        drama = re.fullmatch(r"/drama/(\d+)/", urlparse(url).path)
        vid = m[1] if m else drama[1] if drama else _ids.get(url, "")
        if vid:
            _store(_ids, url, vid, 2048)
        pending.append((vid, url, a.get_text(" ", strip=True), _image(article)))
    unknown = list(dict.fromkeys(url for vid, url, _, _ in pending if not vid))[:24] if resolve else []
    futures = {url: _heads.submit(_head_id, url) for url in unknown}
    resolved = {}
    for url, future in futures.items():
        try:
            resolved[url] = future.result(timeout=12)
        except SiteBusy:
            for other in futures.values():
                other.cancel()
            raise
        except Exception:
            resolved[url] = ""
    cards, seen = [], set()
    for vid, url, title, cover in pending:
        vid = vid or resolved.get(url)
        if vid and vid not in seen:
            cards.append(Card(id=_id(vid), source="mvffm", title=title[:500], cover=cover))
            seen.add(vid)
    return cards


def home_bundle():
    soup = BeautifulSoup(_get("/home/"), "html.parser")
    rows = []
    kinds = {"熱門推薦": "featured", "電影": "movie", "電視劇": "tv", "推薦連續劇": "featured_tv", "動漫": "anime"}
    kinds.update({title: key for key, title in GENRES.items()})
    for header in soup.select("header"):
        h = header.select_one("h2")
        title = h.get_text(strip=True) if h else ""
        if title not in kinds and title not in GENRES.values():
            continue
        for sibling in header.next_siblings:
            if getattr(sibling, "name", None) == "header":
                break
            if getattr(sibling, "get", None) and "items" in sibling.get("class", []):
                cards = parse_cards(str(sibling))
                if cards:
                    rows.append((kinds[title], title, cards))
                break
    if not rows:
        raise SourceUnavailable("MVFFM 首頁暫時沒有可讀取的片單")
    picks = [PickGroup(title="分類", items=[PickChip(name=v[1], kind=k) for k, v in KINDS.items()]),
             PickGroup(title="連續劇", items=[PickChip(name=v, kind="category", slug=k) for k, v in GENRES.items()])]
    return rows, picks


def _listing(first: str, page: int, title: str) -> Listing:
    page = max(1, min(int(page), 1000))
    soup = BeautifulSoup(_get(first), "html.parser")
    def pages(doc):
        found = {}
        for a in doc.select(".pagination a[href]"):
            url = _link(a["href"])
            if not url:
                continue
            p = urlparse(url)
            original = urlparse(urljoin(ORIGIN, first))
            if original.path == "/xssearch":
                q = parse_qs(p.query)
                if p.path != original.path or q.get("q") != parse_qs(original.query).get("q"):
                    continue
                num = q.get("p", [""])[0]
            else:
                m = re.fullmatch(re.escape(original.path) + r"page/(\d+)/", p.path)
                num = m[1] if m and not p.query else ""
            if num.isdigit():
                found[int(num)] = url
        return found
    links = pages(soup)
    if page > 1:
        # Only follow page links published by the source, including its next page.
        for _ in range(3):
            target = links.get(page)
            if target:
                soup = BeautifulSoup(_get(target), "html.parser")
                break
            prior = max((n for n in links if 1 < n < page), default=0)
            if not prior:
                return Listing(items=[], page=page, has_next=False, title=title)
            soup = BeautifulSoup(_get(links[prior]), "html.parser")
            links = pages(soup)
        else:
            return Listing(items=[], page=page, has_next=False, title=title)
    return Listing(items=parse_cards(str(soup), resolve=True), page=page,
                   has_next=page + 1 in pages(soup), title=title)


def browse(kind: str, slug: str | None = None, page: int = 1) -> Listing:
    if kind in GENRES and not slug:
        kind, slug = "category", kind
    kind = {"featured": "movie", "featured_tv": "tv"}.get(kind, kind)
    if kind == "category" and slug in GENRES:
        return _listing(f"/tvtype/{slug}/", page, GENRES[slug])
    if kind not in KINDS or slug:
        raise UnsafeURL("unknown MVFFM category")
    path, title = KINDS[kind]
    return _listing(path, page, title)


def search(query: str, page: int = 1) -> Listing:
    query = safe_search_query(query)
    return _listing("/xssearch?" + urlencode({"q": query}), page, query)


def _episodes(html: str):
    match = re.search(r"\bvideourls\s*:", html)
    choices = OrderedDict()
    if not match:
        return choices
    try:
        data, _ = json.JSONDecoder().raw_decode(html[match.end():].lstrip())
    except ValueError:
        return choices
    if not isinstance(data, list):
        return choices
    for line, entries in enumerate(data[:40]):
        movie = isinstance(entries, dict)
        for item in ([entries] if movie else entries if isinstance(entries, list) else [])[:2000]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not urlparse(url).path.lower().endswith((".m3u8", ".mp4")):
                continue
            title = "正片" if movie else str(item.get("name", "")).strip()
            if not title:
                continue
            number = re.fullmatch(r"(?:第\s*)?0*(\d+)(?:\s*[集話期])?", title)
            eid = "1" if movie else str(int(number[1])) if number else "e" + hashlib.sha256(title.encode()).hexdigest()[:12]
            if len(eid) > 16:
                continue
            choices.setdefault(eid, ("正片" if movie else f"第 {int(number[1])} 集" if number else title[:80], []))
            if url not in [u for _, u in choices[eid][1]]:
                choices[eid][1].append((line, url))
    return OrderedDict(sorted(choices.items(), key=lambda x: (not x[0].isdigit(), int(x[0]) if x[0].isdigit() else x[0])))


def _probe(url: str, deadline: float, stop: threading.Event) -> str:
    if stop.is_set():
        raise TimeoutError("probe cancelled")
    host = urlparse(url).hostname or ""
    assert_https_url(url, {host})
    remember_media_host(host, source="mvffm")
    proxy = proxied_media(url)
    if urlparse(url).path.lower().endswith(".m3u8"):
        # Resolve a compatible rendition while retaining the master for quality selection.
        for _ in range(3):
            if stop.is_set():
                raise TimeoutError("probe cancelled")
            raw, base = _read(url, media=True, deadline=deadline)
            text = raw.decode("utf-8", "replace")
            if not text.lstrip().startswith("#EXTM3U"):
                raise SourceUnavailable("MVFFM 線路沒有有效的播放清單")
            if "#EXTINF" in text:
                break
            variants, attrs = [], None
            external_audio = set()
            for line in text.splitlines():
                if line.startswith("#EXT-X-MEDIA:"):
                    media = dict((k, v.strip('"')) for k, v in _ATTR.findall(line))
                    if media.get("TYPE") == "AUDIO" and media.get("URI"):
                        external_audio.add(media.get("GROUP-ID"))
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("#EXT-X-STREAM-INF:"):
                    attrs = dict((k, v.strip('"')) for k, v in _ATTR.findall(line))
                elif line and not line.startswith("#") and attrs is not None:
                    if attrs.get("AUDIO") not in external_audio and (not attrs.get("CODECS") or "avc" in attrs["CODECS"]):
                        variants.append((int(attrs.get("BANDWIDTH", "0")), line))
                    attrs = None
            if not variants:
                raise SourceUnavailable("MVFFM 這條線路沒有相容的畫質")
            url = _playlist_media_url(urljoin(base, max(variants)[1]), urlparse(base).hostname or "")
        if not text.lstrip().startswith("#EXTM3U") or "#EXTINF" not in text:
            raise SourceUnavailable("MVFFM 線路沒有可播放的片段")
        first = next((s.strip() for s in text.splitlines() if s.strip() and not s.startswith("#")), "")
        if not first:
            raise SourceUnavailable("MVFFM 線路沒有可播放的片段")
        # Check the first segment's key/init data as well, without fetching the film.
        prefix = text.split(first, 1)[0]
        for line in prefix.splitlines():
            if line.startswith(("#EXT-X-KEY:", "#EXT-X-MAP:")) and 'URI="' in line:
                if "METHOD=" in line and "METHOD=AES-128" not in line:
                    raise SourceUnavailable("MVFFM 這條加密線路尚不支援")
                part = re.search(r'URI="([^"]+)"', line)[1]
                target = _playlist_media_url(urljoin(base, part), urlparse(base).hostname or "")
                if stop.is_set():
                    raise TimeoutError("probe cancelled")
                data, _ = _read(target, media=True, deadline=deadline, sample=True)
                if not data or (line.startswith("#EXT-X-KEY:") and len(data) != 16):
                    raise SourceUnavailable("MVFFM 線路金鑰無法讀取")
        url = _playlist_media_url(urljoin(base, first), urlparse(base).hostname or "")
    if stop.is_set():
        raise TimeoutError("probe cancelled")
    data, _ = _read(url, media=True, deadline=deadline, sample=True)
    if not data or data.lstrip().lower().startswith((b"<!doctype", b"<html", b"{\"error")):
        raise SourceUnavailable("MVFFM 線路影片無法讀取")
    return proxy


def _fastest(video_id: str, ep: str, candidates) -> str:
    key = video_id + ":" + ep
    with _lock:
        pending = _inflight.get(key)
        owner = pending is None
        if owner:
            pending = _inflight[key] = Future()
        until, preferred = _preferred.get(video_id, (0, -1))
    if not owner:
        return pending.result(timeout=13)
    stop, active = threading.Event(), {}
    try:
        choices = list(candidates)
        if until > time.monotonic():
            choices.sort(key=lambda c: c[0] != preferred)
        choices = iter(choices[:8])
        deadline = time.monotonic() + 10
        def submit():
            item = next(choices, None)
            if item:
                active[_probes.submit(_probe, item[1], deadline, stop)] = item[0]
        for _ in range(3):
            submit()
        error = None
        while active and time.monotonic() < deadline:
            done, _ = wait(active, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            for future in done:
                line = active.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    if isinstance(exc, SiteBusy) or not isinstance(error, SiteBusy):
                        error = exc
                    submit()
                    continue
                _store(_preferred, video_id, (time.monotonic() + 900, line))
                pending.set_result(result)
                return result
        if isinstance(error, SiteBusy):
            raise error
        raise SourceUnavailable("MVFFM 暫時找不到可用線路，請稍後重新載入")
    except Exception as exc:
        pending.set_exception(exc)
        raise
    finally:
        stop.set()
        for future in active:
            future.cancel()
        with _lock:
            _inflight.pop(key, None)


def fetch_video(video_id: str, ep: str | None = None) -> VideoDetail:
    video_id = _id(video_id)
    html = _get("/?p=" + video_id)
    soup = BeautifulSoup(html, "html.parser")
    choices = _episodes(html)
    seasons = []
    if not choices:
        # Legacy TV collection pages link to each season's current drama page.
        for a in soup.select("#seasons a[href]"):
            url = _link(a["href"])
            m = re.fullmatch(r"/drama/(\d+)/", urlparse(url).path) if url else None
            if m and m[1] not in [c.id for c in seasons]:
                seasons.append(Card(id=m[1], source="mvffm", title=a.get_text(" ", strip=True), cover=_image(soup)))
        if seasons:
            html = _get("/?p=" + seasons[0].id)
            soup = BeautifulSoup(html, "html.parser")
            choices = _episodes(html)
    selected = str(ep) if ep else next(iter(choices), "")
    if selected not in choices:
        raise SourceUnavailable("MVFFM 找不到可播放的這一集，請重新選集")
    playlist = _fastest(video_id, selected, choices[selected][1])
    h1 = soup.select_one("h1")
    description = soup.select_one('meta[name="description"]')
    poster = soup.select_one(".sheader .poster")
    return VideoDetail(id=video_id, source="mvffm", title=h1.get_text(" ", strip=True) if h1 else video_id,
        cover=_image(poster) if poster else "", playlist=playlist, resolved_episode_id=selected,
        description=description.get("content", "")[:2000] if description else None,
        genres=[Tag(name=a.get_text(strip=True), slug="", kind="category", browsable=False) for a in soup.select(".sgeneros a")],
        episodes=[Episode(id=eid, title=label, playlist=playlist if eid == selected else "") for eid, (label, _) in choices.items()],
        related=seasons or parse_cards(str(soup)))


def proxied_cover(video_id: str) -> str:
    return ""  # History retains the cover returned by the detail page.
