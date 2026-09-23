"""Gimy 劇迷 adapter (gimyai.tw). No eval of remote JS."""

from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import quote, urlparse, urljoin, parse_qs
from collections import OrderedDict
from concurrent.futures import Future

from bs4 import BeautifulSoup

from .. import http_client
from ..hls_proxy import dlna_media_url, proxied_media
from ..models import Card, Episode, Listing, PickChip, PickGroup, Tag, VideoDetail
from ..security import (
    SiteBusy,
    UnsafeURL,
    assert_image_url,
    safe_search_query,
    safe_video_id,
)

ORIGIN = "https://gimyai.tw"
HOSTS = {"gimyai.tw", "www.gimyai.tw"}
DETAIL_RE = re.compile(r"/detail/(\d+)\.html")
PLAY_RE = re.compile(r"/play/(\d+)-(\d+)-(\d+)\.html")
OFFICIAL_FROM = {
    "qq", "qiyi", "iqiyi", "youku", "mgtv", "qsvip", "jd4k", "sohu", "letv",
    "bilibili", "migu", "pptv", "wasu", "funshion",
}
GENRES = {
    "featured": ("2", "熱門電視劇"),
    "tv": ("2", "電視劇"),
    "movie": ("1", "電影"),
    "anime": ("4", "動漫"),
    "show": ("29", "綜藝"),
    "cn": ("13", "陸劇"),
    "kr": ("20", "韓劇"),
    "us": ("16", "美劇"),
    "jp": ("15", "日劇"),
    "tw": ("14", "台劇"),
    "hk": ("21", "港劇"),
    "short": ("34", "短劇"),
    "ai": ("38", "AI漫劇"),
    "overseas": ("31", "海外劇"),
    "doc": ("22", "紀錄片"),
}
HOME_TITLES = {
    "熱門推薦": ("featured", "熱門推薦"),
    "熱門電視劇": ("tv", "熱門電視劇"),
    "熱門電影": ("movie", "熱門電影"),
    "熱門動漫": ("anime", "熱門動漫"),
    "熱門綜藝": ("show", "熱門綜藝"),
}
_HOT_TTL = 900.0
_hot_lock = threading.Lock()
_hot_memo: tuple[float, list[Card]] | None = None
_search_lock = threading.Lock()
_search_inflight: dict[tuple[str, int], Future] = {}
_search_memo: OrderedDict[tuple[str, int], tuple[float, Listing]] = OrderedDict()
_search_links: dict[str, tuple[float, dict[int, str]]] = {}
_search_cooldown: tuple[float, SiteBusy] | None = None
_SEARCH_TTL = 30.0


def _get(path: str, *, timeout: float = 25) -> str:
    path = path if path.startswith("/") else "/" + path
    url = ORIGIN + path
    try:
        return http_client.fetch_html_hosts(
            url, HOSTS, impersonate="chrome131", referer=ORIGIN + "/", timeout=timeout
        )
    except SiteBusy as e:
        e.site = "Gimy 劇迷"
        raise
    except UnsafeURL as e:
        if "blocked" in str(e).lower():
            raise SiteBusy("Gimy 劇迷") from e
        raise
    except Exception as e:
        low = str(e).lower()
        if "403" in low or "429" in low or "503" in low:
            raise SiteBusy("Gimy 劇迷") from e
        raise


