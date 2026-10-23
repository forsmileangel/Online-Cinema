from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from . import db

_ALPH = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def ensure_code() -> str:
    c = db.get_setting("tv_code", "")
    if len(c) == 6:
        return c
    return rotate_code()


def _secret() -> str:
    s = db.get_setting("tv_secret", "")
    if not s:
        s = secrets.token_hex(16)
        db.set_setting("tv_secret", s)
    return s


def rotate_code() -> str:
    c = "".join(secrets.choice(_ALPH) for _ in range(6))
    db.set_setting("tv_code", c)
    _secret()
    return c


def cookie_token() -> str:
    return hmac.new(_secret().encode(), ensure_code().encode(), hashlib.sha256).hexdigest()[:20]


def valid_cookie(value: str | None) -> bool:
    if not value:
        return False
    return hmac.compare_digest(value, cookie_token())


_attempt_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
ATTEMPT_LIMIT = 5
ATTEMPT_WINDOW = 60.0


def retry_after(client: str) -> int:
    """Seconds a client must wait after too many wrong codes (0 = may try)."""
    now = time.monotonic()
    with _attempt_lock:
        recent = [t for t in _failures.get(client, []) if now - t < ATTEMPT_WINDOW]
        _failures[client] = recent
        if len(recent) < ATTEMPT_LIMIT:
            return 0
        return max(1, int(ATTEMPT_WINDOW - (now - recent[0])) + 1)


def record_failure(client: str) -> None:
    with _attempt_lock:
        _failures.setdefault(client, []).append(time.monotonic())
        if len(_failures) > 256:
            _failures.pop(next(iter(_failures)))


def clear_failures(client: str) -> None:
    with _attempt_lock:
        _failures.pop(client, None)


def valid_code(raw: str) -> bool:
    got = "".join((raw or "").upper().split())
    return hmac.compare_digest(got, ensure_code())
