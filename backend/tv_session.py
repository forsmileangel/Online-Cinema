from __future__ import annotations

import hashlib
import hmac
import secrets

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


def valid_code(raw: str) -> bool:
    got = "".join((raw or "").upper().split())
    return hmac.compare_digest(got, ensure_code())
