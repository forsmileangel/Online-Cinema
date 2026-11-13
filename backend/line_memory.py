"""Trial "line_memory": the line that last played a show is tried first on its next episode.

Off, `first` returns the candidates as they came and `played` keeps nothing, so the adapters try
their lines in the site's order, as before.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import TypeVar
from urllib.parse import urlparse

from . import trials

T = TypeVar("T")
TRIAL = "line_memory"
# An episode runs longer than the 15 minutes the older per-adapter memories keep.
_KEEP = 6 * 3600
_MAX = 256
_lock = threading.Lock()
_lines: OrderedDict[tuple[str, str], tuple[float, str]] = OrderedDict()


def _remembered(source: str, video_id: str) -> str:
    with _lock:
        until, line = _lines.get((source, video_id), (0.0, ""))
        if until > time.monotonic():
            return line
        _lines.pop((source, video_id), None)
        return ""


def first(source: str, video_id: str, candidates: Sequence[T], line: Callable[[T], str]) -> list[T]:
    """The candidates with the remembered line moved to the front; the rest keep their order."""
    ordered = list(candidates)
    if not trials.on(TRIAL):
        return ordered
    wanted = _remembered(source, video_id)
    if wanted:
        ordered.sort(key=lambda candidate: line(candidate) != wanted)
    return ordered


def played(source: str, video_id: str, line: str) -> None:
    """Report the line that passed its check, to be tried first next time."""
    if not line or not video_id or not trials.on(TRIAL):
        return
    wanted = _remembered(source, video_id)
    if wanted:
        trials.count(TRIAL, fell_back=wanted != line)
    with _lock:
        _lines[(source, video_id)] = (time.monotonic() + _KEEP, line)
        _lines.move_to_end((source, video_id))
        while len(_lines) > _MAX:
            _lines.popitem(last=False)


def host(url: str) -> str:
    """The line of a play address, for sites that list bare addresses."""
    return (urlparse(url).hostname or "").lower()


def clear() -> None:
    with _lock:
        _lines.clear()
