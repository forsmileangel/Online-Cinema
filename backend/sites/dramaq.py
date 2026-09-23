"""DramasQ adapter (dramasq.io). No eval of remote JS."""

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
    final_url_still_allowed,
    safe_search_query,
    safe_video_id,
)

ORIGIN = "https://dramasq.io"
HOSTS = {"dramasq.io", "www.dramasq.io"}
DETAIL_RE = re.compile(r"/detail/(\d+)\.html")
PLAY_RE = re.compile(r"/vodplay/(\d+)/([A-Za-z0-9]+)\.html")
TOKEN_RE = re.compile(r"(?:ep[1-9]\d{0,7}|[1-9]\d{7}|[1-9]\d{0,3})")
EP_NUM_RE = re.compile(r"^ep([1-9]\d*)$")
PAGE = 48
_ALL_TTL = 900.0
_all_lock = threading.Lock()
_all_memo: tuple[float, list[Card]] | None = None

REGIONS = {
    "tw": "台劇",
    "cn": "陸劇",
    "kr": "韓劇",
    "jp": "日劇",
    "us": "美劇",
}


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
            raise SiteBusy("DramaQ") from e
        raise
    except Exception as e:
        low = str(e).lower()
        if "403" in low or "429" in low or "503" in low:
            raise SiteBusy("DramaQ") from e
        raise


def _cover(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("/"):
        u = ORIGIN + u
    if not u.startswith("https://"):
        return ""
    try:
        assert_image_url(u)
    except UnsafeURL:
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


def _safe_token(raw: str | None) -> str:
    s = (raw or "").strip()
    if not TOKEN_RE.fullmatch(s):
        raise UnsafeURL("invalid episode")
    return s


def _token_path(token: str) -> str:
    if EP_NUM_RE.match(token) or re.fullmatch(r"[1-9]\d{7}", token):
        return token
    if re.fullmatch(r"[1-9]\d{0,3}", token):
        return "ep" + token
    raise UnsafeURL("invalid episode")


def parse_cards(html: str) -> list[Card]:
    soup = BeautifulSoup(html or "", "html.parser")
    items: list[Card] = []
    seen: set[str] = set()
    for a in soup.select(".card a[href*='/detail/'], a.drama[href*='/detail/']"):
        m = DETAIL_RE.search(a.get("href") or "")
        if not m:
            continue
        try:
            vid = safe_video_id(m.group(1))
        except UnsafeURL:
            continue
        if vid in seen:
            continue
        title_el = a.select_one(".title")
        title = (title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)) or vid
        title = re.sub(r"\s+第\d+集$", "", title).strip() or vid
        img = a.select_one("img")
        src = ""
        if img:
            src = img.get("src") or img.get("data-src") or ""
        seen.add(vid)
        items.append(Card(id=vid, title=title[:500], cover=_cover(src), source="dramaq"))
    return items


def parse_home_sections(html: str) -> list[tuple[str, str, list[Card]]]:
    soup = BeautifulSoup(html or "", "html.parser")
    rows: list[tuple[str, str, list[Card]]] = []
    used: set[str] = set()
    for heading in soup.select("h1, h2, h3"):
        raw = heading.get_text(" ", strip=True)
        if "最近更新" in raw:
            kind, title = "latest", "最近更新"
        elif "熱門" in raw:
            kind, title = "hot", "最近熱門"
        else:
            continue
        if kind in used:
            continue
        panel = heading.find_next(class_="card-panel")
        cards = parse_cards(str(panel) if panel else "")
        if not cards:
            continue
        used.add(kind)
        rows.append((kind, title, cards[:24]))
    return rows


def home_bundle() -> tuple[list[tuple[str, str, list[Card]]], list[PickGroup]]:
    rows = parse_home_sections(_get("/"))
    picks = [
        PickGroup(
            title="分類",
            items=[
                PickChip(name="最近更新", kind="latest"),
                PickChip(name="最新上架", kind="new"),
                PickChip(name="最近熱門", kind="hot"),
                PickChip(name="全部戲劇", kind="all"),
            ]
            + [PickChip(name=label, kind=key) for key, label in REGIONS.items()],
        )
    ]
    return rows, picks


def home_rows() -> list[tuple[str, str, list[Card]]]:
    return home_bundle()[0]


