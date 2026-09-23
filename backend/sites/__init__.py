from __future__ import annotations

from ..catalog import DEFAULT_SOURCE, SOURCE_LABELS, normalize_source
from . import chinaq
from . import dramaq
from . import gimy
from . import hongguo

_SITES = {
    "hongguo": hongguo,
    "chinaq": chinaq,
    "gimy": gimy,
    "dramaq": dramaq,
}


def get(source: str | None):
    src = normalize_source(source)
    if src not in _SITES:
        src = DEFAULT_SOURCE
    return _SITES[src]


def available() -> list[dict[str, str]]:
    return [{"id": k, "label": SOURCE_LABELS.get(k, k)} for k in _SITES]
