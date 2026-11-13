"""New mechanisms on trial beside the ones they may replace.

Every switch is off until it is turned on in the settings page, and off means the earlier code
path, unchanged. A trial that fails hands the request to the earlier path and is counted, so the
settings page shows whether it is ready to become the default.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from . import db
from .catalog import SOURCES
from .security import UnsafeURL

T = TypeVar("T")
logger = logging.getLogger(__name__)

# id: (sources it changes, name on the settings page, what it does)
TRIALS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "huangguo_api": (
        ("huangguo",), "黃果短劇：清單改用網站的資料介面",
        "首頁、分類、搜尋、排行和選集直接向網站要資料，不再解析網頁。失敗時自動用原本的方式。",
    ),
    "hongguo_hosts": (
        ("hongguo",), "紅果短劇：接受新的影片主機",
        "網站換了影片主機時自動接受，不再顯示找不到影片。",
    ),
    "line_memory": (
        ("gimy", "chinaq", "dramaq"), "影劇：記住上次能播的線路",
        "同一部劇換集時先試上次能播的線路，不用每次從頭試。",
    ),
    "missav_remirror": (
        ("missav",), "MissAV：連不上時自動換鏡像",
        "目前的鏡像連不上時自動改用其他鏡像，不用手動重新探測。",
    ),
}

_KEY = "trial_"
_COUNTS_KEY = "trial_counts"
_SAVE_EVERY = 60.0
_lock = threading.Lock()
_armed = False
_counts: dict[str, dict[str, int]] = {}
_saved_at = 0.0
_unsaved = False


def arm() -> None:
    """Start reading the switches (server start-up). Until then every trial is off, so tests and
    scripts that never ask for one run the earlier paths and never open the database for it."""
    global _armed, _saved_at
    try:
        saved = json.loads(db.get_setting(_COUNTS_KEY, "{}"))
    except (ValueError, TypeError):
        saved = {}
    with _lock:
        _counts.clear()
        for name in TRIALS:
            row = saved.get(name) if isinstance(saved, dict) else None
            row = row if isinstance(row, dict) else {}
            _counts[name] = {field: int(row[field]) if isinstance(row.get(field), int) else 0
                             for field in ("used", "fallbacks")}
        _saved_at = time.monotonic()
        _armed = True


def disarm() -> None:
    global _armed, _unsaved
    with _lock:
        _armed = False
        _unsaved = False
        _counts.clear()


def offered() -> list[str]:
    """The trials that change a source this site has."""
    return [name for name, (sources, _label, _note) in TRIALS.items() if any(s in SOURCES for s in sources)]


def on(name: str) -> bool:
    if not _armed or name not in TRIALS:
        return False
    try:
        return db.get_setting(_KEY + name, "") == "1"
    except Exception:
        return False  # A switch that cannot be read is off.


def switch(name: str, enabled: bool) -> None:
    if name not in offered():
        raise UnsafeURL("unknown trial")
    db.set_setting(_KEY + name, "1" if enabled else "0")


def count(name: str, *, fell_back: bool = False) -> None:
    """One use of a trial, or one time it had to hand over to the earlier path."""
    global _saved_at, _unsaved
    with _lock:
        if not _armed or name not in _counts:
            return
        _counts[name]["fallbacks" if fell_back else "used"] += 1
        _unsaved = True
        if time.monotonic() - _saved_at < _SAVE_EVERY:
            return
        _saved_at = time.monotonic()
        _unsaved = False
        snapshot = json.dumps(_counts)
    try:
        db.set_setting(_COUNTS_KEY, snapshot)
    except Exception:
        pass  # The counts are a convenience; a locked database must not fail the request.


def attempt(name: str, new: Callable[[], T], old: Callable[[], T]) -> T:
    """The new way while its switch is on; the earlier way otherwise, and whenever the new one fails."""
    if not on(name):
        return old()
    try:
        result = new()
    except Exception as error:
        # The class only: messages can carry titles or addresses.
        logger.warning("trial %s fell back to the earlier path: %s", name, type(error).__name__)
        count(name, fell_back=True)
        return old()
    count(name)
    return result


def listing() -> list[dict]:
    with _lock:
        counts = {name: dict(row) for name, row in _counts.items()}
    return [{"id": name, "label": TRIALS[name][1], "note": TRIALS[name][2], "on": on(name),
             "used": counts.get(name, {}).get("used", 0), "fallbacks": counts.get(name, {}).get("fallbacks", 0)}
            for name in offered()]
