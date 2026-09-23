"""Media casting with receiver confirmation, as used by Amberbox."""

from __future__ import annotations

import socket
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import wraps
from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

from . import db, dlna, hls_proxy, lan
from .settings import PORT

_lock = threading.RLock()
_operations = threading.Lock()
_browsers: dict[str, object] = {}
_casts: dict[str, object] = {}
_active: dict[str, str] = {}
CONFIRM_TIMEOUT = 30


class ReceiverUnavailable(RuntimeError):
    pass


def _remaining(deadline: float, stage: str, limit: float = CONFIRM_TIMEOUT) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError(f"電視{stage}逾時，尚未確認操作，請重試或停止投放")
    return min(left, limit)


@contextmanager
def _operation(deadline: float):
    if not _operations.acquire(timeout=_remaining(deadline, "等待其他操作")):
        raise TimeoutError("電視正在處理其他操作，請稍後重試")
    try:
        yield
    finally:
        _operations.release()


def _cc():
    import pychromecast
    return pychromecast


def _serialize_cast_writes(device) -> None:
    # PyChromecast also sends heartbeats and channel messages from its socket
    # thread. The API operation lock alone cannot protect these SSL writes.
    client = device.socket_client
    if "_cinema_send_lock" in vars(client):
        return
    send = client.send_message
    lock = threading.RLock()

    @wraps(send)
    def serialized(*args, **kwargs):
        with lock:
            return send(*args, **kwargs)

    client._cinema_send_lock = lock
    client.send_message = serialized


def discover(timeout: float = 6) -> list[dict]:
    global _browsers, _casts
    with _lock, ThreadPoolExecutor(max_workers=2) as pool:
        renderers = pool.submit(dlna.discover, min(timeout, 4))
        # get_listed_chromecasts requires an explicit list; no filter finds NOTHING.
        chromecasts, browser = _cc().get_chromecasts(timeout=timeout, tries=1, retry_wait=1)
        previous_browsers = set(_browsers.values())
        found = {}
        for cast in chromecasts:
            uuid = str(cast.uuid)
            if uuid in _casts:
                # Discovery objects have not started their socket thread.
                # Chromecast.disconnect() joins that thread and raises here.
                cast.socket_client.disconnect()
                cast = _casts[uuid]
            else:
                _browsers[uuid] = browser
            _serialize_cast_writes(cast)
            found[uuid] = cast
        for renderer in renderers.result():
            found[renderer.uuid] = renderer
        # Do not disconnect the active receiver when rescanning the device menu.
        for uuid, cast in _casts.items():
            if uuid not in found and uuid not in _active and not isinstance(cast, dlna.Renderer):
                cast.socket_client.disconnect()
        _casts = {**{k: v for k, v in _casts.items() if k in _active}, **found}
        # stop_discovery also closes Zeroconf. A retained Chromecast still needs
        # its original browser for DNS resolution when its socket reconnects.
        _browsers = {k: v for k, v in _browsers.items() if k in _casts}
        for unused in (previous_browsers | {browser}) - set(_browsers.values()):
            unused.stop_discovery()
        devices = [{"uuid": uuid, "name": d.name if isinstance(d, dlna.Renderer) else d.cast_info.friendly_name,
                 "host": _host(d), "kind": "dlna" if isinstance(d, dlna.Renderer) else "chromecast"}
                for uuid, d in found.items()]
        from . import cast_devices
        return cast_devices.remember(devices)


def selected_uuid() -> str:
    return db.get_setting("cast_uuid", "")


def select(uuid: str) -> None:
    with _lock:
        from . import cast_devices
        if uuid not in _casts and not cast_devices.known(uuid):
            raise RuntimeError("找不到這台電視，請再掃描一次")
    db.set_setting("cast_uuid", uuid)


def _host(device) -> str:
    return device.host if isinstance(device, dlna.Renderer) else device.cast_info.host


def _cast(uuid: str = "", deadline: float | None = None):
    uuid = uuid or selected_uuid()
    if not uuid:
        raise RuntimeError("請先選一台電視")
    acquired = _lock.acquire() if deadline is None else _lock.acquire(timeout=_remaining(deadline, "等待裝置搜尋"))
    if not acquired:
        raise TimeoutError("裝置搜尋尚未結束，請稍後重試")
    try:
        cast = _casts.get(uuid)
    finally:
        _lock.release()
    if cast is None and deadline is not None:
        raise RuntimeError("找不到這台電視，請重新掃描後投放")
    if cast is None:
        discover(timeout=5)
        with _lock:
            cast = _casts.get(uuid)
    if cast is None:
        raise RuntimeError("找不到已選的電視，請開啟電視後再掃描")
    if not isinstance(cast, dlna.Renderer):
        cast.wait(timeout=8 if deadline is None else _remaining(deadline, "連線", 8))
    return uuid, cast


