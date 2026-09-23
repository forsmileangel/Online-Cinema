"""Known receivers survive standby. Wake is an explicit user action only."""
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
            saved[device["uuid"]] = {**device, "mac": mac}
        db.set_setting("known_cast_devices", json.dumps(saved, ensure_ascii=False))
        return [{**d, "online": key in online, "can_wake": bool(d.get("mac"))} for key, d in saved.items()]


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
    raise TimeoutError("電視未回應喚醒；請確認 LG 已開啟「透過 Wi-Fi 開啟電視／行動裝置開啟電視」，且待機時仍連接網路")
