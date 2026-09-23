"""LG / DLNA media renderer discovery and AVTransport control."""

from __future__ import annotations

import select
import logging
import socket
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape

from . import lan

AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_log = logging.getLogger(__name__)


class ActionError(RuntimeError):
    def __init__(self, code: str, description: str, action: str = "", stage: str = ""):
        self.code = code
        self.description = description
        self.action = action
        self.stage = stage
        context = "／".join(part for part in (stage, action) if part)
        super().__init__(f"LG 電視拒絕操作{f'（{context}，代碼 {code}）' if context else f'（代碼 {code}）'}：{description}")


def remaining(deadline: float | None, stage: str, limit: float = 30) -> float:
    left = limit if deadline is None else deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError(f"LG {stage}逾時，電視尚未確認操作，請重試或停止投放")
    return min(left, limit)


def _wait(deadline: float, stage: str) -> None:
    time.sleep(remaining(deadline, stage, 0.25))


def _local_url(url: str, host: str) -> str:
    p = urlparse(url)
    if p.scheme != "http" or p.hostname != host or p.username or p.password:
        raise ValueError("電視回傳了無效的區網位址")
    return url


def _xml(request: str | urllib.request.Request, timeout: float = 4) -> ET.Element:
    try:
        with _http.open(request, timeout=timeout) as r:
            raw = r.read(1_000_001)
    except urllib.error.HTTPError as e:
        raw = e.read(65536)
        try:
            fault = ET.fromstring(raw)
            detail = fault.findtext(".//{*}errorDescription")
            code = fault.findtext(".//{*}errorCode", "")
        except ET.ParseError:
            detail, code = None, ""
        raise ActionError(code or str(e.code), detail or str(e.code)) from e
    if len(raw) > 1_000_000:
        raise ValueError("電視回應過大")
    return ET.fromstring(raw)


def _seconds(raw: str) -> float:
    try:
        h, m, s = raw.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, AttributeError):
        return 0


