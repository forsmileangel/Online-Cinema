"""Files that must be the same in 深夜電影院 and 線上電影院: fail when any of them has drifted.

Run from either repository (``python tools/check-shared.py``); the other one is the sibling folder.
A change to a shared file is made in one repo and copied to the other (``--sync`` copies this
repo's version over the sibling's). Line endings are ignored.
"""
from __future__ import annotations

import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parents[1]
SIBLINGS = {"MidnightCinema": "Online Cinema", "Online Cinema": "MidnightCinema"}

# Backend modules and tests with no site-specific line in them.
SHARED_FILES = [
    "backend/__main__.py", "backend/alternatives.py", "backend/cast_quality.py", "backend/display.py",
    "backend/home_fill.py", "backend/host_guard.py", "backend/img_cache.py", "backend/line_memory.py", "backend/logsetup.py",
    "backend/nesthub.py", "backend/offline.py", "backend/pools.py", "backend/read_ahead.py",
    "backend/seg_cache.py", "backend/sorting.py", "backend/terms.py", "backend/translate.py",
    "backend/trials.py", "backend/tv_session.py", "backend/web_serving.py", "backend/zh.py",
    "backend/sites/anigamer.py", "backend/sites/chinaq.py", "backend/sites/gimy.py", "backend/sites/hongguo.py",
    "backend/sites/mmov.py", "backend/sites/mvffm.py",
    "tests/test_alternatives.py", "tests/test_anigamer.py", "tests/test_cast_activation.py",
    "tests/test_cast_downshift.py", "tests/test_cast_quality.py", "tests/test_cast_session.py",
    "tests/test_cast_standby.py", "tests/test_cast_volume.py", "tests/test_chinaq.py",
    "tests/test_chinaq_availability.py", "tests/test_chinaq_playback.py", "tests/test_db_cache.py",
    "tests/test_display.py", "tests/test_dns_guard.py", "tests/test_episode_preflight.py", "tests/test_gimy.py",
    "tests/test_health.py", "tests/test_history_library.py", "tests/test_home_fill.py", "tests/test_hongguo.py",
    "tests/test_host_guard.py", "tests/test_img_cache.py", "tests/test_logsetup.py", "tests/test_media_redirects.py",
    "tests/test_mmov.py", "tests/test_next_buffer.py", "tests/test_offline.py", "tests/test_offline_cdn_resume.py",
    "tests/test_offline_priority.py", "tests/test_offline_recovery.py", "tests/test_offline_retention.py",
    "tests/test_offline_speed_and_recovery.py", "tests/test_offline_verified.py", "tests/test_pools.py",
    "tests/test_read_ahead.py", "tests/test_search_multi.py", "tests/test_seg_cache.py", "tests/test_sorting.py",
    "tests/test_startup.py", "tests/test_terms.py", "tests/test_trials.py", "tests/test_tv_pairing.py", "tests/test_wake.py",
    "tests/test_web_quality.py", "tests/test_web_serving.py", "tests/test_shared_files.py", "tests/test_zh.py",
    "tools/check-shared.py", "tools/check-sources.py", "tools/verify_sorts.py",
    "scripts/deploy.ps1", "scripts/test.ps1",
    "web/eslint.config.js", "web/public/favicon.svg", "web/public/icon-192.png", "web/public/icon-512.png",
    "web/public/apple-touch-icon.png",
    ".gitattributes",
]
# Whole folders: every file in them, except the one file that is meant to differ.
SHARED_TREES = {"web/src": {"web/src/appConfig.ts"}, "e2e": set()}


def shared_paths(root: pathlib.Path) -> list[str]:
    paths = list(SHARED_FILES)
    for tree, excluded in SHARED_TREES.items():
        base = root / tree
        if base.is_dir():
            paths += sorted(p.relative_to(root).as_posix() for p in base.rglob("*")
                            if p.is_file() and p.relative_to(root).as_posix() not in excluded)
    return paths


def normalised(path: pathlib.Path) -> bytes | None:
    return path.read_bytes().replace(b"\r\n", b"\n") if path.is_file() else None


def drift(root: pathlib.Path, sibling: pathlib.Path) -> list[tuple[str, str]]:
    found = []
    for rel in shared_paths(root):
        mine, theirs = normalised(root / rel), normalised(sibling / rel)
        if mine is None and theirs is None:
            continue
        if mine is None or theirs is None:
            found.append((rel, "missing in " + ("this repo" if mine is None else sibling.name)))
        elif mine != theirs:
            found.append((rel, "differs"))
    return found


def main(argv: list[str]) -> int:
    sibling = HERE.parent / SIBLINGS.get(HERE.name, "")
    if not sibling.is_dir():
        print(f"sibling repository not found next to {HERE.name}; nothing to compare")
        return 0
    problems = drift(HERE, sibling)
    if "--sync" in argv:
        for rel, _ in problems:
            if (HERE / rel).is_file():
                (sibling / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(HERE / rel, sibling / rel)
                print("copied", rel)
        problems = drift(HERE, sibling)
    for rel, what in problems:
        print(f"{what}: {rel}")
    print(f"{len(shared_paths(HERE))} shared paths checked against {sibling.name}; {len(problems)} differ")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
