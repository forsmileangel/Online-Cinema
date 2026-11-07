#!/usr/bin/env python
"""Walk every source of the running site: home page, video, playlist, key, first segment, covers.

Run from either repository when a site may have changed its pages:

    python tools/check-sources.py                       every source
    python tools/check-sources.py mvffm huangguo        only these
    python tools/check-sources.py --kind new huangguo2  sample a listing instead of the home page

GET requests only, so favourites and history stay as they are. Prints source ids, counts and statuses:
no titles, and only numeric video ids. Exits 1 when a source has no playable sample.
"""
import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urljoin

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASS_NEEDED = 2
# hls.js looks for the first MPEG-TS packet this far into a segment.
HLS_JS_SYNC_WINDOW = 940
# The source works, it only asks for something a GET request cannot do.
NOT_A_FAULT = ("請先在網頁準備", "規定要登入")


def default_base() -> str:
    port = re.search(r"^PORT\s*=\s*(\d+)", (ROOT / "backend" / "settings.py").read_text(encoding="utf-8"), re.M)
    return f"http://127.0.0.1:{port.group(1) if port else 6969}"


def fetch(url, limit=None, timeout=45, headers=None):
    started = time.monotonic()
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(limit) if limit else response.read()
            return response.status, body, response.headers.get("content-type", ""), time.monotonic() - started
    except urllib.error.HTTPError as error:
        return error.code, error.read(4000), error.headers.get("content-type", ""), time.monotonic() - started
    except Exception as error:
        return 0, repr(error)[:120].encode(), "", time.monotonic() - started


def detail(body: bytes) -> str:
    try:
        return str(json.loads(body).get("detail"))[:70]
    except (ValueError, AttributeError):
        return body[:70].decode("utf-8", "replace")


def find_cards(node, found):
    if isinstance(node, dict):
        if "id" in node and "title" in node and "cover" in node:
            found.append(node)
        for value in node.values():
            find_cards(value, found)
    elif isinstance(node, list):
        for value in node:
            find_cards(value, found)


def unique_cards(body: bytes) -> list[dict]:
    cards: list[dict] = []
    find_cards(json.loads(body), cards)
    return list({str(card["id"]): card for card in cards}.values())


def segment_kind(head: bytes, encrypted: bool) -> str:
    if not head:
        return "empty"
    if head[:1] == b"G":
        return "ts"
    if head[4:8] in (b"ftyp", b"styp", b"moof", b"sidx"):
        return "mp4"
    if head.lstrip()[:1] in (b"<", b"{"):
        return "text"
    if encrypted:
        return "aes"
    for offset in range(min(len(head) - 377, HLS_JS_SYNC_WINDOW + 1)):
        if head[offset] == head[offset + 188] == head[offset + 376] == 0x47:
            return f"ts@{offset}"
    return "unknown:" + head[:4].hex()


def segment_plays(kind: str) -> bool:
    return kind in ("ts", "mp4", "aes") or kind.startswith("ts@")


def check_stream(base: str, playlist: str) -> tuple[bool, str]:
    """Follows a playlist down to its first segment."""
    url = urljoin(base + "/", playlist)
    status, body, _, took = fetch(url, limit=2 * 1024 * 1024)
    if status not in (200, 206):
        return False, f"playlist {status} {detail(body)}"
    if not body[:64].lstrip().startswith(b"#EXTM3U"):
        kind = segment_kind(body[:4096], False)
        return segment_plays(kind), f"direct {kind} {took:.1f}s"
    text = body.decode("utf-8", "replace")
    for _ in range(3):
        if "#EXT-X-STREAM-INF" not in text:
            break
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        variant = next((line for i, line in enumerate(lines) if not line.startswith("#") and lines[i - 1].startswith("#EXT-X-STREAM-INF")), "")
        if not variant:
            return False, "master without variant"
        url = urljoin(url, variant)
        status, body, _, _ = fetch(url)
        if status != 200:
            return False, f"variant {status} {detail(body)}"
        text = body.decode("utf-8", "replace")
    segments = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not segments:
        return False, "playlist without segments"
    minutes = sum(float(x) for x in re.findall(r"#EXTINF:([\d.]+)", text)) / 60
    note = f"{len(segments)}seg {minutes:.0f}min"
    key = re.search(r'#EXT-X-KEY:[^\n]*URI="([^"]+)"', text)
    if key:
        status, data, _, _ = fetch(urljoin(url, key[1]))
        if status != 200 or len(data) != 16:
            return False, f"{note} key {status} len={len(data)}"
        note += " key16"
    init = re.search(r'#EXT-X-MAP:[^\n]*URI="([^"]+)"', text)
    if init:
        status, _, _, _ = fetch(urljoin(url, init[1]), limit=4096)
        if status not in (200, 206):
            return False, f"{note} init {status}"
    status, data, _, took = fetch(urljoin(url, segments[0]), limit=4096, headers={"Range": "bytes=0-4095"})
    if status not in (200, 206):
        return False, f"{note} segment {status} {detail(data)}"
    kind = segment_kind(data, bool(key))
    return segment_plays(kind) or bool(init), f"{note} seg={kind} {took:.1f}s"


