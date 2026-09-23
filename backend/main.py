from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from fastapi.staticfiles import StaticFiles

from . import catalog
from . import cast as chromecast
from . import cast_session
from . import cast_devices as known_devices
from . import db
from . import lan
from . import tv_session
from . import hls_proxy
from . import http_client
from . import settings as S
from . import sites
from . import translate as translate_mod
from .models import (
    FavoriteIn,
    HistoryIn,
    HomePayload,
    HomeRow,
    SettingsIn,
    SettingsOut,
    TranslateIn,
    TranslateOut,
    TvPairIn,
    CastPlayIn,
    CastControlIn,
    CastSelectIn,
    CastSessionControlIn,
    CastDeviceConfigIn,
)
from .security import (
    IMAGE_HOSTS,
    SiteBusy,
    UnsafeURL,
    assert_hls_url,
    assert_image_url,
    safe_video_id,
)

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "dist"

class AccessGuard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client = request.client.host if request.client else "127.0.0.1"
        lan_on = db.get_setting("lan_tv", "") == "1"
        if not lan.is_allowed_client(client):
            return JSONResponse({"detail": "拒絕"}, status_code=403)
        if not lan_on and not lan.is_loopback(client):
            return JSONResponse({"detail": "僅本機。請在設定開啟電視連線後重開 start.bat。"}, status_code=403)
        if lan_on and not lan.is_loopback(client):
            path = request.url.path
            if path.startswith("/assets") or path.startswith("/api/hls") or path.startswith("/api/img"):
                return await call_next(request)
            if path in ("/api/tv/pair", "/api/health", "/favicon.ico"):
                return await call_next(request)
            if not tv_session.valid_cookie(request.cookies.get("cinema_tv")):
                if path.startswith("/api/") and not path.startswith("/api/cast"):
                    return JSONResponse({"detail": "請先用配對碼連電視"}, status_code=401)
        return await call_next(request)


app = FastAPI(title="Online Cinema", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(AccessGuard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:6970",
        "http://localhost:6970",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def media_cors(request: Request, call_next):
    # Receiver apps run on Google/LG origins. Only the media endpoint is public
    # across origins; the existing LAN gate still protects every media request.
    if request.url.path != "/api/hls":
        return await call_next(request)
    if request.method == "OPTIONS":
        response = Response(status_code=204)
    else:
        response = await call_next(request)
    response.headers.update({
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    })
    if request.method != "OPTIONS" and response.status_code < 400:
        media_url = parse_qs(request.url.query).get("u", [""])[0]
        seek = "01" if urlparse(media_url).path.lower().endswith(".mp4") else "00"
        response.headers["transferMode.dlna.org"] = "Streaming"
        response.headers["contentFeatures.dlna.org"] = f"DLNA.ORG_OP={seek};DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
    return response


def _err(exc: Exception, status: int = 400) -> HTTPException:
    if isinstance(exc, TimeoutError):
        return HTTPException(504, "來源載入逾時，請重試")
    if isinstance(exc, SiteBusy):
        return HTTPException(503, f"{exc.site} 暫時限制連線，等十幾秒再搜或再點播放")
    if isinstance(exc, UnsafeURL):
        return HTTPException(status, "請求被拒絕")
    return HTTPException(502, "來源站暫時無法使用")


@app.get("/api/health")
def health():
    return {"ok": True}


def _src(source: str | None) -> str:
    return catalog.normalize_source(source or catalog.current_source())


@app.get("/api/home")
def home(source: str | None = None):
    src = _src(source)
    try:
        site = sites.get(src)
        picks = []
        home_error = None
        bundle = getattr(site, "home_bundle", None)
        if callable(bundle):
            rows_raw, picks = bundle()
        else:
            rows_raw = site.home_rows()
        history = db.list_history(source=src)
        cont = []
        for h in history:
            dur = float(h["duration_sec"] or 0)
            pos = float(h["position_sec"] or 0)
            if dur <= 0 or pos / dur >= 0.95:
                continue
            cont.append(
                {
                    "id": h["video_id"],
                    "source": h.get("source") or src,
                    "title": h["title"],
                    "original_title": h["title"],
                    "cover": h["cover"] or site.proxied_cover(h["video_id"]),
                    "duration": None,
                    "progress": max(0.0, min(pos / dur, 1.0)),
                }
            )
        return HomePayload(
            continue_watching=cont[:20],
            rows=[HomeRow(kind=k, title=t, items=items) for k, t, items in rows_raw],
            mirror="",
            source=src,
            picks=picks or [],
            error=home_error,
        )
    except Exception as e:
        raise _err(e, 502) from e


@app.get("/api/browse/{kind}")
def browse(kind: str, page: int = Query(1, ge=1, le=1000), source: str | None = None):
    src = _src(source)
    try:
        return sites.get(src).browse(kind, page=page)
    except Exception as e:
        raise _err(e) from e


@app.get("/api/browse/{kind}/{slug}")
def browse_slug(kind: str, slug: str, page: int = Query(1, ge=1, le=1000), source: str | None = None):
    src = _src(source)
    try:
        return sites.get(src).browse(kind, slug=slug, page=page)
    except Exception as e:
        raise _err(e) from e


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=50),
    page: int = Query(1, ge=1, le=1000),
    source: str | None = None,
):
    src = _src(source)
    try:
        return sites.get(src).search(q, page=page)
    except Exception as e:
        raise _err(e) from e