def lan_media_origin(uuid: str = "") -> str:
    if uuid:
        _, device = _cast(uuid)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # Ask the routing table which interface reaches THIS receiver.
            sock.connect((_host(device), 9))
            ip = sock.getsockname()[0]
        if not lan.is_allowed_client(ip) or lan.is_loopback(ip):
            raise RuntimeError("找不到可連到電視的區網位址")
    else:
        ips = lan.ipv4_lan()
        if not ips:
            raise RuntimeError("找不到區網位址，請確認 Wi-Fi／網路線")
        ip = ips[0]
    return f"http://{ip}:{PORT}"


def check_media_origin(origin: str) -> None:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(origin + "/api/health", timeout=3) as r:
            if r.status != 200:
                raise OSError("unavailable")
    except OSError as e:
        raise RuntimeError("電視無法使用目前的串流位址。請確認已開啟區網投放，關閉舊程式後重開 start.bat，並放行 TCP 6970。") from e


def _status(uuid: str, cast, deadline: float | None = None) -> dict:
    if isinstance(cast, dlna.Renderer):
        return cast.status(deadline=deadline)
    st = cast.media_controller.status
    phase = st.player_state
    return {
        "uuid": uuid, "playing": phase == "PLAYING", "paused": phase == "PAUSED",
        "idle": phase == "IDLE", "buffering": phase == "BUFFERING",
        "content_id": st.content_id or "", "idle_reason": st.idle_reason or "",
        "current_time": float(st.current_time or 0), "duration": float(st.duration or 0),
        "title": st.title or "",
    }


def _fresh_status(uuid: str, cast, deadline: float | None = None) -> dict:
    if not isinstance(cast, dlna.Renderer):
        from pychromecast.error import NotConnected
        event = threading.Event()
        result = []
        def received(ok, _data):
            result.append(ok)
            event.set()
        try:
            cast.media_controller.update_status(callback_function=received)
        except NotConnected as e:
            raise ReceiverUnavailable("電視正在重新連線，尚未確認狀態") from e
        if not event.wait(4 if deadline is None else _remaining(deadline, "確認狀態", 4)) or not result[0]:
            raise ReceiverUnavailable("未收到電視狀態，請檢查連線後重試")
    return _status(uuid, cast, deadline)


def _available_status(uuid: str, cast, deadline: float) -> dict:
    while True:
        _remaining(deadline, "重新連線")
        try:
            return _fresh_status(uuid, cast, deadline)
        except ReceiverUnavailable:
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def _confirm(uuid: str, cast, content_id: str, action: str, position: float = 0,
             *, deadline: float | None = None, require_progress: bool = False) -> dict:
    end = deadline if deadline is not None else time.monotonic() + CONFIRM_TIMEOUT
    first_position = None
    last_state = None
    while time.monotonic() < end:
        try:
            state = _fresh_status(uuid, cast, end)
        except ReceiverUnavailable:
            # A cold Default Media Receiver has no media namespace yet.
            time.sleep(min(0.25, max(0, end - time.monotonic())))
            continue
        except dlna.ActionError as e:
            if e.code != "701":
                raise
            time.sleep(min(0.25, max(0, end - time.monotonic())))
            continue
        last_state = state
        if time.monotonic() >= end:
            break
        same = state["content_id"] == content_id
        if same and state["idle_reason"] == "ERROR":
            raise RuntimeError("電視無法播放此串流，請檢查來源／影片格式")
        if action == "stop":
            confirmed = state["idle"]
        elif action == "pause":
            confirmed = same and state["paused"]
        elif action == "seek":
            confirmed = same and (state["playing"] or state["paused"]) and abs(state["current_time"] - position) < 5
        else:
            confirmed = same and state["playing"]
        if require_progress:
            if confirmed:
                if first_position is None:
                    first_position = state["current_time"]
                confirmed = state["current_time"] > first_position + 0.25
            else:
                first_position = None
        if confirmed:
            return state
        time.sleep(min(0.25, max(0, end - time.monotonic())))
    detail = ""
    if last_state:
        phase = "PLAYING" if last_state["playing"] else "PAUSED" if last_state["paused"] else "BUFFERING" if last_state["buffering"] else "STOPPED"
        detail = f"（最後狀態 {phase}，進度 {last_state['current_time']:.1f} 秒）"
    if not isinstance(cast, dlna.Renderer) and getattr(getattr(cast, "status", None), "is_active_input", None) is False:
        detail += "；電視尚未切到 Chromecast，請確認 SIMPLINK／HDMI-CEC 已開啟"
    raise TimeoutError(f"電視播放／控制確認逾時{detail}，請重試或停止投放")


