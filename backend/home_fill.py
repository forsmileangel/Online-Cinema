"""Extra home rows for sources whose own home page is sparse.

A source with fewer than MIN_ROWS rows gets the first of its picks that are not on its
home page yet, page 1 of each, fetched in parallel. The home page waits at most BUDGET
seconds for them; a row that arrives later is kept for the next load.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait

from .models import Card, HomeRow, PickChip, PickGroup

MIN_ROWS = 4
MAX_FILL = 3
BUDGET = 6.0
TTL = 600
# A failed or empty fetch is tried again sooner than a good one is refreshed.
RETRY_TTL = 60
MIN_ITEMS = 4
MAX_ITEMS = 24
MAX_KEPT = 200

Key = tuple[str, str, str]

_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="home-fill")
_lock = threading.Lock()
# (source, kind, slug) -> (expires at, cards)
_kept: dict[Key, tuple[float, list[Card]]] = {}
_flights: dict[Key, Future] = {}


def rows(site, raw) -> list[HomeRow]:
    """The adapter's (kind, title, cards) rows; more_link(kind) False hides 更多 on a row."""
    more = getattr(site, "more_link", None)
    return [HomeRow(kind=kind, title=title, items=items, more=more(kind) if callable(more) else True)
            for kind, title, items in raw]


def _wanted(site, shown: list[HomeRow], room: int, picks: list[PickGroup]) -> list[PickChip]:
    room = min(MAX_FILL, room)
    skip = set(getattr(site, "HOME_FILL_SKIP", ()))
    seen = {(row.kind, row.slug) for row in shown}
    chips: list[PickChip] = []
    for group in picks:
        for chip in group.items:
            where = (chip.kind, chip.slug)
            if len(chips) >= room:
                return chips
            # kw chips are searches, not lists of the source's own.
            if where in seen or chip.kind in skip or chip.kind == "kw":
                continue
            seen.add(where)
            chips.append(chip)
    return chips


def _fetch(site, key: Key) -> None:
    _source, kind, slug = key
    try:
        items = list(site.browse(kind, slug=slug or None, page=1).items[:MAX_ITEMS])
    except Exception:
        items = []
    ttl = TTL if len(items) >= MIN_ITEMS else RETRY_TTL
    with _lock:
        _flights.pop(key, None)
        while len(_kept) >= MAX_KEPT:
            del _kept[next(iter(_kept))]
        _kept[key] = (time.monotonic() + ttl, items)


def fill(source: str, site, shown: list[HomeRow], picks: list[PickGroup]) -> list[HomeRow]:
    # Rows that came back empty (a section the source failed to send) leave room to fill.
    full = [row for row in shown if row.items]
    if not getattr(site, "HOME_FILL", True) or len(full) >= MIN_ROWS or not picks:
        return shown
    chips = _wanted(site, shown, MIN_ROWS - len(full), picks)
    if not chips:
        return shown
    keys = [(source, chip.kind, chip.slug) for chip in chips]
    pending = []
    now = time.monotonic()
    with _lock:
        for key in keys:
            kept = _kept.get(key)
            if kept and kept[0] > now:
                continue
            # One fetch per list at a time, however many home loads ask for it.
            flight = _flights.get(key)
            if flight is None:
                flight = _flights[key] = _pool.submit(_fetch, site, key)
            pending.append(flight)
    if pending:
        wait(pending, timeout=BUDGET)
    out = list(shown)
    with _lock:
        for chip, key in zip(chips, keys):
            kept = _kept.get(key)
            if kept and len(kept[1]) >= MIN_ITEMS:
                out.append(HomeRow(kind=chip.kind, slug=chip.slug, title=chip.name, items=list(kept[1])))
    return out


def reset() -> None:
    with _lock:
        _kept.clear()
        _flights.clear()
