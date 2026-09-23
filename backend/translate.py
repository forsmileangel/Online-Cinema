"""Japanese → Traditional Chinese. Cached locally; no browser right-click."""

from __future__ import annotations

import re
from urllib.parse import urlencode, urlparse

from curl_cffi import requests

from . import db
from . import settings as S
from .security import UnsafeURL

_KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_HAN = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]{3,}")
_MAX = 2000


def looks_japanese(text: str) -> bool:
    if not text:
        return False
    return len(_KANA.findall(text)) >= 2


def looks_han(text: str) -> bool:
    if not text:
        return False
    return len(_HAN.findall(text)) >= 3


def _gtx(text: str, sl: str = "ja") -> str:
    q = text.strip()[:_MAX]
    if not q:
        return ""
    params = urlencode({"client": "gtx", "sl": sl, "tl": "zh-TW", "dt": "t", "q": q})
    url = "https://translate.googleapis.com/translate_a/single?" + params
    host = (urlparse(url).hostname or "").lower()
    if host != "translate.googleapis.com":
        raise UnsafeURL("translate host not allowed")
    last = None
    r = None
    for profile in ("chrome131", "chrome124", S.IMPERSONATE):
        try:
            r = requests.get(url, impersonate=profile, timeout=12)
            if r.status_code == 200:
                break
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = e
            r = None
    if r is None or r.status_code != 200:
        raise ValueError(last or "translate failed")
    data = r.json()
    parts: list[str] = []
    if isinstance(data, list) and data and isinstance(data[0], list):
        for item in data[0]:
            if isinstance(item, list) and item and isinstance(item[0], str):
                parts.append(item[0])
    out = "".join(parts).strip()
    if not out:
        raise ValueError("empty translation")
    return out


def to_zh_tw(text: str, *, force: bool = False) -> str:
    src = (text or "").strip()
    if not src:
        return ""
    if not force and looks_han(src) and not looks_japanese(src):
        return src
    if not force and not looks_japanese(src) and not _LATIN.search(src):
        return src
    hit = db.get_translation(src)
    if hit and hit != src:
        return hit
    sl = "ja" if looks_japanese(src) else "auto"
    try:
        dst = _gtx(src, sl)
    except Exception:
        return src
    if dst and dst != src:
        db.put_translation(src, dst)
        return dst
    return src
