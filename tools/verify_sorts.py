"""Live check of a source's sort options: every option lists enough items, differs from the default
order and pages without overlap. Prints one line per option and a markdown block for the record.

usage: python tools/verify_sorts.py <source> <kind> [slug] [--pause N]
"""
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import sites  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) < 2:
    sys.exit(__doc__)
source, kind = args[0], args[1]
slug = args[2] if len(args) > 2 else None
pause = float(sys.argv[sys.argv.index("--pause") + 1]) if "--pause" in sys.argv else 1.0

site = sites.get(source)
options = site.sorts(kind, slug)
orders = [o for o in options if not o.kind] or options


def fetch(sort, page, option=None):
    time.sleep(pause)
    started = time.time()
    try:
        if option is not None and option.kind:
            listing = site.browse(option.kind, slug=option.slug or None, page=page)
        elif sort:
            listing = site.browse(kind, slug=slug, page=page, sort=sort)
        else:
            listing = site.browse(kind, slug=slug, page=page)
        return [c.id for c in listing.items], time.time() - started, ""
    except Exception as e:  # report, keep going
        return [], time.time() - started, repr(e)[:120]


default1, _, err = fetch("", 1)
print(f"{source} {kind}/{slug or ''}: default p1 {len(default1)} items {err}")
rows = []
failed = 0
for i, option in enumerate(orders):
    p1, t1, e1 = fetch(option.id if i else "", 1, option)
    p2, t2, e2 = fetch(option.id if i else "", 2, option)
    enough = len(p1) >= 12 and len(p2) >= 12
    differs = p1[:10] != default1[:10]
    paged = p1 != p2 and not set(p1[:10]) & set(p2[:10])
    if i == 0:
        verdict = "default" if p1[:10] == default1[:10] else "DIFFERS FROM DEFAULT"
        ok = enough and paged and p1[:10] == default1[:10]
    else:
        verdict = "ok" if enough and differs and paged else "FAIL"
        ok = verdict == "ok"
    failed += not ok
    print(f"  {option.id:12} {option.label:6} p1={len(p1):3} p2={len(p2):3} differs={differs!s:5} paged={paged!s:5} "
          f"{t1:.1f}s/{t2:.1f}s {verdict} {e1 or e2}")
    rows.append(f"| {source} | {kind}{'/' + slug if slug else ''} | {option.id}（{option.label}） | {len(p1)}／{len(p2)} | "
                f"{'—' if i == 0 else ('是' if differs else '否')} | {'是' if paged else '否'} | {'預設' if i == 0 and ok else ('通過' if ok else '不通過')} |")
print("\n".join(rows))
sys.exit(1 if failed else 0)
