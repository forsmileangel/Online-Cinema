"""Sort options of a source's lists.

An adapter that offers them has sorts(kind, slug) -> list[SortOption]. The first option
without a kind is the site's own default order; the other options without a kind are
orders its browse() takes as sort=. Options with a kind are other lists of the source
(Pornhub's 最新 is /video?o=mr, a list of its own) and are only linked to.
"""
from __future__ import annotations

from .models import Listing
from .security import safe_sort


def browse(site, kind: str, slug: str | None, page: int, sort: str | None = "") -> Listing:
    sort = safe_sort(sort)
    options_of = getattr(site, "sorts", None)
    options = list(options_of(kind, slug) or []) if callable(options_of) else []
    orders = [option.id for option in options if not option.kind]
    # An id the list does not offer (an old link, another list's order) falls back to the default.
    if sort not in orders[1:]:
        sort = ""
    # The same call as before sorting existed unless an order is asked for.
    args = {"slug": slug} if slug is not None else {}
    listing = site.browse(kind, **args, page=page, sort=sort) if sort else site.browse(kind, **args, page=page)
    if options:
        here = next((option.id for option in options
                     if option.kind and option.kind == kind and option.slug == (slug or "")), "")
        listing.sorts = options
        listing.sort = sort or here or (orders[0] if orders else "")
    return listing
