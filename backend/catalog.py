from __future__ import annotations

DEFAULT_SOURCE = "hongguo"

SOURCE_LABELS = {
    "hongguo": "紅果短劇",
    "chinaq": "中國人線上看",
    "gimy": "Gimy 劇迷",
    "dramaq": "DramaQ",
}
SOURCES = tuple(SOURCE_LABELS)


def normalize_source(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    return s if s in SOURCE_LABELS else DEFAULT_SOURCE


def current_source() -> str:
    from . import db

    return normalize_source(db.get_setting("source", DEFAULT_SOURCE))
