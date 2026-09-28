"""Media casting with receiver confirmation, as used by Amberbox."""

from __future__ import annotations

import math
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


def _reusable_cast(device) -> bool:
    client = device.socket_client
    return not client.is_stopped and (client.ident is None or client.is_alive())


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
            old = _casts.get(uuid)
            if old is not None and _reusable_cast(old):
                # Discovery objects have not started their socket thread.
                # Chromecast.disconnect() joins that thread and raises here.
                cast.socket_client.disconnect()
                cast = old
            else:
                if old is not None:
                    old.socket_client.disconnect()
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
                 "host": _host(d), "kind": "dlna" if isinstance(d, dlna.Renderer) else "chromecast",
                 "model": "" if isinstance(d, dlna.Renderer) else getattr(d.cast_info, "model_name", "")}
                for uuid, d in found.items()]
        from . import cast_devices
        return cast_devices.remember(devices)


def selected_uuid() -> str:
    return db.get_setting("cast_uuid", "")


def select(uuid: str) -> None:
    with _lock:
        from . import cast_devices
        if not cast_devices.available(uuid):
            raise RuntimeError("請先勾選「我在高雄」再選擇高雄電視")
        if uuid not in _casts and not cast_devices.known(uuid):
            raise RuntimeError("找不到這台電視，請再掃描一次")
    db.set_setting("cast_uuid", uuid)


def _host(device) -> str:
    return device.host if isinstance(device, dlna.Renderer) else device.cast_info.host


def _cast(uuid: str = "", deadline: float | None = None):
    from pychromecast.error import NotConnected, PyChromecastStopped, RequestTimeout

    uuid = uuid or selected_uuid()
    if not uuid:
        raise RuntimeError("請先選一台電視")
    deadline = deadline if deadline is not None else time.monotonic() + 20
    if not _lock.acquire(timeout=_remaining(deadline, "等待裝置搜尋")):
        raise TimeoutError("裝置搜尋尚未結束，請稍後重試")
    try:
        for attempt in range(2):
            cast = _casts.get(uuid)
            if cast is None or (not isinstance(cast, dlna.Renderer) and not _reusable_cast(cast)):
                discover(timeout=_remaining(deadline, "搜尋", 5))
                cast = _casts.get(uuid)
            if cast is None:
                raise ReceiverUnavailable("無法連到已選裝置，請確認電源與 Wi-Fi 後重試")
            if isinstance(cast, dlna.Renderer):
                return uuid, cast
            try:
                if not _reusable_cast(cast):
                    raise ReceiverUnavailable("投放連線已中斷")
                cast.wait(timeout=_remaining(deadline, "連線", 8))
                # wait() can return an old status even after the socket dies.
                # Receiver GET_STATUS also works in standby, without a media app.
                received = threading.Event()
                result = []
                def ready(ok, _data, result=result, received=received):
                    result.append(ok)
                    received.set()
                cast.socket_client.receiver_controller.update_status(callback_function=ready)
                if not received.wait(_remaining(deadline, "確認連線", 4)) or not result[0]:
                    raise ReceiverUnavailable("未收到投放裝置回應")
                return uuid, cast
            except (NotConnected, PyChromecastStopped, RequestTimeout, ReceiverUnavailable) as e:
                cast.socket_client.disconnect()
                _casts.pop(uuid, None)
                browser = _browsers.pop(uuid, None)
                if browser is not None and browser not in _browsers.values():
                    browser.stop_discovery()
                if attempt:
                    raise ReceiverUnavailable("投放裝置重新連線失敗，請確認電源與 Wi-Fi 後重試") from e
    finally:
        _lock.release()


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
        return {**cast.status(deadline=deadline), "can_set_volume": False, "can_mute": False}
    st = cast.media_controller.status
    phase = st.player_state
    return {
        "uuid": uuid, "playing": phase == "PLAYING", "paused": phase == "PAUSED",
        "idle": phase == "IDLE", "buffering": phase == "BUFFERING",
        "content_id": st.content_id or "", "idle_reason": st.idle_reason or "",
        "current_time": float(st.current_time or 0), "duration": float(st.duration or 0),
        "title": st.title or "",
        **_volume_status(cast),
    }


def _volume_status(cast) -> dict:
    receiver = cast.status
    device_volume = getattr(receiver, "volume_control_type", "") in {"master", "attenuation"}
    source = receiver if device_volume else cast.media_controller.status
    level = getattr(source, "volume_level", None)
    muted = getattr(source, "volume_muted", None)
    valid_level = type(level) in (int, float) and math.isfinite(level) and 0 <= level <= 1
    return {
        "volume_level": float(level) if valid_level else None,
        "volume_muted": muted if isinstance(muted, bool) else None,
        "can_set_volume": valid_level and (device_volume or getattr(source, "supports_stream_volume", False) is True),
        "can_mute": isinstance(muted, bool) and (device_volume or getattr(source, "supports_stream_mute", False) is True),
        "volume_scope": "device" if device_volume else "stream",
    }


