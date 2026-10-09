"""MMOV public HTML adapter. Parse data only; never execute source scripts."""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from urllib.parse import quote, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .. import http_client, zh
from ..hls_proxy import dlna_media_url, proxied_media
from ..models import Card, Episode, Listing, PickChip, PickGroup, Tag, VideoDetail
from ..security import SiteBusy, SourceUnavailable, UnsafeURL, assert_https_url, remember_media_host, safe_search_query

ORIGIN = "https://hk.mmov.app"
PLAYER_ORIGIN = "https://hk.mmov.io"
HOSTS = {"hk.mmov.app", "hk.mmov.io"}
COVER_HOSTS = {"img.mmov.app", "image.mmov.app"}
KINDS = {"movie": ("1", "電影"), "tv": ("2", "連續劇"), "show": ("3", "綜藝"), "anime": ("4", "動漫")}
GENRES = {"6": "動作片", "7": "喜劇片", "8": "愛情片", "9": "科幻片", "10": "恐怖片",
          "11": "劇情片", "12": "戰爭片", "20": "冒險片", "21": "懸疑片", "14": "台劇",
          "13": "陸劇", "15": "韓劇", "35": "日劇", "34": "港劇", "36": "泰劇",
          "37": "越劇", "16": "歐美劇", "33": "海外劇"}
_lock = threading.RLock()
_search_lock = threading.Lock()
_SEARCH_INTERVAL = 5.5
_search_finished = 0.0
_html: OrderedDict[str, tuple[float, str]] = OrderedDict()
_inflight: dict[str, Future] = {}
_cooldowns: dict[str, tuple[float, SiteBusy]] = {}
_pages: OrderedDict[str, tuple[float, dict[int, str]]] = OrderedDict()
_preferred: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _id(value: str) -> str:
    if not re.fullmatch(r"[1-9]\d{0,11}", str(value or "")):
        raise UnsafeURL("invalid MMOV id")
    return str(value)


def _source_url(url: str, _parent: str = "") -> str:
    if urlparse(url).hostname not in HOSTS:
        raise UnsafeURL("MMOV redirect host not allowed")
    return assert_https_url(url, HOSTS)


def _get(url: str, *, deadline: float | None = None, operation: str = "browse") -> str:
    global _search_finished
    url = _source_url(urljoin(ORIGIN, url))
    deadline = deadline or time.monotonic() + 20
    with _lock:
        now = time.monotonic()
        if _html.get(url, (0, ""))[0] > now:
            return _html[url][1]
        if _cooldowns.get(operation, (0, None))[0] > now:
            raise _cooldowns[operation][1]
        pending = _inflight.get(url)
        owner = pending is None
        if owner:
            pending = _inflight[url] = Future()
    if not owner:
        return pending.result(timeout=max(0.01, deadline - time.monotonic()))
    search_locked = False
    try:
        if operation == "search":
            search_locked = _search_lock.acquire(timeout=max(0.01, deadline - time.monotonic()))
            if not search_locked:
                raise TimeoutError("MMOV 搜尋等待逾時，請重試")
            with _lock:
                if _cooldowns.get(operation, (0, None))[0] > time.monotonic():
                    raise _cooldowns[operation][1]
            # Consecutive searches can return HTTP 200 with a short 404 page.
            delay = _SEARCH_INTERVAL - (time.monotonic() - _search_finished)
            if delay > 0:
                if time.monotonic() + delay >= deadline:
                    raise TimeoutError("MMOV 搜尋等待逾時，請重試")
                time.sleep(delay)
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("MMOV 解析逾時，請重試")
        response = http_client.fetch_bytes(url, referer=ORIGIN + "/", allowed_hosts=HOSTS,
                                          impersonate="chrome131", timeout=min(left, 12), stream=True,
                                          redirect_validator=_source_url)
        try:
            raw = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if len(raw) + len(chunk) > 6_000_000 or time.monotonic() >= deadline:
                    raise SourceUnavailable("MMOV 頁面過大或讀取逾時，請重試")
                raw.extend(chunk)
            text = raw.decode("utf-8", "replace")
        finally:
            http_client.close_response(response)
        if operation == "search" and re.search(r"<title>\s*404\s*</title>", text, re.I) and "Data not found" in text:
            raise SiteBusy("MMOV 電影線上看", 503, 30)
        with _lock:
            _html[url] = (time.monotonic() + (30 if operation == "search" else 120), text)
            _html.move_to_end(url)
            while len(_html) > 128:
                _html.popitem(last=False)
        pending.set_result(text)
        return text
    except Exception as exc:
        if isinstance(exc, SiteBusy):
            exc.site = "MMOV 電影線上看"
            with _lock:
                _cooldowns[operation] = (time.monotonic() + max(30, exc.retry_after or 0), exc)
        pending.set_exception(exc)
        raise
    finally:
        if search_locked:
            _search_finished = time.monotonic()
            _search_lock.release()
        with _lock:
            _inflight.pop(url, None)