@dataclass
class Renderer:
    uuid: str
    name: str
    host: str
    control_url: str
    title: str = ""

    def command(self, action: str, *, deadline: float | None = None, stage: str = "", **args) -> ET.Element:
        body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in {"InstanceID": 0, **args}.items())
        envelope = (f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                    f'<s:Body><u:{action} xmlns:u="{AV_TRANSPORT}">{body}</u:{action}></s:Body></s:Envelope>')
        try:
            return _xml(urllib.request.Request(self.control_url, data=envelope.encode(), headers={
                "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{AV_TRANSPORT}#{action}"',
            }), timeout=remaining(deadline, stage or action, 15 if action in ("SetAVTransportURI", "Play") else 4))
        except ActionError as e:
            _log.warning("LG rejected operation: stage=%s action=%s code=%s description=%s", stage, action, e.code, e.description)
            raise ActionError(e.code, e.description, action, stage) from e
        except (OSError, ET.ParseError) as e:
            raise RuntimeError(f"LG {stage or '讀取電視'}失敗（{action}）：{e}") from e

    def _transport(self, deadline: float, stage: str) -> str:
        while True:
            remaining(deadline, stage)
            try:
                return self.command("GetTransportInfo", deadline=deadline, stage=stage).findtext(".//{*}CurrentTransportState", "")
            except ActionError as e:
                if e.code != "701":
                    raise
                _wait(deadline, stage)

    def play(self, url: str, mime: str, title: str, *, deadline: float | None = None) -> None:
        deadline = deadline if deadline is not None else time.monotonic() + 30
        features = "DLNA.ORG_OP=01;DLNA.ORG_CI=0" if mime == "video/mp4" else "*"
        # LG rejects long UTF-8 titles with Play 501 and can truncate metadata
        # mid-character. Keep its display label short; retain the full title
        # below for the browser's status and history.
        device_title = title.encode("utf-8")[:64].decode("utf-8", "ignore")
        metadata = (f'<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                    f'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                    f'<item id="0" parentID="-1" restricted="1"><dc:title>{escape(device_title)}</dc:title>'
                    f'<upnp:class>object.item.videoItem</upnp:class>'
                    f'<res protocolInfo="http-get:*:{mime}:{features}">{escape(url)}</res></item></DIDL-Lite>')
        stage = "停止舊片"
        state = self._transport(deadline, stage)
        while state not in ("STOPPED", "NO_MEDIA_PRESENT"):
            if state in ("PLAYING", "PAUSED_PLAYBACK", "LG_TRANSITIONING"):
                # LG leaves this vendor state after a failed seek. A Stop
                # cancels it; waiting for it to settle can otherwise time out.
                try:
                    self.command("Stop", deadline=deadline, stage=stage)
                except ActionError as e:
                    if e.code != "701":
                        raise
            _wait(deadline, stage)
            state = self._transport(deadline, stage)
        stage = "設定新片"
        while True:
            try:
                self.command("SetAVTransportURI", CurrentURI=url, CurrentURIMetaData=metadata, deadline=deadline, stage=stage)
                break
            except ActionError as e:
                if e.code != "701":
                    raise
                _wait(deadline, stage)
                while self._transport(deadline, stage) not in ("STOPPED", "NO_MEDIA_PRESENT"):
                    _wait(deadline, stage)
        # LG can open its player asynchronously and auto-start after URI setup.
        # Sending Play during TRANSITIONING produces AVTransport error 701.
        stage = "啟動播放"
        while True:
            state = self._transport(deadline, stage)
            if state == "PLAYING":
                break
            if state in ("STOPPED", "PAUSED_PLAYBACK"):
                try:
                    self.command("Play", Speed=1, deadline=deadline, stage=stage)
                    break
                except ActionError as e:
                    if e.code != "701":
                        raise
            _wait(deadline, stage)
        self.title = title

    def control(self, action: str, position: float = 0, *, deadline: float | None = None) -> None:
        if action == "seek":
            seconds = int(position)
            self.command("Seek", Unit="REL_TIME", Target=f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}", deadline=deadline, stage="跳轉進度")
        elif action in ("resume", "play"):
            self.command("Play", Speed=1, deadline=deadline, stage="繼續播放")
        else:
            self.command({"pause": "Pause", "stop": "Stop"}[action], deadline=deadline, stage="暫停播放" if action == "pause" else "停止播放")

    def status(self, *, deadline: float | None = None) -> dict:
        transport = self.command("GetTransportInfo", deadline=deadline, stage="確認播放")
        position = self.command("GetPositionInfo", deadline=deadline, stage="確認播放進度")
        state = transport.findtext(".//{*}CurrentTransportState", "")
        return {
            "uuid": self.uuid, "playing": state == "PLAYING", "paused": state == "PAUSED_PLAYBACK",
            "idle": state in ("STOPPED", "NO_MEDIA_PRESENT"), "buffering": state in ("TRANSITIONING", "LG_TRANSITIONING"),
            "content_id": position.findtext(".//{*}TrackURI", ""), "title": self.title,
            "current_time": _seconds(position.findtext(".//{*}RelTime", "")),
            "duration": _seconds(position.findtext(".//{*}TrackDuration", "")),
            "idle_reason": "ERROR" if transport.findtext(".//{*}CurrentTransportStatus") == "ERROR_OCCURRED" else "",
        }


def describe(location: str, host: str) -> Renderer | None:
    root = _xml(_local_url(location, host))
    device = root.find("{*}device")
    if device is None:
        return None
    for service in device.findall("{*}serviceList/{*}service"):
        if service.findtext("{*}serviceType") == AV_TRANSPORT:
            control = service.findtext("{*}controlURL", "")
            identity = device.findtext("{*}UDN", "")
            if not control or not identity:
                return None
            return Renderer("dlna:" + identity.removeprefix("uuid:"),
                            device.findtext("{*}friendlyName", host), host,
                            _local_url(urljoin(location, control), host))
    return None


def discover(timeout: float = 4) -> list[Renderer]:
    message = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
               'MX: 2\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n').encode()
    sockets = []
    locations = {}
    try:
        for ip in lan.ipv4_lan():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind((ip, 0))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                s.sendto(message, ("239.255.255.250", 1900))
                sockets.append(s)
            except OSError:
                s.close()
        deadline = time.monotonic() + timeout
        while sockets and time.monotonic() < deadline:
            readable, _, _ = select.select(sockets, [], [], max(0, deadline - time.monotonic()))
            for s in readable:
                data, (host, _) = s.recvfrom(65535)
                if not lan.is_allowed_client(host) or lan.is_loopback(host):
                    continue
                for line in data.decode("utf-8", "replace").splitlines():
                    if line.lower().startswith("location:"):
                        locations[line.split(":", 1)[1].strip()] = host
    finally:
        for s in sockets:
            s.close()
    found = {}
    for location, host in locations.items():
        try:
            device = describe(location, host)
            if device:
                found[device.uuid] = device
        except (OSError, ValueError, RuntimeError, ET.ParseError):
            continue
    return list(found.values())
