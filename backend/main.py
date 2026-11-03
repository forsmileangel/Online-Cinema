from __future__ import annotations

import json
import logging
import subprocess
from concurrent.futures import TimeoutError as FuturesTimeout, as_completed
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.concurrency import run_in_threadpool

from . import catalog
from . import cast as chromecast
from . import cast_session
from . import cast_devices as known_devices
from . import db
from . import lan
from . import tv_session
from . import alternatives, display, home_fill, next_buffer, offline, sorting, terms
from . import hls_proxy
from . import host_guard
from . import http_client
from . import img_cache
from . import pools
from . import settings as S
from . import sites
from . import translate as translate_mod
from . import web_serving
from .models import (
    FavoriteIn,
    PrefetchIn,
    OfflineIn,
    OfflineDeleteIn,
    OfflineCleanupIn,
    HistoryIn,
    HomePayload,
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
    HostUnresolvable,
    IMAGE_HOSTS,
    SiteBusy,
    SourceUnavailable,
    UnsafeURL,
    assert_hls_url,
    assert_image_url,
    safe_video_id,
)

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "dist"
logger = logging.getLogger(__name__)


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
            if path.startswith("/assets") or path.startswith("/api/hls") or path.startswith("/api/img") or path.startswith("/api/offline/media/"):
                return await call_next(request)
            if path in ("/api/tv/pair", "/api/tv/status", "/api/health", "/favicon.ico"):
                return await call_next(request)
            if not tv_session.valid_cookie(request.cookies.get("cinema_tv")) and path.startswith("/api/"):
                return JSONResponse({"detail": "請先用配對碼連電視"}, status_code=401)
        return await call_next(request)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    host_guard.refresh()  # This PC's names, before any request is judged by them.
    offline._start_maintenance()
    # The health check runs on the event loop: the commit is read and the downloads counted before it is asked.
    await run_in_threadpool(_version)
    offline.active_count()
    yield