def _slice(items: list[Card], page: int, title: str) -> Listing:
    page = max(1, min(int(page), 1000))
    total = len(items)
    pages = max(1, (total + PAGE - 1) // PAGE) if total else 1
    start = (page - 1) * PAGE
    chunk = items[start : start + PAGE]
    return Listing(
        items=chunk,
        page=page,
        has_next=page < pages,
        title=title,
        pages=pages if pages > 1 else None,
    )


def _all_cards() -> list[Card]:
    global _all_memo
    now = time.monotonic()
    with _all_lock:
        if _all_memo and now - _all_memo[0] < _ALL_TTL:
            return _all_memo[1]
    items = parse_cards(_get("/all/"))
    with _all_lock:
        _all_memo = (time.monotonic(), items)
    return items


def browse(kind: str, slug: str | None = None, page: int = 1) -> Listing:
    page = max(1, min(int(page), 1000))
    kind = (kind or "").strip().lower()
    if kind in ("featured", "update"):
        kind = "latest"
    if kind == "hot":
        rows = parse_home_sections(_get("/"))
        items = next((cards for key, _title, cards in rows if key == "hot"), [])
        return Listing(items=items[:48], page=1, has_next=False, title="最近熱門")
    if kind == "all":
        return _slice(_all_cards(), page, "全部戲劇")
    if kind in ("latest", "new"):
        title = "最近更新" if kind == "latest" else "最新上架"
        return Listing(items=parse_cards(_get(f"/{kind}/"))[:72], page=1, has_next=False, title=title)
    if kind not in REGIONS:
        raise UnsafeURL("unknown category")
    path = f"/type-tv/{kind}/" if page <= 1 else f"/type-tv/{kind}/?page={page}"
    items = parse_cards(_get(path))
    return Listing(
        items=items[:72],
        page=page,
        has_next=len(items) >= 72,
        title=REGIONS[kind],
        pages=None,
    )


def search(query: str, page: int = 1) -> Listing:
    q = safe_search_query(query)
    page = max(1, min(int(page), 50))
    items = parse_cards(_get(f"/search?q={quote(q)}"))
    return _slice(items, page, q)


def _tokens(html: str, video_id: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for vid, token in PLAY_RE.findall(html or ""):
        if vid != video_id:
            continue
        try:
            token = _safe_token(token)
        except UnsafeURL:
            continue
        if token in seen:
            continue
        seen.add(token)
        found.append(token)
    return found


def _fill_numbered(tokens: list[str]) -> list[str]:
    nums = []
    for token in tokens:
        m = EP_NUM_RE.match(token)
        if not m:
            return tokens
        nums.append(int(m.group(1)))
    count = max(nums) if nums else 0
    if not nums or count < 1 or count > 400 or count < max(nums):
        return tokens
    return [f"ep{n}" for n in range(1, count + 1)]


def _sort_tokens(tokens: list[str]) -> list[str]:
    def key(token: str) -> tuple[int, int]:
        m = EP_NUM_RE.match(token)
        if m:
            return (0, int(m.group(1)))
        if token.isdigit():
            return (1, int(token))
        return (2, 0)

    return sorted(tokens, key=key)


def _plays(video_id: str, token: str) -> list[str]:
    path = "/drq/" + video_id + "/" + _token_path(token)
    url = ORIGIN + path
    headers = http_client._headers(ORIGIN + "/")
    headers["Accept"] = "application/json,text/plain,*/*"
    try:
        response = http_client.media_session("chrome131").get(url, headers=headers, timeout=20, allow_redirects=True)
        final_url_still_allowed(str(response.url), HOSTS)
        if response.status_code in (403, 429, 503):
            raise SiteBusy.from_response(response, "DramaQ")
        response.raise_for_status()
        data = response.json()
    except SiteBusy:
        raise
    except Exception as e:
        low = str(e).lower()
        if "403" in low or "429" in low:
            raise SiteBusy("DramaQ") from e
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


def _episode_title(token: str) -> str:
    m = EP_NUM_RE.match(token)
    if m:
        return f"第{int(m.group(1))}集"
    return token


def fetch_video(video_id: str, ep: str | None = None) -> VideoDetail:
    video_id = safe_video_id(video_id)
    want = _safe_token(ep) if ep else None
    if want:
        want = _token_path(want) if re.fullmatch(r"[1-9]\d{0,3}", want) else want
    detail_html = _get(f"/detail/{video_id}.html")
    tokens = _sort_tokens(_fill_numbered(_tokens(detail_html, video_id)))
    pick = want or (tokens[0] if tokens else "ep1")
    pick = _safe_token(pick)
    play_html = _get(f"/vodplay/{video_id}/{_token_path(pick)}.html")
    for token in _tokens(play_html, video_id):
        if token not in tokens:
            tokens.append(token)
    if pick not in tokens:
        tokens.append(pick)
    tokens = _sort_tokens(_fill_numbered(tokens))
    master = ""
    for src in _plays(video_id, pick):
        master = _hls(src)
        if master:
            break
    if not master:
        raise UnsafeURL("stream not found")
    episodes = [
        Episode(id=token, title=_episode_title(token), playlist=master if token == pick else "")
        for token in tokens
    ]
    soup = BeautifulSoup(detail_html, "html.parser")
    h1 = soup.select_one("h1")
    title = (h1.get_text(" ", strip=True) if h1 else "") or video_id
    title = re.sub(r"\s*線上看\s*$", "", title).strip() or video_id
    intro = soup.select_one(".intro")
    text = intro.get_text("\n", strip=True) if intro else ""
    genres: list[Tag] = []
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
    rm = re.search(r"【首播】\s*(\d{4})", text) or re.search(r"【年份】\s*(\d{4})", text)
    if rm:
        release = rm.group(1)
    img = soup.select_one("img")
    cover = _cover(img.get("src") if img else "") or _cover(f"/ssimg/{video_id}.jpg")
    related = [c for c in parse_cards(detail_html) if c.id != video_id][:12]
    return VideoDetail(
        id=video_id,
        source="dramaq",
        resolved_episode_id=pick,
        title=title[:500],
        cover=cover,
        description=(text[:2000] if text else None),
        release_date=release,
        genres=genres,
        playlist=master,
        episodes=episodes,
        related=related,
    )


def proxied_cover(video_id: str) -> str:
    try:
        vid = safe_video_id(video_id)
    except UnsafeURL:
        return ""
    if not vid.isdigit():
        return ""
    return _cover(f"/ssimg/{vid}.jpg")
