from __future__ import annotations

from ..catalog import DEFAULT_SOURCE, GROUP_ORDER, SOURCE_GROUPS, SOURCE_LABELS, normalize_source
from . import anigamer
from . import chinaq
from . import dramaq
from . import mvffm
from . import mmov
from . import gimy
from . import hongguo

_SITES = {
    "mvffm": mvffm,
    "anigamer": anigamer,
    "hongguo": hongguo,
    "chinaq": chinaq,
    "gimy": gimy,
    "mmov": mmov,
    "dramaq": dramaq,
}


def get(source: str | None):
    src = normalize_source(source)
    if src not in _SITES:
        src = DEFAULT_SOURCE
    return _SITES[src]


def available() -> list[dict[str, str]]:
    rank = {group: i for i, group in enumerate(GROUP_ORDER)}
    # Stable sort: registry order is kept inside each group.
    ids = sorted(_SITES, key=lambda k: rank.get(SOURCE_GROUPS.get(k, ""), len(rank)))
    return [{"id": k, "label": SOURCE_LABELS.get(k, k), "group": SOURCE_GROUPS.get(k, "")} for k in ids]
