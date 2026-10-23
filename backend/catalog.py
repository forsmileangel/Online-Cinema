from __future__ import annotations

DEFAULT_SOURCE = "hongguo"

SOURCE_LABELS = {
    "mvffm": "MVFFM 電影線上看",
    "anigamer": "巴哈姆特動畫瘋",
    "hongguo": "紅果短劇",
    "chinaq": "中國人線上看",
    "gimy": "Gimy 劇迷",
    "mmov": "MMOV 電影線上看",
    "dramaq": "DramaQ",
}
SOURCES = tuple(SOURCE_LABELS)

# Groups order the source picker and define 「同類來源」 for multi-source search.
GROUP_ORDER = ("影劇", "短劇", "動畫")
SOURCE_GROUPS = {
    "chinaq": "影劇", "gimy": "影劇", "mmov": "影劇", "mvffm": "影劇", "dramaq": "影劇",
    "hongguo": "短劇",
    "anigamer": "動畫",
}


def normalize_source(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    return s if s in SOURCE_LABELS else DEFAULT_SOURCE


def current_source() -> str:
    from . import db

    return normalize_source(db.get_setting("source", DEFAULT_SOURCE))