app = FastAPI(title="Online Cinema", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(web_serving.Compressed)  # Innermost: only what the routes answer is compressed.
app.add_middleware(AccessGuard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=host_guard.allowed_origins(),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def media_cors(request: Request, call_next):
    # Receiver apps run on Google/LG origins. Only the media endpoint is public
    # across origins; the existing LAN gate still protects every media request.
    if request.url.path != "/api/hls" and not request.url.path.startswith("/api/offline/media/"):
        return await call_next(request)
    if request.method == "OPTIONS":
        client = request.client.host if request.client else "127.0.0.1"
        if not lan.is_allowed_client(client) or (not lan.is_loopback(client) and db.get_setting("lan_tv", "") != "1"):
            return Response(status_code=403)
        response = Response(status_code=204)
        # Cast receivers request private-network access before fetching media.
        if request.headers.get("access-control-request-private-network") == "true":
            response.headers["Access-Control-Allow-Private-Network"] = "true"
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
        seek = "01" if request.url.path.startswith("/api/offline/media/") or urlparse(media_url).path.lower().endswith(".mp4") else "00"
        response.headers["transferMode.dlna.org"] = "Streaming"
        response.headers["contentFeatures.dlna.org"] = f"DLNA.ORG_OP={seek};DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
    return response


# Outermost: a request for another host name, or a cross-site POST, is refused before anything else runs.
app.add_middleware(host_guard.HostGuard)


def _err(exc: Exception, status: int = 400) -> HTTPException:
    if isinstance(exc, SourceUnavailable):
        return HTTPException(404, str(exc))
    if isinstance(exc, TimeoutError):
        return HTTPException(504, "來源載入逾時，請重試")
    if isinstance(exc, SiteBusy):
        return HTTPException(exc.status_code or 503, str(exc), headers={"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None)
    if isinstance(exc, HostUnresolvable):
        return HTTPException(502, "來源網域暫時查不到，請稍後重試")
    if isinstance(exc, UnsafeURL):
        return HTTPException(status, "請求被拒絕")
    # Anything else is unexpected: keep the traceback, the client only sees the generic message.
    logger.error("unexpected %s: %s", type(exc).__name__, exc, exc_info=exc)
    return HTTPException(502, "來源站暫時無法使用")


_version_cache: str | None = None


def _version() -> str:
    """The commit this server runs, read once; empty when git or the repository is not there."""
    global _version_cache
    if _version_cache is None:
        try:
            out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                 timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _version_cache = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            _version_cache = ""
    return _version_cache


async def busy_state(fresh: bool = False) -> dict:
    """What a restart would interrupt: TVs being played to and downloads under way (fresh: counted now)."""
    jobs = await run_in_threadpool(offline.active_count, True) if fresh else offline.active_count()
    return {"casts": cast_session.active_count(), "offline_jobs": jobs}


@app.get("/api/offline")
def offline_list(source: str | None = None, video_id: str | None = None):
    return {"directory": str(offline.ROOT), "items": display.rows(None, offline.listing(catalog.SOURCES, source, video_id)),
            "cleanup": offline.cleanup_policy(), "free_bytes": offline.free_bytes()}


@app.post("/api/offline")
def offline_download(body: OfflineIn):
    if body.source not in catalog.SOURCES:
        raise HTTPException(400, "不支援的來源")
    try:
        return offline.enqueue(body.source, safe_video_id(body.video_id), body.episode, body.height, restart=body.restart,
                               title=body.title, episode_title=body.episode_title)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/offline/cleanup")
def offline_cleanup(body: OfflineCleanupIn):
    try:
        return offline.cleanup_policy(body.enabled)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/offline/playback")
def offline_playback(body: OfflineIn):
    if body.source not in catalog.SOURCES:
        raise HTTPException(400, "不支援的來源")
    try:
        offline.playback(body.source, safe_video_id(body.video_id), body.episode)
        return {"ok": True}
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/offline/delete")
def offline_delete(body: OfflineDeleteIn):
    return offline.delete(body.ids, catalog.SOURCES)


@app.post("/api/offline/{key}/cancel")
def offline_cancel(key: str):
    try:
        item = offline.status(key)
        if not item or item['source'] not in catalog.SOURCES:
            raise ValueError("找不到下載項目")
        offline.cancel(key)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.api_route("/api/offline/media/{key}.mp4", methods=["GET", "HEAD"])
def offline_media(key: str, request: Request):
    try:
        path = offline.media_path(key)
        item = offline.status(key)
        if item['source'] not in catalog.SOURCES:
            raise FileNotFoundError()
        if request.method == "GET":
            offline.touch_played(key)
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-cache"})
    except (OSError, ValueError) as exc:
        raise HTTPException(404, "找不到完整的本地影片") from exc


@app.get("/api/health")
async def health(fresh: bool = False):
    # A liveness check answered on the event loop from what is already known: a busy disk or a full thread
    # pool must never make a healthy server look hung to the supervisor. deploy.ps1 asks with ?fresh=1.
    return {"ok": True, "version": _version(), "busy": await busy_state(fresh)}


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
        return display.home(src, HomePayload(
            continue_watching=cont[:20],
            rows=home_fill.fill(src, site, home_fill.rows(site, rows_raw), picks or []),
            mirror="",
            source=src,
            picks=picks or [],
            error=home_error,
        ))
    except Exception as e:
        raise _err(e, 502) from e


@app.get("/api/browse/{kind}")
def browse(
    kind: str,
    page: int = Query(1, ge=1, le=1000),
    source: str | None = None,
    sort: Annotated[str, Query(max_length=24)] = "",
):
    src = _src(source)
    try:
        return display.listing(src, sorting.browse(sites.get(src), kind, None, page, sort))
    except Exception as e:
        raise _err(e) from e


@app.get("/api/browse/{kind}/{slug}")
def browse_slug(
    kind: str,
    slug: str,
    page: int = Query(1, ge=1, le=1000),
    source: str | None = None,
    sort: Annotated[str, Query(max_length=24)] = "",
):
    src = _src(source)
    try:
        return display.listing(src, sorting.browse(sites.get(src), kind, slug, page, sort))
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
        return display.listing(src, terms.search(sites.get(src), q, page))
    except Exception as e:
        raise _err(e) from e


MULTI_SEARCH_SECONDS = 35


def _multi_search_lines(q: str, sources: list[str]):
    """One NDJSON line per source, in the order the searches finish."""

    def one(src: str) -> dict:
        try:
            return {"source": src, "ok": True, "listing": display.listing(src, terms.search(sites.get(src), q, 1)).model_dump(mode="json")}
        except Exception as exc:
            return {"source": src, "ok": False, "error": _err(exc).detail}

    def line(result: dict) -> str:
        return json.dumps(result, ensure_ascii=False) + "\n"

    futures = {pools.fanout.submit(one, src): src for src in sources}
    sent = set()
    try:
        for future in as_completed(futures, timeout=MULTI_SEARCH_SECONDS):
            sent.add(future)
            yield line(future.result())
    except FuturesTimeout:
        for future, src in futures.items():
            if future not in sent:
                yield line(future.result() if future.done() else {"source": src, "ok": False, "error": "搜尋逾時，請稍後再試"})
    finally:
        # A slow site keeps its worker until its own request timeout; queued ones never start.
        pools.cancel_pending(futures)


@app.get("/api/search/multi")
def search_multi(q: str = Query(..., min_length=1, max_length=50), sources: str = Query(..., max_length=400)):
    known = {s["id"] for s in sites.available()}
    wanted = [s for s in dict.fromkeys(sources.split(",")) if s in known][:20]
    if not wanted:
        raise HTTPException(422, "請選擇要搜尋的來源")
    return StreamingResponse(_multi_search_lines(q, wanted), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store"})


@app.get("/api/alternatives")
def other_sources(source: str = Query(..., max_length=40), video_id: str = Query(..., max_length=200),
                  title: str = Query(..., min_length=1, max_length=200)):
    """Same show on the other sources of its group, for episodes this source lacks."""
    if source not in catalog.SOURCES:
        raise HTTPException(400, "不支援的來源")
    return {"items": alternatives.find(source, safe_video_id(video_id), title)}


@app.post("/api/anigamer/prepare/{video_id}")
def prepare_anigamer(video_id: str, ep: str | None = Query(None, max_length=16)):
    try:
        hist = db.get_history_item(video_id, "anigamer")
        selected = ep or (hist.get("episode_id") if hist else None)
        return sites.get("anigamer").prepare(video_id, selected)
    except Exception as e:
        raise _err(e, 502) from e


@app.post("/api/anigamer/ad/{key}/{event}")
def anigamer_ad_event(key: str, event: str):
    if event not in {"start", "complete", "cancel"} or len(key) > 64:
        raise HTTPException(400, "invalid preparation")
    try:
        return sites.get("anigamer").ad_event(key, event)
    except Exception as e:
        raise _err(e, 502) from e


@app.get("/api/video/{source}/{video_id}")
def video_on_source(source: str, video_id: str, ep: str | None = Query(None, max_length=16)):
    src = catalog.normalize_source(source)
    try:
        video_id = safe_video_id(video_id)
        hist = db.get_history_item(video_id, src)
        selected_ep = ep or (hist.get("episode_id") if hist else None)
        detail = offline.fetch_detail(src, video_id, selected_ep)
        detail.source = src
        detail.favorited = db.is_favorite(video_id, src)
        if detail.episodes and not detail.resolved_episode_id:
            detail.resolved_episode_id = next((e.id for e in detail.episodes if e.playlist == detail.playlist), None)
        if hist:
            detail.episode_id = hist.get("episode_id")
            if not detail.episodes or (detail.episode_id and detail.episode_id == detail.resolved_episode_id):
                detail.position_sec = float(hist["position_sec"] or 0)
        return display.video(src, detail)
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
        out_names = pools.map_in(pools.fanout, translate_mod.to_zh_tw, names) if names else []
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
    return {"items": display.rows(None, items)}


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
def history(limit: int = Query(40, ge=1, le=1000), source: str | None = None):
    return {"items": display.rows(None, db.list_history(limit=limit, source=source))}


@app.put("/api/history")
def put_history(body: HistoryIn):
    try:
        db.upsert_history(
            body.id, body.title, body.cover, body.position_sec, body.duration_sec, source=body.source, episode_id=body.episode_id
        )
        return {"ok": True}
    except Exception as e:
        raise _err(e) from e


@app.delete("/api/history/{video_id}")
def del_history(video_id: str, source: str = "hongguo"):
    try:
        db.remove_history(video_id, source=source)
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
        in_kaohsiung=db.get_setting("in_kaohsiung", "") == "1",
        lan_ips=ips,
        tv_code=code if lan_on else "",
        tv_url=tv_url,
        sources=sites.available(),
    )


@app.get("/api/connection")
def get_connection():
    enabled = db.get_setting("lan_tv", "") == "1"
    code = tv_session.ensure_code() if enabled else ""
    addresses = []
    for ip in lan.ipv4_lan():
        url = f"http://{ip}:{S.PORT}"
        addresses.append({"ip": ip, "url": url,
                          "connect_url": f"{url}/connect?c={code}" if enabled else "",
                          "listening": enabled and lan.port_open(ip, S.PORT)})
    return JSONResponse({"enabled": enabled, "port": S.PORT, "addresses": addresses},
                        headers={"Cache-Control": "no-store"})


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
        if body.in_kaohsiung is not None:
            db.set_setting("in_kaohsiung", "1" if body.in_kaohsiung else "0")
        return get_settings()
    except Exception as e:
        raise _err(e) from e


@app.get("/api/cast/devices")
def cast_devices():
    try:
        devices = chromecast.discover()
        in_kaohsiung = db.get_setting("in_kaohsiung", "") == "1"
        devices = [d for d in devices if d.get("location") != "kaohsiung" or in_kaohsiung]
        selected = chromecast.selected_uuid()
        return {"devices": devices, "selected": selected if known_devices.available(selected) else "", "origin": chromecast.lan_media_origin(),
                "in_kaohsiung": in_kaohsiung}
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
    if not known_devices.available(body.uuid or chromecast.selected_uuid()):
        raise HTTPException(400, "請先勾選「我在高雄」再投放到高雄電視")
    if body.managed:
        try:
            uuid = body.uuid or chromecast.selected_uuid()
            chromecast.select(uuid)
            return JSONResponse(cast_session.start({**body.model_dump(), "uuid": uuid}), status_code=202)
        except Exception as e:
            raise HTTPException(400, str(e)) from e
    try:
        local_key = offline.media_key(body.url) if body.url.startswith("/api/offline/media/") else None
    except (OSError, ValueError) as exc:
        raise HTTPException(400, "本地影片已移除，請重新選片") from exc
    parsed = urlparse(body.url)
    urls = parse_qs(parsed.query).get("u", [])
    if not local_key and (parsed.scheme or parsed.netloc or parsed.path != "/api/hls" or len(urls) != 1):
        raise HTTPException(400, "請從影片頁選擇可播放的串流")
    try:
        upstream = assert_hls_url(urls[0]) if not local_key else ""
        uuid = body.uuid or chromecast.selected_uuid()
        if not uuid:
            raise HTTPException(400, "請先選一台電視")
        origin = chromecast.lan_media_origin(uuid)
        chromecast.check_media_origin(origin)
        url = origin + offline.media_url(local_key) if local_key else hls_proxy.proxied_media(upstream, origin)
        mime = "video/mp4" if local_key or urlparse(upstream).path.lower().endswith(".mp4") else "application/vnd.apple.mpegurl"
        cast_session.cancel(uuid)
        return chromecast.play(url, mime, body.title, body.position_sec, uuid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, str(e) or "投放失敗") from e


@app.post("/api/cast/control")
def cast_control(body: CastControlIn):
    try:
        return chromecast.control(body.action, body.position_sec, body.uuid, body.content_id,
                                  volume_level=body.volume_level, muted=body.muted)
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


@app.get("/api/tv/status")
def tv_status(request: Request):
    """Whether this device may use the app; lets a LAN device ask for the code instead of failing."""
    client = request.client.host if request.client else "127.0.0.1"
    return {"paired": lan.is_loopback(client) or tv_session.valid_cookie(request.cookies.get("cinema_tv"))}


@app.post("/api/tv/pair")
def tv_pair(body: TvPairIn, request: Request):
    client = request.client.host if request.client else "127.0.0.1"
    wait = tv_session.retry_after(client)
    if wait:
        raise HTTPException(429, f"配對碼輸入錯誤次數過多，請 {wait} 秒後再試", headers={"Retry-After": str(wait)})
    code = body.code
    if not tv_session.valid_code(code):
        tv_session.record_failure(client)
        raise HTTPException(403, "配對碼不正確")
    tv_session.clear_failures(client)
    resp = JSONResponse({"ok": True})
    resp.set_cookie("cinema_tv", tv_session.cookie_token(), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return resp


@app.post("/api/tv/rotate")
def tv_rotate():
    return {"tv_code": tv_session.rotate_code()}


def _detect_image_media_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and any(brand in data[8:32] for brand in (b"avif", b"avis")):
        return "image/avif"
    if data.startswith(b"BM"):
        return "image/bmp"
    return ""


def fetch_image(raw: str) -> Response:
    """One cover straight from its site, with the referer that site expects."""
    url = assert_image_url(raw)
    host = (urlparse(url).hostname or "").lower()
    if host in {"img.mmov.app", "image.mmov.app"}:
        referer = "https://hk.mmov.app/"
    elif sites.hongguo.is_cover_host(host):
        referer = "https://www.hongguoapp.cn/"
    elif host.endswith("1777cdn.com") or host.endswith("gimyai.tw"):
        referer = "https://gimyai.tw/"
    elif host.endswith("dramasq.io"):
        referer = "https://dramasq.io/"
    elif "chinaq" in host:
        referer = "https://chinaq.fun/"
    else:
        referer = f"https://{host}/"
    r = http_client.fetch_bytes(url, referer=referer, allowed_hosts=IMAGE_HOSTS, timeout=20, impersonate="chrome131")
    try:
        data = r.content
    finally:
        http_client.close_response(r)
    if len(data) > 8_000_000:
        raise UnsafeURL("image too large")
    ctype = _detect_image_media_type(data)
    if not ctype:
        raise ValueError("upstream response is not a supported image")
    return Response(content=data, media_type=ctype)


@app.get("/api/img")
async def image_proxy(request: Request, u: str = Query(..., max_length=500)):
    # Served from the image pool and a disk cache: a listing's covers never take the media threads.
    try:
        return await img_cache.respond(request, unquote(u), fetch_image)
    except Exception as e:
        raise _err(e) from e


@app.get("/api/playback/720p")
def web_720p(u: str = Query(..., max_length=4000)):
    import time
    try:
        original = hls_proxy.proxied_media(u)
        url = hls_proxy.nesthub_media_url(original, time.monotonic() + 25, strict=True)
        return {"url": url + "&web=1" if parse_qs(urlparse(url).query).get("nesthub") == ["1"] else url}
    except (UnsafeURL, SiteBusy, TimeoutError) as e:
        raise _err(e) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        raise _err(e) from e


@app.post("/api/playback/prefetch")
def prefetch_next_episode(body: PrefetchIn):
    try:
        return next_buffer.update(body.owner, body.url, body.ready, body.height, body.retry)
    except Exception as e:
        raise _err(e) from e


@app.get("/api/playback/prefetch/{owner}")
def prefetch_status(owner: str):
    return next_buffer.status(owner)


@app.delete("/api/playback/prefetch/{owner}")
def prefetch_cancel(owner: str):
    next_buffer.cancel(owner)
    return {"ok": True}


@app.api_route("/api/hls", methods=["GET", "HEAD"])
def hls_proxy_ep(request: Request, u: str = Query(..., max_length=4000)):
    try:
        return hls_proxy.serve_media(request, u)
    except Exception as e:
        raise _err(e) from e


if (DIST / "assets").is_dir():
    app.mount("/assets", web_serving.ImmutableAssets(directory=DIST / "assets"), name="assets")


@app.get("/")
def index():
    index_file = DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return HTMLResponse("介面尚未建置。請用 start.bat 啟動（會自動 npm run build）。", status_code=503)


@app.get("/favicon.ico")
def favicon():
    # Browsers ask for this by habit; the SVG answers it (they go by content type, not the name).
    icon = DIST / "favicon.svg"
    if icon.is_file():
        return FileResponse(icon, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(404)


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("api"):
        raise HTTPException(404)
    if ".." in full_path or full_path.startswith("\\"):
        raise HTTPException(400)
    # Icons and the manifest live in the build root next to index.html.
    if full_path and "/" not in full_path:
        asset = (DIST / full_path).resolve()
        if asset.is_file() and asset.parent == DIST.resolve() and asset.name != "index.html":
            return FileResponse(asset, headers={"Cache-Control": "public, max-age=86400"})
    index_file = DIST / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    raise HTTPException(404)
