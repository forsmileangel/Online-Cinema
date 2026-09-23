"""ChinaQ / 中國人線上看 adapter (chinaq.fun). No eval of remote JS."""

from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup

from .. import http_client
from ..hls_proxy import dlna_media_url, proxied_media
from ..models import Card, Episode, Listing, PickChip, PickGroup, Tag, VideoDetail
from ..security import (
    SiteBusy,
    SourceUnavailable,
    UnsafeURL,
    assert_image_url,
    final_url_still_allowed,
    safe_search_query,
    safe_video_id,
)

ORIGIN = "https://chinaq.fun"
HOSTS = {"chinaq.fun", "www.chinaq.fun"}
DETAIL_RE = re.compile(r"/tv-([a-z]{2})/(\d+)/?(?:$|[?#])")
EP_RE = re.compile(r"/tv-([a-z]{2})/(\d+)/ep(\d+)\.html")
ID_RE = re.compile(r"^([a-z]{2})-(\d{4,12})$")
YEAR_RE = re.compile(r"\s*\(\d{4}\)\s*$")
PAGE = 24
_ALL_TTL = 900.0

REGIONS = {
    "cn": "陸劇",
    "kr": "韓劇",
    "tw": "台劇",
    "jp": "日劇",
    "hk": "港劇",
    "th": "泰劇",
}
HOME_KIND = {
    "/update.html": ("update", "最近更新"),
    "/new.html": ("new", "最新上架"),
    "/tv-cn/": ("cn", "陸劇"),
    "/tv-kr/": ("kr", "韓劇"),
    "/tv-tw/": ("tw", "台劇"),
    "/tv-jp/": ("jp", "日劇"),
}

_all_lock = threading.Lock()
_all_memo: tuple[float, list[Card]] | None = None


def _get(path: str) -> str:
    path = path if path.startswith("/") else "/" + path
    url = ORIGIN + path
    try:
        return http_client.fetch_html_hosts(
            url, HOSTS, impersonate="chrome131", referer=ORIGIN + "/", timeout=40 if path.startswith("/all") else 25
        )
    except SiteBusy:
        raise
    except UnsafeURL as e:
        if "blocked" in str(e).lower():
            raise SiteBusy("中國人線上看") from e
        raise
    except Exception as e:
        low = str(e).lower()
        if "403" in low or "429" in low or "503" in low:
            raise SiteBusy("中國人線上看") from e
        raise


def _cover(num: str) -> str:
    u = f"{ORIGIN}/img_th/{num}.jpg"
    try:
        assert_image_url(u)
    except UnsafeURL:
        host = (urlparse(u).hostname or "").lower()
        if not host.endswith("chinaq.fun"):
            return ""
    return "/api/img?u=" + quote(u, safe="")


def _hls(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if not u.startswith("https://"):
        return ""
    try:
        return proxied_media(u)
    except UnsafeURL:
        return ""


def _make_id(region: str, num: str) -> str:
    return f"{region}-{num}"


def _parse_id(video_id: str) -> tuple[str, str]:
    m = ID_RE.fullmatch(video_id)
    if not m or m.group(1) not in REGIONS:
        raise UnsafeURL("invalid video id")
    return m.group(1), m.group(2)


def _card(region: str, num: str, title: str, duration: str | None, seen: set[str]) -> Card | None:
    if region not in REGIONS:
        return None
    try:
        vid = safe_video_id(_make_id(region, num))
    except UnsafeURL:
        return None
    if vid in seen:
        return None
    name = YEAR_RE.sub("", (title or "").strip()) or vid
    seen.add(vid)
    return Card(id=vid, title=name[:500], cover=_cover(num), duration=duration, source="chinaq")


def parse_cards(html: str) -> list[Card]:
    soup = BeautifulSoup(html or "", "html.parser")
    items: list[Card] = []
    seen: set[str] = set()
    for li in soup.select("ul.drama_rich li"):
        a = li.select_one("a[href*='/tv-']")
        if not a:
            continue
        href = a.get("href") or ""
        m = DETAIL_RE.search(href)
        if not m:
            continue
        title_el = a.select_one(".title")
        title = (title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True))
        ep_el = li.select_one(".episode")
        rem = ep_el.get_text(" ", strip=True) if ep_el else None
        card = _card(m.group(1), m.group(2), title, rem, seen)
        if card:
            items.append(card)
    for a in soup.select("ul.drama_list a[href*='/tv-'], ul.drama_text a[href*='/tv-']"):
        href = a.get("href") or ""
        m = DETAIL_RE.search(href)
        if not m:
            continue
        card = _card(m.group(1), m.group(2), a.get_text(" ", strip=True), None, seen)
        if card:
            items.append(card)
    return items