def _cover(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("/"):
        u = ORIGIN + u
    if not u.startswith("https://"):
        return ""
    if "placeholder" in u or "load.gif" in u:
        return ""
    try:
        assert_image_url(u)
    except UnsafeURL:
        host = (urlparse(u).hostname or "").lower()
        if not host.endswith("1777cdn.com") and not host.endswith("gimyai.tw"):
            return ""
    return "/api/img?u=" + quote(u, safe="")


def _hls(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if not u.startswith("https://"):
        return ""
    if ".m3u8" not in urlparse(u).path.lower():
        return ""
    try:
        return proxied_media(u)
    except UnsafeURL:
        return ""


def parse_cards(html: str) -> list[Card]:
    soup = BeautifulSoup(html or "", "html.parser")
    items: list[Card] = []
    seen: set[str] = set()
    for a in soup.select("a.poster[href*='/detail/']"):
        href = a.get("href") or ""
        m = DETAIL_RE.search(href)
        if not m:
            continue
        try:
            vid = safe_video_id(m.group(1))
        except UnsafeURL:
            continue
        if vid in seen:
            continue
        title_el = a.select_one(".poster__title")
        title = (title_el.get_text(" ", strip=True) if title_el else a.get("title") or "") or vid
        img = a.select_one("img")
        src = ""
        if img:
            src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        st = a.select_one(".poster__status")
        rem = st.get_text(" ", strip=True) if st else None
        seen.add(vid)
        items.append(Card(id=vid, title=title[:500], cover=_cover(src), duration=rem, source="gimy"))
        if len(items) >= 80:
            break
    return items


def parse_home_sections(html: str) -> list[tuple[str, str, list[Card]]]:
    soup = BeautifulSoup(html or "", "html.parser")
    rows: list[tuple[str, str, list[Card]]] = []
    used: set[str] = set()
    for block in soup.select("section.section"):
        h2 = block.select_one(".section__title")
        grid = block.select_one(".grid")
        if not h2 or not grid:
            continue
        raw = h2.get_text(" ", strip=True)
        meta = HOME_TITLES.get(raw)
        if not meta:
            continue
        kind, title = meta
        if kind in used:
            continue
        cards = parse_cards(str(grid))
        if not cards:
            continue
        used.add(kind)
        rows.append((kind, title, cards[:24]))
    return rows


def _pages(html: str, tid: str, page: int) -> int | None:
    nums = [int(n) for n in re.findall(rf"/genre/{re.escape(tid)}-(\d+)\.html", html or "")]
    nums += [int(n) for n in re.findall(rf"/explore/{re.escape(tid)}--------+(\d+)---+", html or "")]
    real = [n for n in nums if 1 <= n <= 2000]
    if not real:
        return None
    last = max(real)
    return last if last >= page else None


def _listing(html: str, page: int, title: str, tid: str) -> Listing:
    items = parse_cards(html)
    _remember_cards(items)
    pages = _pages(html, tid, page)
    if pages:
        has_next = page < pages
    else:
        has_next = "下一頁" in (html or "") and page < 1000
    return Listing(
        items=items[:48],
        page=page,
        has_next=has_next,
        title=title,
        pages=pages if pages and pages > 1 else None,
    )


def home_bundle() -> tuple[list[tuple[str, str, list[Card]]], list[PickGroup]]:
    rows = parse_home_sections(_get("/"))
    _remember_cards([card for _kind, _title, cards in rows for card in cards])
    picks = [
        PickGroup(
            title="分類",
            items=[
                PickChip(name="電視劇", kind="tv"),
                PickChip(name="陸劇", kind="cn"),
                PickChip(name="韓劇", kind="kr"),
                PickChip(name="台劇", kind="tw"),
                PickChip(name="日劇", kind="jp"),
                PickChip(name="港劇", kind="hk"),
                PickChip(name="短劇", kind="short"),
                PickChip(name="動漫", kind="anime"),
                PickChip(name="綜藝", kind="show"),
                PickChip(name="排行", kind="top"),
            ],
        )
    ]
    return rows, picks


def home_rows() -> list[tuple[str, str, list[Card]]]:
    return home_bundle()[0]


def browse(kind: str, slug: str | None = None, page: int = 1) -> Listing:
    page = max(1, min(int(page), 1000))
    kind = (kind or "").strip().lower()
    if kind in ("featured", "hot"):
        kind = "tv"
    if kind == "top":
        return Listing(items=parse_cards(_get("/label/top.html"))[:48], page=1, has_next=False, title="排行榜")
    if kind == "update":
        html = _get("/explore/2--time---------.html" if page <= 1 else f"/explore/2--time------{page}---.html")
        return _listing(html, page, "最近更新", "2")
    if kind not in GENRES:
        raise UnsafeURL("unknown category")
    tid, title = GENRES[kind]
    path = f"/genre/{tid}.html" if page <= 1 else f"/genre/{tid}-{page}.html"
    return _listing(_get(path), page, title, tid)


def _remember_cards(cards: list[Card]) -> None:
    global _hot_memo
    if not cards:
        return
    now = time.monotonic()
    with _hot_lock:
        previous = _hot_memo[1] if _hot_memo and now - _hot_memo[0] < _HOT_TTL else []
        merged = {card.id: card for card in previous}
        merged.update((card.id, card) for card in cards)
        _hot_memo = (now, list(merged.values())[-500:])


def _cached_search(q: str, page: int) -> Listing | None:
    with _hot_lock:
        cards = list(_hot_memo[1]) if _hot_memo and time.monotonic() - _hot_memo[0] < _HOT_TTL else []
    if not cards:
        return None
    items = [card for card in cards if q.casefold() in card.title.casefold()]
    start = (page - 1) * 24
    pages = max(1, (len(items) + 23) // 24)
    return Listing(items=items[start:start + 24], page=page, has_next=page < pages,
                   title=q, pages=pages if pages > 1 else None,
                   notice="來源搜尋暫時無法使用，僅搜尋已載入片單；結果不代表完整片庫。")


def _page_links(html: str, q: str, page: int, path: str) -> dict[int, str]:
    links = {page: path}
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href]"):
        url = urlparse(urljoin(ORIGIN + path, anchor.get("href") or ""))
        if (url.scheme != "https" or url.hostname not in HOSTS or url.username or url.password
                or url.netloc not in HOSTS or url.fragment or not url.path.startswith("/find/")
                or parse_qs(url.query).get("wd") != [q]):
            continue
        text = anchor.get_text(" ", strip=True)
        number = int(text) if text.isdigit() else page + 1 if "下一" in text or "next" in anchor.get("rel", []) else 0
        if 1 <= number <= 200:
            links[number] = url.path + ("?" + url.query if url.query else "")
    return links


def _search_once(q: str, page: int) -> Listing:
    global _search_cooldown
    with _search_lock:
        cooldown = _search_cooldown
        known = _search_links.get(q)
    if cooldown and time.monotonic() < cooldown[0]:
        cached = _cached_search(q, page)
        if cached is not None:
            return cached
        raise cooldown[1]
    if page == 1:
        path = f"/find/-------------.html?wd={quote(q)}"
    else:
        path = known[1].get(page) if known and time.monotonic() - known[0] < _HOT_TTL else None
        if not path:
            return Listing(items=[], page=page, has_next=False, title=q, notice="這個搜尋頁碼已失效，請回到第一頁重新搜尋。")
    try:
        html = _get(path)
        items = parse_cards(html)
        links = _page_links(html, q, page, path)
        with _search_lock:
            now = time.monotonic()
            for key in [key for key, value in _search_links.items() if now - value[0] >= _HOT_TTL]:
                del _search_links[key]
            if q not in _search_links and len(_search_links) >= 100:
                del _search_links[next(iter(_search_links))]
            previous = known[1] if known and now - known[0] < _HOT_TTL else {}
            _search_links[q] = (now, {**previous, **links})
            _search_cooldown = None
        _remember_cards(items)
        return Listing(items=items, page=page, has_next=page + 1 in links, title=q)
    except SiteBusy as e:
        with _search_lock:
            _search_cooldown = (time.monotonic() + (e.retry_after if e.retry_after is not None else 30), e)
        cached = _cached_search(q, page)
        if cached is not None:
            return cached
        raise
    except Exception:
        cached = _cached_search(q, page)
        if cached is not None:
            return cached
        raise


def search(query: str, page: int = 1) -> Listing:
    q = safe_search_query(query)
    page = max(1, min(int(page), 200))
    key = (q, page)
    with _search_lock:
        cached = _search_memo.get(key)
        if cached and time.monotonic() - cached[0] < _SEARCH_TTL:
            return cached[1]
        future = _search_inflight.get(key)
        owner = future is None
        if owner:
            future = Future()
            _search_inflight[key] = future
    if not owner:
        return future.result(timeout=30)
    try:
        result = _search_once(q, page)
        with _search_lock:
            _search_memo[key] = (time.monotonic(), result)
            _search_memo.move_to_end(key)
            while len(_search_memo) > 100:
                _search_memo.popitem(last=False)
        future.set_result(result)
        return result
    except Exception as e:
        future.set_exception(e)
        raise
    finally:
        with _search_lock:
            _search_inflight.pop(key, None)


def _safe_ep(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not re.fullmatch(r"[1-9]\d{0,3}", s):
        raise UnsafeURL("invalid episode")
    return s


def _player_data(html: str) -> dict:
    for mark in ("var player_data", "var player_aaaa"):
        i = (html or "").find(mark)
        if i < 0:
            continue
        j = html.find("{", i)
        if j < 0:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(html[j:])
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _play_url(data: dict) -> str:
    raw = (data.get("url") or "").replace("\\/", "/").strip()
    frm = str(data.get("from") or "").strip().lower()
    if frm in OFFICIAL_FROM:
        return ""
    try:
        enc = int(data.get("encrypt") or 0)
    except (TypeError, ValueError):
        enc = 0
    if enc == 1 and raw:
        import base64

        try:
            raw = base64.b64decode(raw).decode("utf-8", "replace").strip()
        except Exception:
            return ""
    if enc >= 2:
        return ""
    if not raw.startswith("https://"):
        return ""
    if ".m3u8" not in urlparse(raw).path.lower():
        return ""
    return raw


def _routes(html: str, video_id: str) -> list[tuple[str, str, list[str]]]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[tuple[str, str, list[str]]] = []
    for box in soup.select(".episodes-route[data-route-sid]"):
        sid = str(box.get("data-route-sid") or "").strip()
        if not sid.isdigit():
            continue
        title_el = box.find_previous("div", class_="route-title")
        title = title_el.get_text(" ", strip=True) if title_el else sid
        eps: list[str] = []
        seen: set[str] = set()
        for a in box.select("a.ep[href*='/play/']"):
            m = PLAY_RE.search(a.get("href") or "")
            if not m or m.group(1) != video_id:
                continue
            nid = str(int(m.group(3)))
            if nid in seen:
                continue
            seen.add(nid)
            eps.append(nid)
        if eps:
            out.append((sid, title, eps))
    return out


def _prefer_routes(routes: list[tuple[str, str, list[str]]]) -> list[tuple[str, str, list[str]]]:
    yun = [r for r in routes if "雲" in r[1] or "云" in r[1]]
    rest = [r for r in routes if r not in yun]
    return yun + rest


def fetch_video(video_id: str, ep: str | None = None) -> VideoDetail:
    video_id = safe_video_id(video_id)
    want = _safe_ep(ep)
    deadline = time.monotonic() + 45
    html = _get(f"/detail/{video_id}.html", timeout=25)
    routes = _prefer_routes(_routes(html, video_id))
    if not routes:
        raise UnsafeURL("stream not found")
    pick_ep = want or routes[0][2][0]
    chosen: tuple[str, str, list[str]] | None = None
    data: dict = {}
    src = ""
    master = ""
    last_error: Exception | None = None
    candidates = [route for route in routes if pick_ep in route[2]][:3]
    for sid, title, eps in candidates:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Gimy 解析逾時")
        try:
            play_html = _get(f"/play/{video_id}-{sid}-{pick_ep}.html", timeout=min(25, remaining))
            data = _player_data(play_html)
            src = _play_url(data)
            master = _hls(src)
            if not master:
                continue
            # A valid master URL may still contain unsupported segment URLs.
            # Check the playlist before choosing a route or stopping the TV.
            dlna_media_url(master, deadline, validate=True)
            chosen = (sid, title, eps)
            break
        except SiteBusy:
            raise
        except Exception as e:
            last_error = e
    if not chosen or not master:
        raise last_error or UnsafeURL("stream not found")
    sid, _route_title, ep_ids = chosen
    if pick_ep not in ep_ids:
        ep_ids = sorted(set(ep_ids + [pick_ep]), key=lambda x: int(x))
    episodes = [
        Episode(id=eid, title=f"第{eid}集", playlist=master if eid == pick_ep else "")
        for eid in ep_ids
    ]
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1.detail__title, h1")
    title = (h1.get_text(" ", strip=True) if h1 else "") or str((data.get("vod_data") or {}).get("vod_name") or "") or video_id
    desc_el = soup.select_one("#desc .desc, .desc")
    desc = desc_el.get_text(" ", strip=True) if desc_el else None
    img = soup.select_one(".detail__poster img")
    cover = _cover(img.get("src") if img else "")
    genres: list[Tag] = []
    for a in soup.select(".detail__meta a[href*='/genre/']"):
        name = a.get_text(" ", strip=True)
        if not name:
            continue
        m = re.search(r"/genre/(\d+)", a.get("href") or "")
        category = next((key for key, (tid, _label) in GENRES.items() if m and tid == m.group(1) and key != "featured"), None)
        genres.append(Tag(name=name[:40], slug=m.group(1) if m else name[:40], kind=category or "tag", browsable=category is not None))
        if len(genres) >= 6:
            break
    related = [c for c in parse_cards(html) if c.id != video_id][:12]
    if time.monotonic() >= deadline:
        raise TimeoutError("Gimy 解析逾時")
    return VideoDetail(
        id=video_id,
        source="gimy",
        resolved_episode_id=pick_ep,
        title=title[:500],
        cover=cover,
        description=(desc[:2000] if desc else None),
        genres=genres,
        playlist=master,
        episodes=episodes,
        related=related,
    )


def proxied_cover(video_id: str) -> str:
    return ""