@app.get("/api/video/{source}/{video_id}")
def video_on_source(source: str, video_id: str, ep: str | None = Query(None, max_length=16)):
    src = catalog.normalize_source(source)
    try:
        video_id = safe_video_id(video_id)
        site = sites.get(src)
        detail = site.fetch_video(video_id, ep=ep)
        detail.source = src
        detail.favorited = db.is_favorite(video_id, src)
        hist = db.get_history_item(video_id, src)
        if hist:
            detail.episode_id = hist.get("episode_id")
            if not detail.episodes or detail.episode_id:
                detail.position_sec = float(hist["position_sec"] or 0)
        return detail
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e, 502) from e


@app.get("/api/video/{video_id}")
def video(video_id: str):
    return video_on_source(catalog.current_source(), video_id)


@app.post("/api/translate")
def translate_text(body: TranslateIn):
    title = (body.title or "")[:500]
    desc = (body.description or "")[:2000]
    names = [(n or "")[:200] for n in (body.names or [])[:40]]
    try:
        out_names: list[str] = []
        if names:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=8) as pool:
                out_names = list(pool.map(translate_mod.to_zh_tw, names))
        return TranslateOut(
            title=translate_mod.to_zh_tw(title) if title else "",
            description=translate_mod.to_zh_tw(desc) if desc else "",
            names=out_names,
        )
    except Exception as e:
        raise _err(e, 502) from e


@app.get("/api/favorites")
def favorites():
    items = []
    for row in db.list_favorites():
        items.append(
            {
                "id": row["video_id"],
                "title": row["title"],
                "cover": row["cover"] or "",
                "source": row.get("source") or "hongguo",
            }
        )
    return {"items": items}


@app.post("/api/favorites")
def add_favorite(body: FavoriteIn):
    try:
        db.add_favorite(body.id, body.title, body.cover, source=body.source)
        return {"ok": True}
    except Exception as e:
        raise _err(e) from e


@app.delete("/api/favorites/{video_id}")
def del_favorite(video_id: str, source: str = "hongguo"):
    try:
        db.remove_favorite(video_id, source=source)
        return {"ok": True}
    except Exception as e:
        raise _err(e) from e


@app.get("/api/history")
def history():
    return {"items": db.list_history()}


@app.put("/api/history")
def put_history(body: HistoryIn):
    try:
        db.upsert_history(
            body.id, body.title, body.cover, body.position_sec, body.duration_sec, source=body.source, episode_id=body.episode_id
        )
        return {"ok": True}
    except Exception as e:
        raise _err(e) from e


@app.delete("/api/history")
def wipe_history():
    db.clear_history()
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    ips = lan.ipv4_lan()
    code = tv_session.ensure_code()
    lan_on = db.get_setting("lan_tv", "") == "1"
    tv_url = f"http://{ips[0]}:{S.PORT}/tv?c={code}" if ips and lan_on else ""
    return SettingsOut(
        source=catalog.current_source(),
        theme=db.get_setting("theme", ""),
        lan_tv=lan_on,
        lan_ips=ips,
        tv_code=code if lan_on else "",
        tv_url=tv_url,
        sources=sites.available(),
    )


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    try:
        if body.source is not None:
            db.set_setting("source", catalog.normalize_source(body.source))
        if body.theme is not None:
            db.set_setting("theme", body.theme[:8000])
        if body.lan_tv is not None:
            db.set_setting("lan_tv", "1" if body.lan_tv else "0")
            if body.lan_tv:
                tv_session.ensure_code()
        return get_settings()
    except Exception as e:
        raise _err(e) from e


@app.get("/api/cast/devices")
def cast_devices():
    try:
        devices = chromecast.discover()
        return {"devices": devices, "selected": chromecast.selected_uuid(), "origin": chromecast.lan_media_origin()}
    except Exception as e:
        raise HTTPException(502, f"掃描電視失敗：{e}") from e


