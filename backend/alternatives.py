"""Other sources of the same show, for episodes the current source lacks."""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed

from . import sites, zh
from .catalog import SOURCE_GROUPS, SOURCE_LABELS

DEADLINE = 30
CACHE_SECONDS = 600
_cache: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}
_lock = threading.Lock()

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_SEASON = re.compile(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*[季部]|season\s*(\d{1,2})", re.I)
_YEAR = re.compile(r"[（(]\s*\d{4}\s*[)）]")


_SPAN = re.compile(r"第\s*0*(\d{1,4})\s*[-–—~～至到]\s*0*(\d{1,4})\s*[集話话]")


def episode_span(title: str, episode_id: str = "") -> tuple[int, int] | None:
    """First and last episode one entry covers: a merged upload is labelled 第1-3集."""
    match = _SPAN.search(title or "")
    if match and 0 <= int(match[2]) - int(match[1]) <= 50:
        return int(match[1]), int(match[2])
    number = episode_number(title, episode_id)
    return (number, number) if number is not None else None


def episode_number(title: str, episode_id: str = "") -> int | None:
    """第7集 / 第 07 集 / 7 / EP7 (第1-3集 counts as 1), from the label or else the id."""
    match = _SPAN.search(title or "")
    if match:
        return int(match[1])
    for text in (title or "", episode_id or ""):
        match = (re.fullmatch(r"\s*(?:第\s*)?0*(\d{1,4})\s*(?:集|話|话)?\s*", text)
                 or re.search(r"第\s*0*(\d{1,4})\s*[集話话]", text)
                 or re.fullmatch(r"\s*ep?\s*0*(\d{1,4})\s*", text, re.I))
        if match:
            return int(match[1])
    return None


def _core(title: str) -> str:
    # Sites append the original-language title after a space; the first part is the name.
    words = _YEAR.sub(" ", title or "").split()
    return re.sub(r"[\W_]+", "", zh.to_simplified(words[0])).lower() if words else ""


def _season(title: str) -> int:
    match = _SEASON.search(title or "")
    if not match:
        return 1
    raw = match[1] or match[2]
    if raw.isdigit():
        return int(raw)
    if raw.startswith("十"):
        return 10 + _CN_DIGITS.get(raw[1:], 0)
    tens, _, ones = raw.partition("十")
    return _CN_DIGITS.get(tens, 0) * 10 + _CN_DIGITS.get(ones, 0) if "十" in raw else _CN_DIGITS.get(raw, 1)


def find(source: str, video_id: str, title: str) -> list[dict]:
    """Same-group sources that list this show, with the episode numbers each has."""
    key = (source, video_id, title)
    with _lock:
        kept = _cache.get(key)
        if kept and time.monotonic() - kept[0] < CACHE_SECONDS:
            return kept[1]
    group = SOURCE_GROUPS.get(source)
    others = [s["id"] for s in sites.available() if group and s["group"] == group and s["id"] != source]
    core, season = _core(title), _season(title)
    if not others or not core:
        return []
    query = title.split()[0][:50]
    deadline = time.monotonic() + DEADLINE

    def search(src: str):
        listing = sites.get(src).search(query, page=1)
        return [(src, card) for card in listing.items
                if _core(card.title) == core and _season(card.title) == season][:2]

    def detail(src: str, card):
        found = sites.get(src).fetch_video(card.id)
        numbered = {}
        for ep in found.episodes:
            span = episode_span(ep.title, ep.id)
            if span is not None:
                for number in range(span[0], span[1] + 1):
                    numbered.setdefault(number, ep.id)
        return dict(source=src, label=SOURCE_LABELS.get(src, src), id=card.id, title=found.title or card.title,
                    count=len(found.episodes), episodes=sorted([n, ep_id] for n, ep_id in numbered.items()))

    pool = ThreadPoolExecutor(max_workers=6)
    matches, results = [], []
    try:
        searches = [pool.submit(search, src) for src in others]
        try:
            for future in as_completed(searches, timeout=max(1, deadline - time.monotonic())):
                try:
                    matches += future.result()
                except Exception:
                    continue  # A refused or failing site simply offers nothing.
        except FuturesTimeout:
            pass
        details = [pool.submit(detail, src, card) for src, card in matches]
        try:
            for future in as_completed(details, timeout=max(1, deadline - time.monotonic())):
                try:
                    results.append(future.result())
                except Exception:
                    continue
        except FuturesTimeout:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    results.sort(key=lambda item: (-len(item["episodes"]), item["source"]))
    with _lock:
        _cache[key] = (time.monotonic(), results)
        while len(_cache) > 64:
            _cache.pop(next(iter(_cache)))
    return results
