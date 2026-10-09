"""Offline Chinese variants with lazily initialized, serialized converters."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_converters = {}


def _convert(text: str, mode: str) -> str:
    from opencc import OpenCC

    with _lock:
        if mode not in _converters:
            _converters[mode] = OpenCC(mode)
        return _converters[mode].convert(text)


# Users type Taiwan forms (著、眾、裡); plain t2s leaves 著 unconverted and
# s2t displays non-Taiwan forms such as 衆 and 裏.
def to_simplified(text: str) -> str:
    return _convert(text, "tw2s")


def to_traditional(text: str) -> str:
    return _convert(text, "s2tw")


def normalize_traditional(text: str) -> str:
    """Taiwan Traditional form with script variants folded (鬥→斗, 裏→裡)."""
    return to_traditional(to_simplified(text))


def search_key(text: str) -> str:
    """Script- and variant-insensitive key for local title matching."""
    return to_simplified(text).casefold()


def variants(text: str) -> list[str]:
    return list(dict.fromkeys([text, to_simplified(text), to_traditional(text)]))