@app.post("/api/cast/select")
def cast_select(body: CastSelectIn):
    try:
        chromecast.select(body.uuid)
        return {"ok": True, "selected": body.uuid}
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/cast/play")
def cast_play(body: CastPlayIn):
    if db.get_setting("lan_tv", "") != "1":
        raise HTTPException(400, "請先在設定開啟「允許投放」，然後重開 start.bat")
    if body.managed:
        try:
            uuid = body.uuid or chromecast.selected_uuid()
            chromecast.select(uuid)
            return JSONResponse(cast_session.start({**body.model_dump(), "uuid": uuid}), status_code=202)
        except Exception as e:
            raise HTTPException(400, str(e)) from e
    parsed = urlparse(body.url)
    urls = parse_qs(parsed.query).get("u", [])
    if parsed.scheme or parsed.netloc or parsed.path != "/api/hls" or len(urls) != 1:
        raise HTTPException(400, "請從影片頁選擇可播放的串流")
    try:
        upstream = assert_hls_url(urls[0])
        uuid = body.uuid or chromecast.selected_uuid()
        if not uuid:
            raise HTTPException(400, "請先選一台電視")
        origin = chromecast.lan_media_origin(uuid)
        chromecast.check_media_origin(origin)
        url = hls_proxy.proxied_media(upstream, origin)
        mime = "video/mp4" if urlparse(upstream).path.lower().endswith(".mp4") else "application/vnd.apple.mpegurl"
        cast_session.cancel(uuid)
        return chromecast.play(url, mime, body.title, body.position_sec, uuid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, str(e) or "投放失敗") from e


@app.post("/api/cast/control")
def cast_control(body: CastControlIn):
    try:
        return chromecast.control(body.action, body.position_sec, body.uuid, body.content_id)
    except Exception as e:
        raise HTTPException(502, str(e) or "控制失敗") from e


@app.get("/api/cast/status")
def cast_status(uuid: str = ""):
    try:
        return chromecast.status(uuid)
    except Exception as e:
        raise HTTPException(502, str(e) or "無法確認電視狀態") from e


@app.get("/api/cast/session")
def get_cast_session(uuid: str = ""):
    return cast_session.get(uuid or chromecast.selected_uuid())


@app.post("/api/cast/session/control")
def control_cast_session(body: CastSessionControlIn):
    try:
        return cast_session.control(**body.model_dump())
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/cast/device")
def configure_cast_device(body: CastDeviceConfigIn):
    try:
        return known_devices.configure(body.uuid, body.mac)
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/tv/pair")
def tv_pair(body: TvPairIn):
    code = body.code
    if not tv_session.valid_code(code):
        raise HTTPException(403, "配對碼不正確")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("cinema_tv", tv_session.cookie_token(), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return resp


@app.post("/api/tv/rotate")
def tv_rotate():
    return {"tv_code": tv_session.rotate_code()}


@app.get("/api/img")
def image_proxy(u: str = Query(..., max_length=500)):
    try:
        url = assert_image_url(unquote(u))
        host = (urlparse(url).hostname or "").lower()
        if "picbf" in host or "wangwangzyimg" in host or "hongguoapp" in host:
            referer = "https://www.hongguoapp.cn/"
            imp = "chrome131"
        elif host.endswith("1777cdn.com") or host.endswith("gimyai.tw"):
            referer = "https://gimyai.tw/"
            imp = "chrome131"
        elif host.endswith("dramasq.io"):
            referer = "https://dramasq.io/"
            imp = "chrome131"
        elif "chinaq" in host:
            referer = "https://chinaq.fun/"
            imp = "chrome131"
        else:
            referer = f"https://{host}/"
            imp = "chrome131"
        r = http_client.fetch_bytes(
            url, referer=referer, allowed_hosts=IMAGE_HOSTS, timeout=20, impersonate=imp
        )
        try:
            data = r.content
        finally:
            http_client.close_response(r)
        if len(data) > 8_000_000:
            raise UnsafeURL("image too large")
        ctype = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
        if not ctype.startswith("image/"):
            ctype = "image/jpeg"
        return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})
    except Exception as e:
        raise _err(e) from e


@app.api_route("/api/hls", methods=["GET", "HEAD"])
def hls_proxy_ep(request: Request, u: str = Query(..., max_length=4000)):
    try:
        return hls_proxy.serve_media(request, u)
    except Exception as e:
        raise _err(e) from e


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/")
def index():
    index_file = DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return HTMLResponse("介面尚未建置。請用 start.bat 啟動（會自動 npm run build）。", status_code=503)


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("api"):
        raise HTTPException(404)
    if ".." in full_path or full_path.startswith("\\"):
        raise HTTPException(400)
    index_file = DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    raise HTTPException(404)
