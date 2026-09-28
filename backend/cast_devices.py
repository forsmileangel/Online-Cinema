"""Known receivers survive standby; only DLNA TVs use Wake-on-LAN."""
from __future__ import annotations

import ctypes
import ipaddress
import json
import re
import socket
import sys
import threading
import time

from . import db, lan

_lock = threading.RLock()


def records():
    try:
        value = json.loads(db.get_setting("known_cast_devices", "{}"))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def known(uuid):
    return records().get(uuid)


def available(uuid):
    device = known(uuid) or {}
    return device.get("location") != "kaohsiung" or db.get_setting("in_kaohsiung", "") == "1"


def normalize_mac(value):
    raw = re.sub(r"[:-]", "", value.strip()).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", raw) or int(raw[:2], 16) & 1 or raw == "0" * 12:
        raise ValueError("請輸入有效的電視 MAC 位址，例如 10:20:30:40:50:60")
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def learn_mac(host):
    # Native API avoids creating an arp.exe / PowerShell console window.
    if sys.platform != "win32" or not lan.is_allowed_client(host) or lan.is_loopback(host):
        return ""
    try:
        address = int.from_bytes(socket.inet_aton(host), "little")
        buffer = (ctypes.c_ubyte * 6)()
        length = ctypes.c_ulong(6)
        if ctypes.windll.iphlpapi.SendARP(address, 0, buffer, ctypes.byref(length)) == 0 and length.value == 6:
            return normalize_mac(bytes(buffer).hex())
    except (ValueError, OSError, AttributeError):
        pass
    return ""


def remember(devices):
    with _lock:
        saved = records()
        online = {d["uuid"] for d in devices}
        for device in devices:
            old = saved.get(device["uuid"], {})
            mac = old.get("mac", "") or learn_mac(device["host"])
            saved[device["uuid"]] = {**old, **device, "mac": mac}
        db.set_setting("known_cast_devices", json.dumps(saved, ensure_ascii=False))
        return [{**d, "online": key in online, "can_wake": d.get("kind") == "dlna" and bool(d.get("mac")) and not d.get("manual_power_on", False)} for key, d in saved.items()]


def configure(uuid, mac):
    with _lock:
        saved = records()
        if uuid not in saved:
            raise ValueError("請先開啟電視並掃描一次")
        saved[uuid]["mac"] = normalize_mac(mac) if mac.strip() else ""
        db.set_setting("known_cast_devices", json.dumps(saved, ensure_ascii=False))
        return saved[uuid]


def magic_packet(mac):
    return b"\xff" * 6 + bytes.fromhex(normalize_mac(mac).replace(":", "")) * 16


def wake_and_wait(uuid, valid, timeout=60):
    from . import cast
    device = known(uuid)
    if device and device.get("manual_power_on"):
        raise ValueError("這台電視需手動開機。請先用遙控器開機，再按「掃描電視」後投放。")
    if device and device.get("kind") == "chromecast":
        # Legacy clients may still send wake=true. Cast LOAD launches the
        # receiver (and HDMI-CEC); sending a MAC magic packet cannot do that.
        if not valid():
            raise RuntimeError("喚醒已取消")
        return
    if not device or not device.get("mac"):
        raise ValueError("尚未記住電視 MAC 位址，請先開機掃描或輸入 MAC")
    host = device["host"]
    if not lan.is_allowed_client(host) or lan.is_loopback(host):
        raise ValueError("電視必須位於同一區網")
    packet = magic_packet(device["mac"])
    import ifaddr
    broadcasts = {"255.255.255.255"}
    for adapter in ifaddr.get_adapters():
        for address in adapter.ips:
            if isinstance(address.ip, str):
                network = ipaddress.ip_network(f"{address.ip}/{address.network_prefix}", strict=False)
                if ipaddress.ip_address(host) in network:
                    broadcasts.add(str(network.broadcast_address))
    if not valid():
        raise RuntimeError("喚醒已取消")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for address in broadcasts:
            sock.sendto(packet, (address, 9))
    deadline = time.monotonic() + timeout
    while valid() and time.monotonic() < deadline:
        devices = cast.discover(timeout=min(4, max(0.1, deadline - time.monotonic())))
        if any(d["uuid"] == uuid and d.get("online") for d in devices):
            return
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    if not valid():
        raise RuntimeError("喚醒已取消")
    raise TimeoutError("已送出網路喚醒訊號，但電視未回應。請先用遙控器開機，再按「掃描電視」後投放；記住 MAC 不代表機型支援喚醒，請確認電視是否提供並已開啟網路待機設定。")
