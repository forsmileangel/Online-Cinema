"""Public Anime Crazy catalog and anonymous, ad-supported playback."""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from urllib.parse import quote

from curl_cffi import requests

from ..hls_proxy import proxied_media
from ..models import Card, Episode, Listing, PickChip, PickGroup, VideoDetail
from ..security import SourceUnavailable, assert_https_url, safe_search_query

ORIGIN = "https://ani.gamer.com.tw"
API = "https://api.gamer.com.tw/anime/v1/"
HEADERS = {"Origin": ORIGIN, "Referer": ORIGIN + "/"}
GROUPS = {"featured": "本季新番", "hot": "近期熱播", "new": "新上架"}
_lock = threading.RLock()
_memo: OrderedDict = OrderedDict()
_jobs: dict = {}
_ready: dict = {}
_TTL = 1800


def _id(value):
    value = str(value or "")
    if not re.fullmatch(r"[1-9][0-9]{0,11}", value):
        raise SourceUnavailable("動畫瘋的作品或集數編號無效")
    return value


def _json(session, url, params=None):
    response = session.get(url, params=params, headers=HEADERS, timeout=20, allow_redirects=False)
    response.raise_for_status()
    response.encoding = "utf-8"
    result = response.json()
    if result.get("error"):
        message = result["error"].get("message") or "來源暫時無法使用"
        raise SourceUnavailable("動畫瘋：" + str(message)[:200])
    return result


def _data(path, **params):
    key = (path, tuple(sorted(params.items())))
    with _lock:
        cached = _memo.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
    with requests.Session(impersonate="chrome131") as session:
        data = _json(session, API + path, params)["data"]
    with _lock:
        _memo[key] = (time.monotonic() + 180, data)
        _memo.move_to_end(key)
        while len(_memo) > 32:
            _memo.popitem(last=False)
    return data


def _cover(url):
    try:
        assert_https_url(url, {"p2.bahamut.com.tw"})
        return "/api/img?u=" + quote(url, safe="")
    except ValueError:
        return ""


def _cards(items):
    cards, seen = [], set()
    for item in items:
        vid = str(item.get("animeSn") or "")
        if not re.fullmatch(r"[1-9][0-9]{0,11}", vid) or vid in seen:
            continue
        seen.add(vid)
        vip = bool((item.get("highlightTag") or {}).get("vipTime"))
        cards.append(Card(id=vid, source="anigamer", title=str(item.get("title") or vid),
                          cover=_cover(item.get("cover") or ""),
                          duration="付費會員限定" if vip else item.get("info") or item.get("dateInfo") or None))
    return cards


def _groups():
    data = _data("index.php")
    return {"featured": data.get("newAnime", {}).get("date", []),
            "hot": data.get("hotAnime", []), "new": data.get("newAdded", [])}


def home_bundle():
    groups = _groups()
    return ([(key, title, _cards(groups[key])[:24]) for key, title in GROUPS.items()],
            [PickGroup(title="動畫", items=[PickChip(name=title, kind=key) for key, title in GROUPS.items()])])


def browse(kind, slug=None, page=1):
    if kind not in GROUPS:
        raise SourceUnavailable("找不到這個動畫分類")
    return _listing(_cards(_groups()[kind]), page, GROUPS[kind])


