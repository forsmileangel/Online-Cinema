from __future__ import annotations

import ipaddress
import socket


def _ok_lan(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(addr.is_private and not addr.is_loopback and not addr.is_link_local)


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