def _cover(raw: str) -> str:
    if not raw:
        return ""
    url = urljoin(ORIGIN, raw)
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in COVER_HOSTS or p.port not in (None, 443) or p.username or p.password or p.fragment:
        return ""
    return "/api/img?u=" + quote(url, safe="")


def parse_cards(html: str) -> list[Card]:
    soup = BeautifulSoup(html, "html.parser")
    result, seen = [], set()
    for a in soup.select("a.stui-vodlist__thumb[href]"):
        p = urlparse(urljoin(ORIGIN, a["href"]))
        m = re.fullmatch(r"/vod/([1-9]\d{0,11})\.html", p.path)
        if p.hostname not in HOSTS or not m or m[1] in seen:
            continue
        seen.add(m[1])
        title = re.sub(r"\s*線上看$", "", a.get("title", "")).strip() or m[1]
        image = a.select_one("img")
        raw = a.get("data-src") or a.get("data-original") or (image.get("data-src") or image.get("data-original") or image.get("src") if image else "")
        status = a.select_one(".pic-text")
        result.append(Card(id=m[1], title=title[:500], source="mmov", cover=_cover(raw),
                           duration=status.get_text(strip=True) if status else None))
    return result[:80]


def home_bundle():
    soup = BeautifulSoup(_get("/"), "html.parser")
    rows = []
    for panel in soup.select(".stui-pannel"):
        heading = panel.select_one(".stui-pannel__head .title")
        if not heading:
            continue
        title = heading.get_text(" ", strip=True).replace("hk.mmov.app", "").strip()
        kind = {"本週熱播TV": "hot_tv", "近期熱門電影": "hot_movie", "電影": "movie",
                "連續劇": "tv", "綜藝": "show", "動漫": "anime"}.get(title)
        cards = parse_cards(str(panel))
        if kind and cards:
            rows.append((kind, title, cards))
    if not rows:
        raise SourceUnavailable("MMOV 首頁暫時沒有可讀取的片單")
    picks = [PickGroup(title="分類", items=[PickChip(name=name, kind=kind) for kind, (_, name) in KINDS.items()]),
             PickGroup(title="電影與劇集類型", items=[PickChip(name=name, kind="category", slug=key) for key, name in GENRES.items()])]
    return rows, picks


def _listing(key: str, first: str, page: int, title: str, *, search: bool = False) -> Listing:
    page = max(1, min(int(page), 1000))
    with _lock:
        until, links = _pages.get(key, (0, {}))
        path = links.get(page) if until > time.monotonic() else None
    if page > 1 and not path and until > time.monotonic():
        return Listing(items=[], page=page, has_next=False, title=title)
    if page > 1 and not path:
        _listing(key, first, 1, title, search=search)
        with _lock:
            path = _pages.get(key, (0, {}))[1].get(page)
        if not path:
            return Listing(items=[], page=page, has_next=False, title=title)
    html = _get(path if page > 1 else first, operation="search" if search else "browse")
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for a in soup.select(".stui-page a[href]"):
        p = urlparse(urljoin(ORIGIN, a["href"]))
        if p.hostname != "hk.mmov.app" or p.scheme != "https" or p.query or p.fragment:
            continue
        if search:
            m = re.fullmatch(r"/vodsearch/(.*?)----------(\d+)---\.html", unquote(p.path))
            valid = m and m[1] == key.removeprefix("search:")
        elif first.startswith("/type/"):
            m = re.fullmatch(r"/type/(\d+)-(\d+)\.html", p.path)
            valid = m and m[1] == key
        else:
            m = re.fullmatch(r"/vodshow(\d+)--------(\d+)---\.html", p.path)
            valid = m and m[1] == key
        if valid and 1 <= int(m[2]) <= 1000:
            found[int(m[2])] = p.path
    with _lock:
        _pages[key] = (time.monotonic() + 900, found)
        _pages.move_to_end(key)
        while len(_pages) > 128:
            _pages.popitem(last=False)
    return Listing(items=parse_cards(html), page=page, has_next=page + 1 in found, title=title)


def browse(kind: str, slug: str | None = None, page: int = 1) -> Listing:
    kind = {"hot_tv": "tv", "hot_movie": "movie"}.get(kind, kind)
    if kind == "category" and slug in GENRES:
        return _listing(slug, f"/vodshow{slug}-----------.html", page, GENRES[slug])
    if kind not in KINDS or slug:
        raise UnsafeURL("unknown MMOV category")
    key, title = KINDS[kind]
    return _listing(key, f"/type/{key}.html", page, title)


