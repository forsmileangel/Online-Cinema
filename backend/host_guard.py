"""Only requests addressed to this PC reach the app.

Two checks, both outside the pairing guard:

- The Host header must be one of our own names: loopback, this PC's LAN addresses, its host name, or
  CINEMA_HOSTS. A page that rebinds its own domain to 127.0.0.1 sends its domain as Host and gets 400.
- A state-changing request (POST, PUT, DELETE) a browser sends from a page on another origin gets 403.
  Browsers put the page's Origin on every such request; non-browser callers (TV Remote, curl) send none
  and are not affected. Media and listing requests are GETs and stay as they are.
"""
from __future__ import annotations

import os
import socket
import threading
import time

from starlette.responses import JSONResponse

from . import lan
from . import settings as S

LOCAL = ("127.0.0.1", "localhost", "::1")
DEV_PORT = 5173  # Vite's dev server, allowed only with CINEMA_DEV=1
TTL = 60.0

_lock = threading.Lock()
_names: frozenset[str] = frozenset()
_checked = 0.0
_refresh: threading.Thread | None = None


def dev_mode() -> bool:
    return os.environ.get("CINEMA_DEV") == "1"


def _extra() -> list[str]:
    return [h.strip().lower() for h in os.environ.get("CINEMA_HOSTS", "").split(",") if h.strip()]


def _lookup() -> frozenset[str]:
    """Loopback, this PC's LAN addresses and host name, plus CINEMA_HOSTS. Slow: the address lookup can take seconds."""
    names = set(LOCAL) | set(_extra()) | set(lan.ipv4_lan())
    try:
        name = socket.gethostname().strip().lower()
    except OSError:
        name = ""
    if name:
        names.update((name, f"{name}.local"))
    return frozenset(names)


def refresh() -> frozenset[str]:
    """Look the names up now. Called at start-up, so no request ever waits for the lookup."""
    global _names, _checked, _refresh
    names = _lookup()
    with _lock:
        _names, _checked, _refresh = names, time.monotonic(), None
    return names


def allowed_hosts() -> frozenset[str]:
    """The known names; after a minute they are looked up again in the background (the request path is the event loop)."""
    global _refresh
    with _lock:
        names = _names
        stale = time.monotonic() - _checked >= TTL
        if names and stale and _refresh is None:
            _refresh = threading.Thread(target=refresh, name="host-names", daemon=True)
            _refresh.start()
    return names or refresh()


def join_refresh() -> None:
    """Wait for a background lookup, if one is running (tests)."""
    with _lock:
        thread = _refresh
    if thread:
        thread.join(5)


def reset() -> None:
    global _names, _checked, _refresh
    join_refresh()
    with _lock:
        _names, _checked, _refresh = frozenset(), 0.0, None


def split_host(value: str) -> tuple[str, int | None]:
    """'192.168.1.2:6969' -> ('192.168.1.2', 6969); '[::1]:6969' -> ('::1', 6969); 'localhost' -> ('localhost', None)."""
    value = value.strip().lower()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    else:
        host, sep, port = value.rpartition(":")
        if not sep or not port.isdigit():
            return value, None
    return host, (int(port) if port.isdigit() else None)


def host_ok(header: str) -> bool:
    """An empty Host (old DLNA renderers) passes: a rebinding attack always carries the attacker's name."""
    if not header:
        return True
    host, port = split_host(header)
    return host in allowed_hosts() and port in (None, S.PORT)


def origin_ok(origin: str) -> bool:
    """A page served by this app, or by the Vite dev server when CINEMA_DEV=1."""
    scheme, sep, rest = origin.strip().lower().partition("://")
    if scheme != "http" or not sep:
        return False
    host, port = split_host(rest)
    if host not in allowed_hosts():
        return False
    return port == S.PORT or (dev_mode() and port == DEV_PORT)


def allowed_origins() -> list[str]:
    """What CORSMiddleware lets read responses: the app's own loopback origins, plus Vite's in dev mode."""
    ports = [S.PORT] + ([DEV_PORT] if dev_mode() else [])
    return [f"http://{host}:{port}" for port in ports for host in ("127.0.0.1", "localhost")]


class HostGuard:
    """Pure ASGI middleware; sits outside the other middleware so a bad Host never reaches them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            host = headers.get(b"host", b"").decode("latin-1")
            if not host_ok(host):
                response = JSONResponse({"detail": "拒絕"}, status_code=400)
                return await response(scope, receive, send)
            if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
                origin = headers.get(b"origin", b"").decode("latin-1")
                site = headers.get(b"sec-fetch-site", b"").decode("latin-1").lower()
                if (origin and not origin_ok(origin)) or site == "cross-site":
                    response = JSONResponse({"detail": "拒絕"}, status_code=403)
                    return await response(scope, receive, send)
        await self.app(scope, receive, send)
