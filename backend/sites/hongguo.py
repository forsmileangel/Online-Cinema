"""Hongguo short-drama adapter (hongguoapp.cn). No eval of remote JS."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    safe_slug,
    safe_video_id,
)

ORIGIN = "https://www.hongguoapp.cn"
HOSTS = {"hongguoapp.cn", "www.hongguoapp.cn"}
DETAIL_RE = re.compile(r"/voddetail/(\d+)\.html")
PLAY_RE = re.compile(r"/vodplay/(\d+)-1-(\d+)\.html")
TYPE_ID = "51"
CLASSES = {
    "gudian": ("古装", "古裝"),
    "dushi": ("都市", "都市"),
    "jiating": ("家庭", "家庭"),
    "xiju": ("喜剧", "喜劇"),
    "juqing": ("剧情", "劇情"),
    "qihuan": ("奇幻", "奇幻"),
    "qingchun": ("青春偶像", "青春偶像"),
    "dongzuo": ("动作", "動作"),
    "lishi": ("历史", "歷史"),
    "wangju": ("网剧", "網劇"),
    "zhanzheng": ("战争", "戰爭"),
    "fanzui": ("犯罪", "犯罪"),
    "jingdian": ("经典", "經典"),
    "xiangcun": ("乡村", "鄉村"),
    "qingjing": ("情景", "情景"),
    "shangzhan": ("商战", "商戰"),
    "qita": ("其他", "其他"),
}


def _get(path: str) -> str:
    path = path if path.startswith("/") else "/" + path
    url = ORIGIN + path
    try:
        return http_client.fetch_html_hosts(
            url, HOSTS, impersonate="chrome131", referer=ORIGIN + "/", timeout=25
        )
    except UnsafeURL as e:
        if "blocked" in str(e).lower():
            raise SiteBusy("紅果短劇") from e
        raise
    except Exception as e:
        low = str(e).lower()
        if "403" in low or "429" in low or "503" in low:
            raise SiteBusy("紅果短劇") from e
        raise


def _cover(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("/"):
        if u.startswith("/upload/"):
            u = "https://img.picbf.com" + u
        else:
            u = ORIGIN + u
    if not u.startswith("https://"):
        return ""
    if "placeholder" in u or "load.gif" in u or "qrserver" in u or "tmdb.org" in u:
        return ""
    if "?" in u and "picbf.com" in u:
        u = u.split("?", 1)[0]
    try:
        assert_image_url(u)
    except UnsafeURL:
        host = (urlparse(u).hostname or "").lower()
        if not (host.endswith("picbf.com") or host.endswith("hongguoapp.cn") or host.endswith("wangwangzyimg.com")):
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


def _card_from_anchor(a, seen: set[str]) -> Card | None:
    href = a.get("href") or ""
    m = DETAIL_RE.search(href)
    if not m:
        return None
    try:
        vid = safe_video_id(m.group(1))
    except UnsafeURL:
        return None
    if vid in seen:
        return None
    title = (a.get("title") or a.get_text(" ", strip=True) or vid).strip()
    img = a.select_one("img")
    src = ""
    if img:
        src = img.get("data-original") or img.get("data-src") or img.get("src") or ""
    if not src:
        src = a.get("data-original") or ""
    parent = a.parent
    rem = ""
    box = parent if parent else a
    rem_el = box.select_one(".hg-episode, .remarks, .hl-pic-text")
    if rem_el:
        rem = rem_el.get_text(" ", strip=True)
    seen.add(vid)
    return Card(id=vid, title=title[:500], cover=_cover(src), duration=rem or None, source="hongguo")


def parse_cards(html: str) -> list[Card]:
    soup = BeautifulSoup(html or "", "html.parser")
    items: list[Card] = []
    seen: set[str] = set()
    nodes = soup.select("a.hg-card[href*='/voddetail/']")
    if not nodes:
        nodes = soup.select("a.hl-item-thumb[href*='/voddetail/']")
    if not nodes:
        nodes = soup.select("a[href*='/voddetail/']")
    for a in nodes:
        card = _card_from_anchor(a, seen)
        if card:
            items.append(card)
        if len(items) >= 48:
            break
    return items


def _pages_from_html(html: str) -> int | None:
    soup = BeautifulSoup(html or "", "html.parser")
    tip = soup.select_one(".hl-page-tips")
    if tip:
        m = re.search(r"(\d+)\s*/\s*(\d+)", tip.get_text(" ", strip=True))
        if m:
            last = int(m.group(2))
            if 1 <= last <= 2000:
                return last
    nums = [int(n) for n in re.findall(r"-----(\d+)---\.html", html or "")]
    nums = [n for n in nums if 1 <= n <= 2000]
    return max(nums) if nums else None


def _player_data(html: str) -> dict:
    i = (html or "").find("var player_aaaa")
    if i < 0:
        return {}
    j = html.find("{", i)
    if j < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(html[j:])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _play_url(data: dict) -> str:
    raw = (data.get("url") or "").replace("\\/", "/").strip()
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
    return raw if raw.startswith("https://") else ""


def _safe_ep(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not re.fullmatch(r"[1-9]\d{0,3}", s):
        raise UnsafeURL("invalid episode")
    return s


def _show_path(kind: str, page: int) -> str:
    page = max(1, min(int(page), 1000))
    if kind in CLASSES:
        cls = quote(CLASSES[kind][0])
        if page <= 1:
            return f"/vodshow/{TYPE_ID}---{cls}--------.html"
        return f"/vodshow/{TYPE_ID}---{cls}-----{page}---.html"
    if page <= 1:
        return f"/vodshow/{TYPE_ID}-----------.html"
    return f"/vodshow/{TYPE_ID}--------{page}---.html"


def home_bundle() -> tuple[list[tuple[str, str, list[Card]]], list[PickGroup]]:
    html = _get("/")
    rows: list[tuple[str, str, list[Card]]] = []
    hot = parse_cards(html)
    if hot:
        rows.append(("featured", "熱門短劇", hot[:24]))
    extra = [
        ("gudian", "古裝", "gudian"),
        ("dushi", "都市", "dushi"),
        ("jiating", "家庭", "jiating"),
        ("xiju", "喜劇", "xiju"),
        ("qihuan", "奇幻", "qihuan"),
        ("all", "全部短劇", "all"),
    ]

    def one(item: tuple[str, str, str]) -> tuple[str, str, list[Card]]:
        kind, title, key = item
        page = _get(_show_path(key if key != "all" else "all", 1))
        return kind, title, parse_cards(page)[:24]

    got: dict[str, tuple[str, str, list[Card]]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(one, item): item[0] for item in extra}
        for fut in as_completed(futs):
            kind = futs[fut]
            try:
                got[kind] = fut.result()
            except Exception:
                continue
    for kind, title, _key in extra:
        if kind in got and got[kind][2]:
            rows.append(got[kind])
    picks = [
        PickGroup(
            title="分類",
            items=[PickChip(name="全部短劇", kind="all", slug="")]
            + [PickChip(name=tw, kind=key, slug="") for key, (_cn, tw) in CLASSES.items()],
        )
    ]
    return rows, picks


def home_rows() -> list[tuple[str, str, list[Card]]]:
    return home_bundle()[0]


def _listing_from(html: str, page: int, title: str) -> Listing:
    items = parse_cards(html)
    pages = _pages_from_html(html)
    if pages:
        has_next = page < pages
    else:
        has_next = len(items) >= 24
    return Listing(
        items=items[:48],
        page=page,
        has_next=has_next and page < 1000,
        title=title,
        pages=pages if pages and pages > 1 else None,
    )


def browse(kind: str, slug: str | None = None, page: int = 1) -> Listing:
    page = max(1, min(int(page), 1000))
    kind = (kind or "").strip().lower()
    title = "全部短劇"
    path_kind = "all"
    if kind in ("featured", "hot"):
        path_kind = "all"
        title = "熱門短劇" if kind == "featured" else "全部短劇"
    elif kind in CLASSES:
        path_kind = kind
        title = CLASSES[kind][1]
    elif kind in ("all", "type", "51"):
        path_kind = "all"
        title = "全部短劇"
    elif kind == "class" and slug:
        slug = safe_slug(slug)
        match = next((k for k, v in CLASSES.items() if v[0] == slug or v[1] == slug), None)
        if match:
            path_kind = match
            title = CLASSES[match][1]
        else:
            html = _get(
                f"/vodshow/{TYPE_ID}---{quote(slug)}--------.html"
                if page <= 1
                else f"/vodshow/{TYPE_ID}---{quote(slug)}-----{page}---.html"
            )
            return _listing_from(html, page, slug)
    else:
        raise UnsafeURL("unknown category")
    html = _get(_show_path(path_kind, page))
    return _listing_from(html, page, title)


def _get_json(path: str) -> dict:
    path = path if path.startswith("/") else "/" + path
    url = ORIGIN + path
    headers = http_client._headers(ORIGIN + "/")
    headers["Accept"] = "application/json,text/javascript,*/*"
    headers["X-Requested-With"] = "XMLHttpRequest"
    try:
        r = http_client.media_session("chrome131").get(url, headers=headers, timeout=20, allow_redirects=True)
        final_url_still_allowed(str(r.url), HOSTS)
        if r.status_code == 403:
            raise SiteBusy("紅果短劇")
        r.raise_for_status()
        data = r.json()
    except SiteBusy:
        raise
    except Exception as e:
        low = str(e).lower()
        if "403" in low or "429" in low:
            raise SiteBusy("紅果短劇") from e
        raise UnsafeURL("search failed") from e
    return data if isinstance(data, dict) else {}


def search(query: str, page: int = 1) -> Listing:
    q = safe_search_query(query)
    page = max(1, min(int(page), 50))
    data = _get_json(f"/index.php/ajax/suggest?mid=1&wd={quote(q)}&limit=50&page={page}")
    docs = data.get("list") if isinstance(data, dict) else None
    items: list[Card] = []
    seen: set[str] = set()
    for d in docs or []:
        if not isinstance(d, dict):
            continue
        raw_id = str(d.get("id") or "")
        try:
            vid = safe_video_id(raw_id)
        except UnsafeURL:
            continue
        if vid in seen:
            continue
        seen.add(vid)
        title = str(d.get("name") or vid)[:500]
        items.append(Card(id=vid, title=title, cover=_cover(str(d.get("pic") or "")), source="hongguo"))
        if len(items) >= 50:
            break
    total = int(data.get("total") or 0) if isinstance(data, dict) else 0
    pagecount = int(data.get("pagecount") or 0) if isinstance(data, dict) else 0
    pages = pagecount if pagecount > 1 else (min(50, max(1, (total + 49) // 50)) if total > 50 else None)
    return Listing(items=items, page=page, has_next=(pages or 1) > page, title=q, pages=pages)


def _detail_episodes(html: str, video_id: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for vid, nid in PLAY_RE.findall(html or ""):
        if vid != video_id or nid in seen:
            continue
        seen.add(nid)
        found.append(nid)
        if len(found) >= 200:
            break
    return sorted(found, key=lambda x: int(x) if x.isdigit() else 0)


def _tags_from_class(raw: str) -> list[Tag]:
    out: list[Tag] = []
    seen: set[str] = set()
    for part in str(raw or "").split(","):
        name = part.strip()
        if not name:
            continue
        try:
            slug = safe_slug(name)
        except UnsafeURL:
            continue
        if slug in seen:
            continue
        seen.add(slug)
        out.append(Tag(name=name[:80], slug=slug, kind="class"))
        if len(out) >= 12:
            break
    return out


def fetch_video(video_id: str, ep: str | None = None) -> VideoDetail:
    video_id = safe_video_id(video_id)
    want = _safe_ep(ep) or "1"
    play_path = f"/vodplay/{video_id}-1-{want}.html"
    detail_html = ""
    play_html = ""
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_detail = pool.submit(_get, f"/voddetail/{video_id}.html")
        f_play = pool.submit(_get, play_path)
        try:
            detail_html = f_detail.result()
        except Exception:
            detail_html = ""
        play_html = f_play.result()
    data = _player_data(play_html)
    src = _play_url(data)
    nxt = (data.get("url_next") or "").replace("\\/", "/").strip()
    if nxt and not nxt.startswith("https://"):
        nxt = ""
    ep_ids = _detail_episodes(detail_html or play_html, video_id)
    if want not in ep_ids:
        ep_ids = sorted(set(ep_ids + [want]), key=lambda x: int(x) if x.isdigit() else 0)
    if not ep_ids:
        ep_ids = [want]
    srcs: dict[str, str] = {}
    if src:
        srcs[want] = src
    try:
        nxt_id = str(int(want) + 1)
    except ValueError:
        nxt_id = ""
    if nxt and nxt_id:
        srcs[nxt_id] = nxt
    episodes = [
        Episode(id=eid, title=f"第{eid}集", playlist=_hls(srcs.get(eid, "")))
        for eid in ep_ids
    ]
    master = _hls(srcs.get(want) or src)
    if not master:
        master = next((e.playlist for e in episodes if e.playlist), "")
    if not master:
        raise UnsafeURL("stream not found")
    vod = data.get("vod_data") if isinstance(data.get("vod_data"), dict) else {}
    soup = BeautifulSoup(detail_html or play_html, "html.parser")
    title_el = soup.select_one("h2.hl-dc-title, h1, .hg-title")
    title = str(vod.get("vod_name") or "").strip() or (title_el.get_text(" ", strip=True) if title_el else "") or video_id
    desc_el = soup.select_one(".hl-content-text, .sketch, .hl-full-items, .hg-desc")
    desc = desc_el.get_text(" ", strip=True) if desc_el else None
    cover = ""
    img = soup.select_one(".hl-dc-pic img, .hl-item-thumb, img.hl-lazy")
    if img:
        cover = _cover(img.get("data-original") or img.get("src") or "")
    if not cover:
        og = soup.find("meta", attrs={"property": "og:image"})
        cover = _cover(og.get("content") if og else "")
    related = [c for c in parse_cards(detail_html or play_html) if c.id != video_id][:12]
    return VideoDetail(
        id=video_id,
        source="hongguo",
        title=title[:500],
        cover=cover,
        description=(desc[:2000] if desc else None),
        genres=_tags_from_class(str(vod.get("vod_class") or "")),
        playlist=master,
        episodes=episodes,
        related=related,
    )


def proxied_cover(video_id: str) -> str:
    return ""