def search(query: str, page: int = 1) -> Listing:
    query = safe_search_query(query)
    result = _listing("search:" + query, "/vodsearch/" + quote(query, safe="") + "-------------.html", page, query, search=True)
    if not result.items:
        normalized = zh.normalize_traditional(query)
        if normalized != query:
            result = _listing("search:" + normalized, "/vodsearch/" + quote(normalized, safe="") + "-------------.html", page, query, search=True)
    return result


def _episodes(soup: BeautifulSoup, video_id: str):
    episodes: OrderedDict[str, tuple[str, list[tuple[str, str]]]] = OrderedDict()
    for a in soup.select(".stui-content__playlist a[href]"):
        p = urlparse(urljoin(ORIGIN, a["href"]))
        m = re.fullmatch(r"/vodplay/(\d+)/(\d+)-(\d+)\.html", p.path)
        if p.scheme != "https" or p.hostname not in HOSTS or p.port not in (None, 443) or p.username or p.password or p.query or p.fragment or not m or m[1] != video_id:
            continue
        title = a.get_text(" ", strip=True)
        # Episode labels, not line-local offsets, identify the same episode across lines.
        number = re.fullmatch(r"第\s*0*(\d+)\s*[集話]", title)
        episode_id = str(int(number[1])) if number else "p" + m[3]
        if len(episode_id) > 16:
            continue
        if episode_id not in episodes:
            episodes[episode_id] = (title or "正片", [])
        candidate = (m[2], p.geturl())
        if candidate not in episodes[episode_id][1]:
            episodes[episode_id][1].append(candidate)
    return episodes


def _resolve(page: str, deadline: float) -> str:
    html = _get(page, deadline=deadline, operation="video")
    match = re.search(r"\bvar\s+videoSrc\s*=\s*(['\"])(https://[^'\"\s]+)\1\s*;", html)
    if not match:
        raise SourceUnavailable("這條 MMOV 線路沒有支援的播放網址")
    url = match[2].replace("\\/", "/")
    p = urlparse(url)
    if not p.path.lower().endswith(".m3u8"):
        raise SourceUnavailable("這條 MMOV 線路尚不支援")
    assert_https_url(url, {p.hostname or ""})
    remember_media_host(p.hostname or "", source="mmov")
    proxy = proxied_media(url)
    dlna_media_url(proxy, deadline, validate=True)
    return proxy  # Preserve the master for browser quality selection.


def fetch_video(video_id: str, ep: str | None = None) -> VideoDetail:
    video_id = _id(video_id)
    deadline = time.monotonic() + 45
    soup = BeautifulSoup(_get(f"/vod/{video_id}.html", deadline=deadline, operation="video"), "html.parser")
    choices = _episodes(soup, video_id)
    selected = str(ep) if ep else next(iter(choices), "")
    if selected not in choices:
        raise SourceUnavailable("MMOV 找不到這一集，請重新選集")
    candidates = list(choices[selected][1])
    with _lock:
        until, preferred = _preferred.get(video_id, (0, ""))
    if until > time.monotonic():
        candidates.sort(key=lambda item: item[0] != preferred)
    playlist, last_error = "", None
    for line, page in candidates[:3]:
        if time.monotonic() >= deadline:
            raise TimeoutError("MMOV 解析逾時，請重試")
        try:
            playlist = _resolve(page, deadline)
            with _lock:
                _preferred[video_id] = (time.monotonic() + 900, line)
                _preferred.move_to_end(video_id)
                while len(_preferred) > 128:
                    _preferred.popitem(last=False)
            break
        except SiteBusy:
            raise  # Rejection/rate limiting must not trigger more requests.
        except Exception as exc:
            last_error = exc
    if not playlist:
        raise SourceUnavailable("MMOV 這一集的可用線路無法載入，請重試或選擇其他來源") from last_error
    heading = soup.select_one(".stui-content__detail .title")
    cover = soup.select_one('.stui-content__thumb [data-src], meta[property="og:image"]')
    description = soup.select_one(".stui-content__desc")
    genres = []
    for a in soup.select(".stui-content__detail .data a[href]"):
        m = re.fullmatch(r"/vodshow(\d+)-----------\.html", a["href"])
        if m and m[1] in GENRES:
            genres.append(Tag(name=GENRES[m[1]], kind="category", slug=m[1]))
    return VideoDetail(id=video_id, source="mmov", title=heading.get_text(strip=True) if heading else video_id,
                       cover=_cover(cover.get("data-src") or cover.get("content") or "") if cover else "",
                       playlist=playlist, resolved_episode_id=selected,
                       episodes=[Episode(id=key, title=title, playlist=playlist if key == selected else "") for key, (title, _) in choices.items()],
                       genres=genres, description=description.get_text(" ", strip=True) if description else "",
                       related=parse_cards(str(soup)))


def proxied_cover(video_id: str) -> str:
    return ""  # History and favorites retain the cover obtained with the detail.
