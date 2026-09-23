from __future__ import annotations

import threading
from typing import Any

from curl_cffi import requests

from . import settings as S
from .security import UnsafeURL, final_url_still_allowed

_lock = threading.Lock()
_req_lock = threading.Lock()
_session: requests.Session | None = None
_impersonate = S.IMPERSONATE
_tls = threading.local()


def session() -> requests.Session:
    global _session
    with _lock:
        if _session is None:
            _session = requests.Session(impersonate=_impersonate)
        return _session


def media_session(impersonate: str | None = None) -> requests.Session:
    key = impersonate or _impersonate
    store = getattr(_tls, "by_imp", None)
    if store is None:
        store = {}
        _tls.by_imp = store
    sess = store.get(key)
    if sess is None:
        sess = requests.Session(impersonate=key)
        store[key] = sess
    return sess


def reset_session(impersonate: str | None = None) -> None:
    global _session, _impersonate
    with _lock:
        if impersonate:
            _impersonate = impersonate
        _session = requests.Session(impersonate=_impersonate)
    _tls.media = None


def _accept_language() -> str:
    return "zh-TW,zh-Hant;q=0.9,zh;q=0.8,ja;q=0.5"


def _headers(referer: str | None = None) -> dict[str, str]:
    h = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": _accept_language(),
    }
    if referer:
        h["Referer"] = referer
        p = referer.split("/", 3)
        if len(p) >= 3:
            h["Origin"] = f"{p[0]}//{p[2]}"
    return h


def fetch_html_hosts(
    url: str,
    allowed_hosts: set[str],
    timeout: int = 25,
    impersonate: str | None = None,
    referer: str | None = None,
) -> str:
    headers = _headers(referer)
    if impersonate:
        sess = media_session(impersonate)
        r = sess.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    else:
        with _req_lock:
            r = session().get(url, headers=headers, timeout=timeout, allow_redirects=True)
    final_url_still_allowed(str(r.url), allowed_hosts)
    if r.status_code == 403:
        raise UnsafeURL("blocked by remote")
    r.raise_for_status()
    text = r.text or ""
    if len(text) > 6_000_000:
        raise UnsafeURL("response too large")
    return text


def fetch_bytes(
    url: str,
    *,
    referer: str,
    timeout: int = 30,
    stream: bool = False,
    allowed_hosts: set[str],
    impersonate: str | None = None,
    method: str = "GET",
    range_header: str | None = None,
) -> Any:
    headers = {
        "Accept": "*/*",
        "Referer": referer,
        "Origin": referer.split("/", 3)[0] + "//" + referer.split("/", 3)[2] if referer.count("/") >= 2 else referer,
    }
    sess = media_session(impersonate)
    if range_header:
        headers["Range"] = range_header
    r = sess.request(
        method,
        url,
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
        stream=stream,
    )
    try:
        final_url_still_allowed(str(r.url), allowed_hosts)
        if r.status_code >= 400 and not (range_header and r.status_code == 416):
            r.raise_for_status()
    except Exception:
        close_response(r)
        raise
    return r


def close_response(r: Any) -> None:
    try:
        r.close()
    except Exception:
        pass