def parse_home_sections(html: str) -> list[tuple[str, str, list[Card]]]:
    soup = BeautifulSoup(html or "", "html.parser")
    rows: list[tuple[str, str, list[Card]]] = []
    used: set[str] = set()
    for block in soup.select("div.channel"):
        link = block.select_one("h2 a[href]")
        ul = block.select_one("ul.drama_rich")
        if not link or not ul:
            continue
        href = (link.get("href") or "").split("?")[0]
        meta = HOME_KIND.get(href)
        if not meta:
            continue
        kind, title = meta
        if kind in used:
            continue
        cards = parse_cards(str(ul))
        if not cards:
            continue
        used.add(kind)
        rows.append((kind, title, cards[:24]))
    return rows


def _all_cards() -> list[Card]:
    global _all_memo
    now = time.monotonic()
    with _all_lock:
        if _all_memo and now - _all_memo[0] < _ALL_TTL:
            return _all_memo[1]
    items = parse_cards(_get("/all.html"))
    with _all_lock:
        _all_memo = (time.monotonic(), items)
    return items


def _slice(items: list[Card], page: int, title: str) -> Listing:
    page = max(1, min(int(page), 1000))
    total = len(items)
    pages = max(1, (total + PAGE - 1) // PAGE) if total else 1
    start = (page - 1) * PAGE
    chunk = items[start : start + PAGE]
    has_next = page < pages
    return Listing(
        items=chunk,
        page=page,
        has_next=has_next,
        title=title,
        pages=pages if pages > 1 else None,
    )


def home_bundle() -> tuple[list[tuple[str, str, list[Card]]], list[PickGroup]]:
    rows = parse_home_sections(_get("/"))
    picks = [
        PickGroup(
            title="分類",
            items=[
                PickChip(name="最近更新", kind="update"),
                PickChip(name="最新上架", kind="new"),
                PickChip(name="全部戲劇", kind="all"),
            ]
            + [PickChip(name=label, kind=key) for key, label in REGIONS.items()],
        )
    ]
    return rows, picks


def home_rows() -> list[tuple[str, str, list[Card]]]:
    return home_bundle()[0]


def browse(kind: str, slug: str | None = None, page: int = 1) -> Listing:
    kind = (kind or "").strip().lower()
    if kind in ("featured", "update", "hot"):
        return _slice(parse_cards(_get("/update.html")), page, "最近更新")
    if kind == "new":
        return _slice(parse_cards(_get("/new.html")), page, "最新上架")
    if kind == "all":
        return _slice(_all_cards(), page, "全部戲劇")
    if kind in REGIONS:
        prefix = kind + "-"
        items = [c for c in _all_cards() if c.id.startswith(prefix)]
        return _slice(items, page, REGIONS[kind])
    raise UnsafeURL("unknown category")


def search(query: str, page: int = 1) -> Listing:
    q = safe_search_query(query)
    needle = q.casefold()
    items = [c for c in _all_cards() if needle in (c.title or "").casefold()]
    return _slice(items, page, q)


def _safe_ep(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not re.fullmatch(r"[1-9]\d{0,3}", s):
        raise UnsafeURL("invalid episode")
    return s


def _episodes(html: str, region: str, num: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for reg, vid, eid in EP_RE.findall(html or ""):
        if reg != region or vid != num:
            continue
        n = str(int(eid))
        if n in seen:
            continue
        seen.add(n)
        found.append(n)
        if len(found) >= 400:
            break
    return sorted(found, key=lambda x: int(x))


def _qplays(num: str, ep: str) -> list[str]:
    path = f"/qplays/{num}/ep{ep}"
    url = ORIGIN + path
    headers = http_client._headers(ORIGIN + "/")
    headers["Accept"] = "application/json,text/plain,*/*"
    try:
        r = http_client.media_session("chrome131").get(url, headers=headers, timeout=20, allow_redirects=True)
        final_url_still_allowed(str(r.url), HOSTS)
        if r.status_code in (403, 429, 503):
            raise SiteBusy.from_response(r, "中國人線上看")
        if r.status_code == 404:
            raise SourceUnavailable("此來源目前沒有提供這一集的播放連結")
        r.raise_for_status()
        data = r.json()
    except (SiteBusy, SourceUnavailable):
        raise
    except Exception as e:
        low = str(e).lower()
        if "403" in low or "429" in low:
            raise SiteBusy("中國人線上看") from e
        raise UnsafeURL("stream not found") from e
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as e:
            raise UnsafeURL("stream not found") from e
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for item in data.get("video_plays") or []:
        if not isinstance(item, dict):
            continue
        src = str(item.get("play_data") or "").replace("\\/", "/").strip()
        if src.startswith("https://"):
            out.append(src)
        if len(out) >= 8:
            break
    return out


def _pick_stream(urls: list[str]) -> str:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    attempts = 0
    for src in urls:
        if attempts >= 3:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("中國人線上看解析逾時")
        proxied = _hls(src)
        if not proxied:
            continue
        attempts += 1
        try:
            # A listed source can be empty or contain unusable media URLs.
            dlna_media_url(proxied, deadline, validate=True)
            return proxied
        except SiteBusy:
            raise
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    return ""


def fetch_video(video_id: str, ep: str | None = None) -> VideoDetail:
    video_id = safe_video_id(video_id)
    region, num = _parse_id(video_id)
    want = _safe_ep(ep)
    html = _get(f"/tv-{region}/{num}/")
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.select_one("h1")
    title = (h1.get_text(" ", strip=True) if h1 else "") or video_id
    title = title.replace("ChinaQ線上看", "").strip() or video_id
    desc_el = soup.select_one("#summary")
    desc = desc_el.get_text(" ", strip=True) if desc_el else None
    if desc:
        desc = desc.replace("【簡介】", "").strip()
    ep_ids = _episodes(html, region, num)
    if not ep_ids:
        trailer_path = f"/tv-{region}/{num}/yu_gao_pian.html"
        if soup.find("a", href=trailer_path):
            raise SourceUnavailable("此來源目前只有預告片，尚未提供正片集數")
        raise SourceUnavailable("此來源目前沒有可播放的正片集數")
    if want and want not in ep_ids:
        raise SourceUnavailable("此來源尚未提供指定集數，請選擇已上架集數")
    pick = want or ep_ids[0]
    master = _pick_stream(_qplays(num, pick))
    if not master:
        raise UnsafeURL("stream not found")
    episodes = [
        Episode(id=eid, title=f"第{eid}集", playlist=master if eid == pick else "")
        for eid in ep_ids
    ]
    genres: list[Tag] = []
    text = soup.get_text("\n", strip=True)
    gm = re.search(r"【類型】\s*([^\n【]+)", text)
    if gm:
        seen: set[str] = set()
        for part in re.split(r"[/／,，、]", gm.group(1)):
            name = part.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            genres.append(Tag(name=name[:40], slug=name[:40], kind="tag", browsable=False))
            if len(genres) >= 8:
                break
    release = None
    rm = re.search(r"【首播】\s*(\d{4}-\d{2}-\d{2})", text)
    if rm:
        release = rm.group(1)
    related = [c for c in parse_cards(html) if c.id != video_id][:12]
    return VideoDetail(
        id=video_id,
        source="chinaq",
        resolved_episode_id=pick,
        title=title[:500],
        cover=_cover(num),
        description=(desc[:2000] if desc else None),
        release_date=release,
        genres=genres,
        playlist=master,
        episodes=episodes,
        related=related,
    )


def proxied_cover(video_id: str) -> str:
    try:
        _region, num = _parse_id(safe_video_id(video_id))
    except UnsafeURL:
        return ""
    return _cover(num)