def _listing(cards, page, title):
    start = (page - 1) * 24
    return Listing(items=cards[start:start + 24], page=page, has_next=start + 24 < len(cards),
                   pages=max(1, (len(cards) + 23) // 24), title=title)


def search(query, page=1):
    query = safe_search_query(query)
    return _listing(_cards(_data("search.php", kw=query, recordKeyword=0).get("animeList", [])), page, query)


def _info(video_id, ep=None):
    video_id = _id(video_id)
    info = _data("video.php", animeSn=video_id)
    entries = [(str(item["videoSn"]), str(item["episode"]))
               for group in info["anime"].get("episodes", {}).values() for item in group]
    selected = _id(ep or info["video"]["videoSn"])
    if selected not in dict(entries):
        raise SourceUnavailable("動畫瘋找不到這一集")
    return video_id, selected, entries, info


def _purge():
    now = time.monotonic()
    for key, job in list(_jobs.items()):
        if job["expires"] <= now:
            job["session"].close()
            del _jobs[key]
    for key, value in list(_ready.items()):
        if value[0] <= now:
            del _ready[key]


def prepare(video_id, ep=None):
    video_id, selected, _entries, _meta = _info(video_id, ep)
    with _lock:
        _purge()
        if (video_id, selected) in _ready:
            return {"ready": True}
        if len(_jobs) >= 8:
            raise SourceUnavailable("動畫瘋準備中的影片較多，請稍後再試")
        session = requests.Session(impersonate="chrome131")
        try:
            session.get(ORIGIN + "/animeVideo.php", params={"sn": selected}, headers=HEADERS, timeout=20).raise_for_status()
            device = _json(session, ORIGIN + "/ajax/getdeviceid.php")["deviceid"]
            _json(session, API + "video.php", {"videoSn": selected})
            _json(session, ORIGIN + "/ajax/token.php",
                  {"adID": 0, "sn": selected, "device": device, "hash": secrets.token_hex(6)})
            ad_response = session.get("https://i2.bahamut.com.tw/JS/ad/animeVideo2.js", headers=HEADERS, timeout=20)
            ad_response.raise_for_status()
            match = re.search(r"var adlist\s*=\s*(\[\[.*?\]\]);", ad_response.text)
            choices = [ad for ad in json.loads(match[1]) if len(ad) >= 4 and ad[3] == "video"
                       and str(ad[0]).isdigit() and str(ad[2]).isdigit()] if match else []
            if not choices:
                raise SourceUnavailable("動畫瘋目前的廣告格式尚不支援，請稍後再試")
            ad = secrets.choice(choices)
            key = secrets.token_urlsafe(24)
            _jobs[key] = dict(session=session, device=device, sn=selected, video_id=video_id, sid=ad[2],
                              started=None, expires=time.monotonic() + 180, ready=False)
            return {"ready": False, "id": key, "seconds": 31,
                    "ad": proxied_media("https://bahamut.akamaized.net/ad/" + str(ad[0]) + "/playlist.m3u8")}
        except Exception:
            session.close()
            raise


def ad_event(key, event):
    with _lock:
        _purge()
        job = _jobs.get(key)
        if not job:
            raise SourceUnavailable("動畫瘋準備逾時，請重試")
        session = job["session"]
        if event == "cancel":
            session.close()
            del _jobs[key]
            return {"ready": False}
        if event == "start":
            if job["started"] is None:
                session.get(ORIGIN + "/ajax/videoCastcishu.php", params={"s": job["sid"], "sn": job["sn"]},
                            headers=HEADERS, timeout=20).raise_for_status()
                job["started"] = time.monotonic()
            return {"ready": False}
        if event != "complete" or job["started"] is None or time.monotonic() - job["started"] < 30:
            raise SourceUnavailable("動畫瘋尚未準備完成")
        if not job["ready"]:
            session.get(ORIGIN + "/ajax/videoCastcishu.php", params={"s": job["sid"], "sn": job["sn"], "ad": "end"},
                        headers=HEADERS, timeout=20).raise_for_status()
            data = _json(session, API + "video_src.php",
                         {"videoSn": job["sn"], "deviceid": job["device"], "deviceTypeUseCases": 1})["data"]
            sources = [item for item in data.get("srcUseCases", []) if item.get("deviceType") == 1]
            if not sources:
                raise SourceUnavailable("動畫瘋未提供可播放串流")
            url = sources[0]["src"]["playlist"]
            assert_https_url(url, {"bahamut.akamaized.net"})
            _ready[(job["video_id"], job["sn"])] = (time.monotonic() + _TTL, proxied_media(url))
            job["ready"] = True
            job["expires"] = time.monotonic() + 30
        return {"ready": True}


def fetch_video(video_id, ep=None):
    video_id, selected, entries, info = _info(video_id, ep)
    with _lock:
        _purge()
        ready = {sn: _ready.get((video_id, sn), (0, ""))[1] for sn, _ in entries}
    if not ready[selected]:
        raise SourceUnavailable("請先在網頁準備這集動畫，再開始播放或投放")
    anime = info["anime"]
    return VideoDetail(id=video_id, source="anigamer", title=anime["title"], cover=_cover(anime.get("cover", "")),
                       description=anime.get("content", ""), playlist=ready[selected], resolved_episode_id=selected,
                       episodes=[Episode(id=sn, title="第" + label + "集", playlist=ready[sn]) for sn, label in entries],
                       related=_cards(info.get("relatedAnime", [])))


def proxied_cover(video_id):
    return ""