def check_source(base: str, source: str, kind: str, samples: int) -> str:
    """Prints the source's report and returns PASS, FAIL or WAIT (nothing a GET request could judge)."""
    path = f"/api/browse/{kind}?source={source}" if kind else f"/api/home?source={source}"
    status, body, _, took = fetch(base + path, timeout=60)
    if status != 200:
        print(f"{source:10} FAIL list {status} {took:.1f}s {detail(body)}", flush=True)
        return "FAIL"
    cards = unique_cards(body)
    picked = cards[:: max(1, len(cards) // samples)][:samples]
    results, passed, waiting = [], 0, 0
    for card in picked:
        video_id = str(card["id"])
        shown = video_id if video_id.isdigit() else "#"
        status, body, _, took = fetch(f"{base}/api/video/{source}/{quote(video_id, safe='')}", timeout=70)
        if status != 200:
            message = detail(body)
            waiting += any(text in message for text in NOT_A_FAULT)
            results.append(f"{shown}: video {status} {took:.1f}s {message}")
            continue
        data = json.loads(body)
        playlist = data.get("playlist") or next((e.get("playlist") for e in data.get("episodes") or [] if e.get("playlist")), "")
        episodes = len(data.get("episodes") or [])
        if not playlist:
            results.append(f"{shown}: no playlist eps={episodes}")
            continue
        ok, note = check_stream(base, playlist)
        results.append(f"{shown}: {'ok' if ok else 'FAIL'} {took:.1f}s eps={episodes} {note}")
        passed += ok
        if passed >= PASS_NEEDED and not kind:
            break
    covers: dict[str, int] = {}
    for card in cards[:: max(1, len(cards) // 6)][:6]:
        cover = card.get("cover") or ""
        if not cover:
            covers["none"] = covers.get("none", 0) + 1
            continue
        status, _, ctype, _ = fetch(urljoin(base + "/", cover), limit=2048, timeout=30)
        label = "ok" if status == 200 and "image" in ctype else str(status)
        covers[label] = covers.get(label, 0) + 1
    verdict = "PASS" if passed else "WAIT" if waiting else "FAIL"
    print(f"{source:10} {verdict} {len(cards)}cards covers={covers} passed={passed}/{len(results)}", flush=True)
    for line in results:
        print("    " + line, flush=True)
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="Check every source from its list down to the first video segment.")
    parser.add_argument("sources", nargs="*", help="source ids; every source when left out")
    parser.add_argument("--base", default=default_base(), help="the running site (default: this repository's port)")
    parser.add_argument("--kind", default="", help="sample this listing, such as new, instead of the home page")
    parser.add_argument("--samples", type=int, default=4, help="videos to try per source (default 4)")
    args = parser.parse_args()
    status, body, _, _ = fetch(args.base + "/api/settings")
    if status != 200:
        print(f"{args.base} does not answer: {status}")
        return 2
    sources = args.sources or [item["id"] for item in json.loads(body).get("sources", [])]
    verdicts = {source: check_source(args.base, source, args.kind, args.samples) for source in sources}
    failed = [source for source, verdict in verdicts.items() if verdict == "FAIL"]
    print(f"{len(verdicts)} sources, {len(failed)} failed" + (": " + ", ".join(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
