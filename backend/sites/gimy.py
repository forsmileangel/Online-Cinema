"""Gimy 劇迷 adapter (gimyai.tw). No eval of remote JS."""

from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup

from .. import http_client
from ..hls_proxy import proxied_media
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


def _get(path: str) -> str:
    path = path if path.startswith("/") else "/" + path
    url = ORIGIN + path
    try:
        return http_client.fetch_html_hosts(
            url, HOSTS, impersonate="chrome131", referer=ORIGIN + "/", timeout=25
        )
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


def _hot_catalog() -> list[Card]:
    global _hot_memo
    now = time.monotonic()
    with _hot_lock:
        if _hot_memo and now - _hot_memo[0] < _HOT_TTL:
            return _hot_memo[1]
    seen: set[str] = set()
    items: list[Card] = []
    for path in ("/", "/label/top.html", "/genre/13.html", "/genre/20.html", "/genre/14.html", "/genre/15.html", "/genre/2.html"):
        try:
            for card in parse_cards(_get(path)):
                if card.id in seen:
                    continue
                seen.add(card.id)
                items.append(card)
        except Exception:
            continue
    with _hot_lock:
        _hot_memo = (time.monotonic(), items)
    return items


def search(query: str, page: int = 1) -> Listing:
    q = safe_search_query(query)
    page = max(1, min(int(page), 200))
    needle = q.casefold()
    try:
        html = _get(f"/find/-------------.html?wd={quote(q)}")
        listing = _listing(html, page, q, "2")
        if listing.items:
            return listing
    except (SiteBusy, UnsafeURL):
        pass
    items = [c for c in _hot_catalog() if needle in (c.title or "").casefold()]
    start = (page - 1) * 24
    chunk = items[start : start + 24]
    pages = max(1, (len(items) + 23) // 24) if items else 1
    return Listing(items=chunk, page=page, has_next=page < pages, title=q, pages=pages if pages > 1 else None)


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
    html = _get(f"/detail/{video_id}.html")
    routes = _prefer_routes(_routes(html, video_id))
    if not routes:
        raise UnsafeURL("stream not found")
    pick_ep = want or routes[0][2][0]
    chosen: tuple[str, str, list[str]] | None = None
    data: dict = {}
    src = ""
    nxt = ""
    for sid, title, eps in routes[:8]:
        if pick_ep not in eps:
            continue
        play_html = _get(f"/play/{video_id}-{sid}-{pick_ep}.html")
        data = _player_data(play_html)
        src = _play_url(data)
        if not src:
            continue
        nxt = (data.get("url_next") or "").replace("\\/", "/").strip()
        if nxt and not nxt.startswith("https://"):
            nxt = ""
        chosen = (sid, title, eps)
        break
    if not chosen or not src:
        raise UnsafeURL("stream not found")
    sid, _route_title, ep_ids = chosen
    if pick_ep not in ep_ids:
        ep_ids = sorted(set(ep_ids + [pick_ep]), key=lambda x: int(x))
    srcs: dict[str, str] = {pick_ep: src}
    try:
        nxt_id = str(int(pick_ep) + 1)
    except ValueError:
        nxt_id = ""
    if nxt and nxt_id:
        srcs[nxt_id] = nxt
    episodes = [
        Episode(id=eid, title=f"第{eid}集", playlist=_hls(srcs.get(eid, "")))
        for eid in ep_ids
    ]
    master = _hls(src)
    if not master:
        raise UnsafeURL("stream not found")
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
        genres.append(Tag(name=name[:40], slug=m.group(1) if m else name[:40], kind="genre"))
        if len(genres) >= 6:
            break
    related = [c for c in parse_cards(html) if c.id != video_id][:12]
    return VideoDetail(
        id=video_id,
        source="gimy",
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