def play(content_id: str, content_type: str, title: str, position: float = 0, uuid: str = "", *, guard=None, marker="", expected="") -> dict:
    deadline = time.monotonic() + CONFIRM_TIMEOUT
    with _operation(deadline):
        uuid, cast = _cast(uuid, deadline)
        if isinstance(cast, dlna.Renderer) and content_type == "application/vnd.apple.mpegurl":
            content_id = hls_proxy.dlna_media_url(content_id, deadline)
        if guard and not guard():
            raise RuntimeError("投放已取消")
        if expected:
            current = _available_status(uuid, cast, deadline)
            if current["content_id"] not in ("", expected) or (not current["idle"] and current["content_id"] != expected):
                raise RuntimeError("電視已切換其他內容，已停止連播")
        if marker:
            parsed = urlparse(content_id)
            query = parse_qs(parsed.query)
            query["cast_session"] = [marker]
            content_id = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        if not isinstance(cast, dlna.Renderer) and not expected:
            # LOAD alone may reuse a background receiver without selecting its
            # HDMI input. An explicit cast must issue LAUNCH to trigger CEC.
            from pychromecast.config import APP_MEDIA_RECEIVER
            cast.start_app(APP_MEDIA_RECEIVER, force_launch=True,
                           timeout=_remaining(deadline, "啟動 Chromecast", 10))
            if guard and not guard():
                raise RuntimeError("投放已取消")
        _active[uuid] = content_id
        if isinstance(cast, dlna.Renderer):
            cast.play(content_id, content_type, title, deadline=deadline)
        else:
            cast.media_controller.play_media(content_id, content_type, title=title or "線上電影院",
                                              current_time=float(position), autoplay=True, stream_type="BUFFERED")
        state = _confirm(uuid, cast, content_id, "play", deadline=deadline, require_progress=True)
        if isinstance(cast, dlna.Renderer) and position > 0:
            try:
                cast.control("seek", position, deadline=deadline)
                state = _confirm(uuid, cast, content_id, "seek", position, deadline=deadline)
            except RuntimeError:
                # Some LG firmware accepts playback but refuses REL_TIME seek.
                state = _confirm(uuid, cast, content_id, "play", deadline=deadline)
                state["warning"] = "電視已播放，但此串流無法跳到原進度，將從目前位置繼續。"
        return state


def control(action: str, position: float | None = None, uuid: str = "", content_id: str = "", *, guard=None) -> dict:
    if action not in {"pause", "resume", "play", "seek", "stop"}:
        raise ValueError("不支援的電視指令")
    deadline = time.monotonic() + CONFIRM_TIMEOUT
    with _operation(deadline):
        uuid, cast = _cast(uuid, deadline)
        expected = content_id or _active.get(uuid, "")
        state = _available_status(uuid, cast, deadline)
        if not expected or expected != _active.get(uuid) or (not state["idle"] and state["content_id"] != expected):
            raise RuntimeError("電視已切換其他影片，請重新投放")
        if action == "stop" and state["idle"]:
            _active.pop(uuid, None)
            return state
        if action == "seek" and (position is None or position < 0 or not state["duration"] or position > state["duration"]):
            raise ValueError("跳轉位置超出影片範圍")
        if guard and not guard():
            raise RuntimeError("投放已取消")
        was_paused = state["paused"]
        # Leave time to restore pause even if the seek itself times out.
        command_deadline = deadline - min(5, _remaining(deadline, "跳轉進度") / 2) if action == "seek" and was_paused else deadline
        failure = None
        try:
            if isinstance(cast, dlna.Renderer):
                # This LG rejects Seek while paused. Restore pause after seeking,
                # including when Seek fails, so it never unexpectedly keeps playing.
                if action == "seek" and was_paused:
                    cast.control("resume", deadline=command_deadline)
                    _confirm(uuid, cast, expected, "play", deadline=command_deadline)
                cast.control(action, position or 0, deadline=command_deadline)
            else:
                mc = cast.media_controller
                if action == "seek":
                    mc.seek(position, timeout=_remaining(command_deadline, "跳轉進度", 10))
                else:
                    getattr(mc, {"resume": "play"}.get(action, action))(timeout=_remaining(command_deadline, "控制", 10))
            state = _confirm(uuid, cast, expected, action, position or 0, deadline=command_deadline)
        except Exception as e:
            failure = e
            raise
        finally:
            if action == "seek" and was_paused:
                try:
                    if isinstance(cast, dlna.Renderer):
                        cast.control("pause", deadline=deadline)
                    else:
                        cast.media_controller.pause(timeout=_remaining(deadline, "恢復暫停", 10))
                    state = _confirm(uuid, cast, expected, "pause", deadline=deadline)
                except Exception as e:
                    if failure:
                        raise RuntimeError(f"{failure}；恢復暫停也未確認：{e}") from failure
                    raise
        if action == "stop":
            _active.pop(uuid, None)
        return state


def status(uuid: str = "") -> dict:
    # FastAPI runs sync requests on multiple threads. A cancelled browser poll
    # can still be running here when a control request starts. Serializing both
    # prevents concurrent writes to the same Chromecast SSL socket.
    deadline = time.monotonic() + 12
    with _operation(deadline):
        uuid, cast = _cast(uuid, deadline)
        return _available_status(uuid, cast, deadline)


def session_content(uuid: str, session_id: str) -> str:
    content = _active.get(uuid, "")
    marker = parse_qs(urlparse(content).query).get("cast_session", [""])[0]
    return content if marker.startswith(session_id + "-") else ""
