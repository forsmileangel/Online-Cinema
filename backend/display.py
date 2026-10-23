"""Titles as shown to the viewer: Traditional script for sources that publish Simplified.

Only the display changes. Searching, matching episodes across sources and the
stored history keep working on whatever the source calls the title.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import zh
from .models import Card, HomePayload, Listing, VideoDetail

# Sources measured to carry Simplified titles (sampled from their home pages).
SOURCES = frozenset({"hongguo", "huangguo", "huangguo2", "duanju51", "91md", "pornhub", "mvffm", "mmov"})
# Japanese and Korean titles share characters with Chinese ones; leave them as written.
_KANA_HANGUL = re.compile(r"[぀-ヿ가-힯]")


@lru_cache(maxsize=8192)
def _convert(text: str) -> str:
    return zh.to_traditional(text)


def title(source: str | None, text: str | None) -> str | None:
    if not text or source not in SOURCES or _KANA_HANGUL.search(text):
        return text
    return _convert(text)


def card(source: str | None, item: Card) -> Card:
    own = item.source or source
    item.title = title(own, item.title) or item.title
    item.original_title = title(own, item.original_title)
    return item


def cards(source: str | None, items: list[Card]) -> list[Card]:
    for item in items:
        card(source, item)
    return items


def listing(source: str | None, value: Listing) -> Listing:
    cards(source, value.items)
    return value


def home(source: str | None, payload: HomePayload) -> HomePayload:
    cards(source, payload.continue_watching)
    for row in payload.rows:
        cards(source, row.items)
    return payload


def video(source: str | None, detail: VideoDetail) -> VideoDetail:
    own = detail.source or source
    detail.title = title(own, detail.title) or detail.title
    detail.original_title = title(own, detail.original_title)
    for episode in detail.episodes:
        episode.title = title(own, episode.title) or episode.title
    cards(own, detail.related)
    return detail


def rows(source: str | None, items: list[dict]) -> list[dict]:
    """History / favorites / offline rows: dicts with a title and their own source."""
    for row in items:
        own = row.get("source") or source
        for key in ("title", "episode_title"):
            if row.get(key):
                row[key] = title(own, row[key])
    return items