def _set_volume(uuid, device, state, expected, deadline, *, level=None, muted=None) -> dict:
    if state["idle"] or state["content_id"] != expected:
        raise RuntimeError("影片已停止或切換，請重新確認投放")
    if (level is not None and not state.get("can_set_volume")) or (muted is not None and not state.get("can_mute")):
        raise ValueError("這台裝置目前不支援網頁音量控制，請使用遙控器")
    # Moving the slider also unmutes, without changing the remembered level
    # when the user only presses mute.
    if level is not None and state.get("can_mute"):
        muted = False
    if state["volume_scope"] == "device":
        if level is not None:
            device.set_volume(level, timeout=_remaining(deadline, "調整音量", 5))
        if muted is not None:
            device.set_volume_muted(muted, timeout=_remaining(deadline, "切換靜音", 5))
    else:
        volume = {}
        if level is not None:
            volume["level"] = level
        if muted is not None:
            volume["muted"] = muted
        received, result = threading.Event(), []
        def acknowledged(ok, _data):
            result.append(ok)
            received.set()
        device.media_controller.send_message({"type": "SET_VOLUME", "mediaSessionId": device.media_controller.status.media_session_id,
                                              "volume": volume}, inc_session_id=True, callback_function=acknowledged)
        if not received.wait(_remaining(deadline, "調整音量", 5)) or not result[0]:
            raise ReceiverUnavailable("未收到投放音量調整回應")
    while True:
        # Read receiver and media volume back; an accepted command is not yet
        # proof of the actual audible level, especially on fixed-volume TVs.
        _, device = _cast(uuid, deadline)
        current = _available_status(uuid, device, deadline)
        if current["idle"] or current["content_id"] != expected:
            raise RuntimeError("影片已停止或切換，請重新確認投放")
        if (level is None or (current.get("volume_level") is not None and abs(current["volume_level"] - level) < .005)) and (muted is None or current.get("volume_muted") is muted):
            return current
        time.sleep(_remaining(deadline, "確認音量", .1))


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
        nesthub = not isinstance(cast, dlna.Renderer) and any(
            name in str(cast.cast_info.model_name).lower() for name in ("nest hub", "google home hub"))
        if isinstance(cast, dlna.Renderer) and content_type == "application/vnd.apple.mpegurl":
            content_id = hls_proxy.dlna_media_url(content_id, deadline)
        elif nesthub and content_type == "application/vnd.apple.mpegurl":
            content_id = hls_proxy.nesthub_media_url(content_id, deadline)
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
        if nesthub and parse_qs(urlparse(content_id).query).get("nesthub") == ["1"]:
            state["warning"] = "Nest Hub 已使用 720p 相容模式播放"
        if isinstance(cast, dlna.Renderer) and position > 0:
            try:
                cast.control("seek", position, deadline=deadline)
                state = _confirm(uuid, cast, content_id, "seek", position, deadline=deadline)
            except RuntimeError:
                # Some LG firmware accepts playback but refuses REL_TIME seek.
                state = _confirm(uuid, cast, content_id, "play", deadline=deadline)
                state["warning"] = "電視已播放，但此串流無法跳到原進度，將從目前位置繼續。"
        return state


def control(action: str, position: float | None = None, uuid: str = "", content_id: str = "", *, guard=None,
            volume_level: float | None = None, muted: bool | None = None) -> dict:
    if action not in {"pause", "resume", "play", "seek", "stop", "volume", "mute"}:
        raise ValueError("不支援的電視指令")
    if action == "volume" and (type(volume_level) not in (int, float) or not math.isfinite(volume_level) or not 0 <= volume_level <= 1):
        raise ValueError("音量必須介於 0 與 1")
    if action == "mute" and not isinstance(muted, bool):
        raise ValueError("請指定是否靜音")
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
        if action in {"volume", "mute"}:
            return _set_volume(uuid, cast, state, expected, deadline,
                               level=volume_level if action == "volume" else None,
                               muted=muted if action == "mute" else None)
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


def reconnect(uuid: str, content_id: str, session_id: str, title: str = "") -> dict:
    marker = parse_qs(urlparse(content_id).query).get("cast_session", [""])[0]
    if not session_id or not marker.startswith(session_id + "-"):
        raise ValueError("無法確認原本的投放，請重新投放")
    deadline = time.monotonic() + CONFIRM_TIMEOUT
    with _operation(deadline):
        uuid, device = _cast(uuid, deadline)
        state = _available_status(uuid, device, deadline)
        if state["idle"] or state["content_id"] != content_id or _active.get(uuid, content_id) != content_id:
            raise RuntimeError("電視已停止或切換其他內容，請重新投放")
        if isinstance(device, dlna.Renderer):
            device.title = title
        _active[uuid] = content_id
        return {**state, "title": title}


def session_content(uuid: str, session_id: str) -> str:
    content = _active.get(uuid, "")
    marker = parse_qs(urlparse(content).query).get("cast_session", [""])[0]
    return content if marker.startswith(session_id + "-") else ""
