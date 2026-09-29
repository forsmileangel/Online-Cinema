from __future__ import annotations

import ipaddress
import socket
import json
import os
import subprocess
import threading
import time

_download_lock = threading.Lock()
_download_ips: list[str] = []
_download_checked = 0.0


def _ok_lan(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(addr.version == 4 and addr.is_private and not addr.is_loopback
                and not addr.is_link_local and not addr.is_unspecified and not addr.is_multicast)


def ipv4_lan() -> list[str]:
    found: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if _ok_lan(ip) and ip not in found:
                found.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if _ok_lan(ip) and ip not in found:
            found.insert(0, ip)
    except Exception:
        pass
    found.sort(key=lambda x: (0 if x.startswith("192.168.") else 1 if x.startswith("10.") else 2, x))
    return found


def port_open(ip: str, port: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=.4):
            return True
    except OSError:
        return False


def download_interfaces() -> list[str]:
    """Current default-route IPv4 addresses, with the OS-selected route first."""
    global _download_ips, _download_checked
    with _download_lock:
        now = time.monotonic()
        if now - _download_checked < 30:
            return list(_download_ips)
        ips = []
        try:
            if os.name == "nt":
                script = ("$ErrorActionPreference='Stop'; "
                          "$routes=Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4; "
                          "Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred | "
                          "Where-Object { $_.InterfaceIndex -in $routes.InterfaceIndex } | "
                          "Select-Object -ExpandProperty IPAddress | ConvertTo-Json -Compress")
                result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                        capture_output=True, timeout=4, check=True,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                found = json.loads(result.stdout.decode("utf-8-sig"))
                ips = [ip for ip in (found if isinstance(found, list) else [found]) if isinstance(ip, str) and _ok_lan(ip)]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect(("8.8.8.8", 80))
                primary = route.getsockname()[0]
            # Only switch when the default path is one of these LAN adapters.
            if primary in ips:
                ips = [primary] + [ip for ip in ips if ip != primary]
            else:
                ips = []
        except (OSError, ValueError, subprocess.SubprocessError):
            ips = []
        _download_ips, _download_checked = list(dict.fromkeys(ips)), time.monotonic()
        return list(_download_ips)


def is_allowed_client(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(addr.is_loopback or addr.is_private)


def is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return ip in {"127.0.0.1", "::1"}
