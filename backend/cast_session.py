"""Server-owned playback: browser lifetime never controls episode progression."""
from __future__ import annotations

import copy
import queue
import threading
import time
import uuid as uuid_module
from urllib.parse import parse_qs, urlparse

from . import cast, catalog, db, hls_proxy, sites
from .security import SiteBusy, assert_hls_url, safe_video_id

SERIES_SOURCES = {"hongguo", "chinaq", "gimy", "dramaq"}
TERMINAL = {"ended", "stopped", "error", "replaced"}


def natural_end(previous: dict | None, current: dict, expected: str) -> bool:
    if not previous or not current.get("idle") or current.get("idle_reason") in {"ERROR", "CANCELLED", "INTERRUPTED"}:
        return False
    if current.get("content_id") not in ("", expected) or previous.get("content_id") != expected:
        return False
    if current.get("idle_reason") == "FINISHED":
        return True
    return bool(previous.get("playing") and previous.get("duration", 0) > 0
                and previous.get("current_time", 0) >= previous["duration"] - 2)


class PlaybackSession:
    def __init__(self, body: dict):
        self.id = uuid_module.uuid4().hex
        self.uuid = body["uuid"]
        self.body = dict(body)
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.stop_requested = threading.Event()
        self.commands: queue.Queue = queue.Queue()
        self.detail = None
        self.previous = None
        self.episode_ids: list[str] = []
        self.epoch = 0
        self.last_saved = 0.0
        self.prefetched = None
        self.prefetching = False
        self.snapshot = dict(uuid=self.uuid, session_id=self.id, content_id="", playing=False, paused=False,
                             idle=False, buffering=False, current_time=body.get("position_sec", 0), duration=0,
                             phase="waking" if body.get("wake") else "loading", error="", title=body.get("title", ""),
                             source=body.get("source", ""), video_id=body.get("video_id", ""),
                             episode_id=body.get("episode_id", ""), autoplay_next=bool(body.get("autoplay_next")), playlist=body.get("url", ""), pending_action="")

    def get(self):
        with self.lock:
            return copy.deepcopy(self.snapshot)

    def publish(self, **values):
        with self.lock:
            if not self.cancelled.is_set():
                self.snapshot.update(values)

    def valid(self):
        return not self.cancelled.is_set() and not self.stop_requested.is_set()

    def save(self, state: dict, force=False):
        if not self.body.get("video_id") or not state.get("duration"):
            return
        now = time.monotonic()
        if not force and now - self.last_saved < 5:
            return
        db.upsert_history(self.body["video_id"], self.detail.title if self.detail else self.body.get("title", ""), self.detail.cover if self.detail else self.body.get("cover", ""),
                          state.get("current_time", 0), state["duration"], self.body["source"],
                          self.snapshot["episode_id"] or None)
        self.last_saved = now

    def load(self, episode: str, position=0):
        for attempt in range(2):
            try:
                self._load(episode, position)
                self.prefetch()
                return
            except SiteBusy:
                raise
            except Exception:
                self.prefetched = None
                if not self.valid() or attempt or self.body.get("source") not in SERIES_SOURCES:
                    raise

    def prefetch(self):
        episode = self.snapshot["episode_id"]
        index = self.episode_ids.index(episode) if episode in self.episode_ids else -1
        if self.prefetching or not self.snapshot["autoplay_next"] or not 0 <= index < len(self.episode_ids) - 1:
            return
        target, epoch = self.episode_ids[index + 1], self.epoch
        self.prefetching = True
        def resolve():
            try:
                detail = sites.get(self.body["source"]).fetch_video(self.body["video_id"], ep=target)
                if self.valid() and self.epoch == epoch:
                    self.prefetched = (target, time.monotonic(), detail)
            except Exception:
                pass  # Retry on demand; a broken next episode cannot stop this one.
            finally:
                self.prefetching = False
        threading.Thread(target=resolve, name="cast-prefetch", daemon=True).start()

    def _load(self, episode: str, position=0):
        if not self.valid():
            return
        self.publish(phase="loading", error="", playing=False, paused=False, idle=False, buffering=False,
                     episode_id=episode, current_time=position, duration=0, playlist="")
        self.previous = None
        self.epoch += 1
        playlist = self.body.get("url", "")
        title = self.body.get("title", "")
        source = self.body.get("source")
        if source in SERIES_SOURCES and self.body.get("video_id"):
            cached = self.prefetched
            self.prefetched = None
            detail = cached[2] if cached and cached[0] == episode and time.monotonic() - cached[1] < 120 else sites.get(source).fetch_video(self.body["video_id"], ep=episode or None)
            self.detail = detail
            self.episode_ids = [e.id for e in detail.episodes]
            episode = episode or detail.resolved_episode_id or (self.episode_ids[0] if self.episode_ids else "")
            selected = next((e for e in detail.episodes if e.id == episode), None)
            if detail.episodes and not selected:
                raise ValueError("找不到這一集，請重新載入選集")
            playlist = selected.playlist if selected else detail.playlist
            title = f"{detail.title} {selected.title}" if selected else detail.title
        if not playlist:
            raise ValueError("這一集解析失敗，請重試")
        parsed = urlparse(playlist)
        upstream = parse_qs(parsed.query).get("u", [])
        if parsed.scheme or parsed.netloc or parsed.path != "/api/hls" or len(upstream) != 1:
            raise ValueError("請從影片頁選擇可播放的串流")
        upstream = assert_hls_url(upstream[0])
        if not self.valid():
            return
        origin = cast.lan_media_origin(self.uuid)
        cast.check_media_origin(origin)
        url = hls_proxy.proxied_media(upstream, origin)
        mime = "video/mp4" if urlparse(upstream).path.lower().endswith(".mp4") else "application/vnd.apple.mpegurl"
        self.publish(playlist=playlist, title=title, episode_id=episode)
        marker = f"{self.id}-{self.epoch}"
        state = cast.play(url, mime, title, position, self.uuid, guard=self.valid, marker=marker, expected=cast.session_content(self.uuid, self.id) or self.snapshot["content_id"])
        if not self.valid():
            return
        self.previous = state
        self.publish(**state, phase="paused" if state.get("paused") else "playing")
        self.save(state, True)

    def tick(self):
        state = cast.status(self.uuid)
        expected = self.snapshot["content_id"]
        if state.get("idle_reason") == "ERROR":
            raise RuntimeError("電視無法播放這一集，請重試")
        if state.get("content_id") not in ("", expected) or (not state.get("idle") and state.get("content_id") != expected):
            self.publish(phase="replaced", playing=False, paused=False, error="電視已切換其他內容，已停止自動連播")
            return
        if natural_end(self.previous, state, expected):
            # Require a second idle observation; LG can briefly clear TrackURI.
            confirmed = cast.status(self.uuid)
            if not natural_end(self.previous, confirmed, expected):
                return
            self.save({**self.previous, "current_time": self.previous["duration"]}, True)
            episode = self.snapshot["episode_id"]
            index = self.episode_ids.index(episode) if episode in self.episode_ids else -1
            if self.snapshot["autoplay_next"] and 0 <= index < len(self.episode_ids) - 1:
                try:
                    self.load(self.episode_ids[index + 1])
                except Exception as e:
                    self.publish(phase="error", playing=False, paused=False, error=str(e))
            else:
                self.publish(**state, phase="ended")
            return
        if state.get("idle"):
            self.publish(**state, phase="stopped")
            return
        self.previous = state
        self.publish(**state, phase="paused" if state.get("paused") else "buffering" if state.get("buffering") else "playing", error="")
        self.save(state)

    def command(self, action: str, **values):
        if action not in {"pause", "resume", "seek", "stop", "episode", "autoplay", "retry", "detach"}:
            raise ValueError("不支援的投放操作")
        if self.snapshot["phase"] == "replaced" and action not in {"detach", "stop"}:
            raise ValueError("電視已切換其他內容，請重新投放")
        if action == "autoplay":
            self.publish(autoplay_next=bool(values.get("autoplay_next")))
            self.prefetch()
        else:
            if action in {"stop", "detach"}:
                self.stop_requested.set()
            self.publish(pending_action=action)
            self.commands.put((action, values))
        return self.get()

    def handle(self, action, values):
        if action == "detach":
            self.publish(phase="stopped", playing=False, paused=False)
            return
        if action == "episode":
            self.stop_requested.clear()
            episode = str(values.get("episode_id") or "")
            if episode not in self.episode_ids:
                raise ValueError("找不到這一集")
            self.load(episode)
            return
        if action == "retry":
            self.stop_requested.clear()
            self.load(self.snapshot["episode_id"], self.snapshot["current_time"])
            return
        expected = cast.session_content(self.uuid, self.id) or self.snapshot["content_id"]
        if action == "stop" and not expected:
            self.publish(phase="stopped", playing=False, paused=False)
            return
        state = cast.control(action, values.get("position_sec"), self.uuid, expected,
                             guard=lambda: not self.cancelled.is_set())
        self.save(self.previous if action == "stop" and self.previous else state, True)
        self.previous = state
        self.publish(**state, phase="stopped" if action == "stop" else "paused" if state.get("paused") else "playing", error="")

    def run(self):
        failures = 0
        try:
            if self.body.get("wake"):
                from .cast_devices import wake_and_wait
                wake_and_wait(self.uuid, self.valid)
            if self.valid():
                self.load(self.body.get("episode_id", ""), self.body.get("position_sec", 0))
        except Exception as e:
            self.publish(phase="error", error=str(e), playing=False, paused=False)
        while not self.cancelled.is_set():
            try:
                action, values = self.commands.get(timeout=1)
            except queue.Empty:
                if self.snapshot["phase"] in TERMINAL:
                    continue
                try:
                    self.tick()
                    failures = 0
                except Exception as e:
                    failures += 1
                    self.publish(error=str(e))
                    if failures >= 3:
                        self.publish(phase="error", playing=False, paused=False)
                continue
            try:
                self.handle(action, values)
            except Exception as e:
                self.publish(error=str(e), phase="error", playing=False, paused=False)
            finally:
                self.publish(pending_action="")


_lock = threading.Lock()
_sessions: dict[str, PlaybackSession] = {}


def start(body: dict):
    if body.get("source"):
        catalog.normalize_source(body["source"])
    if body.get("video_id"):
        safe_video_id(body["video_id"])
    session = PlaybackSession(body)
    with _lock:
        previous = _sessions.get(session.uuid)
        if previous:
            previous.cancelled.set()
        _sessions[session.uuid] = session
    threading.Thread(target=session.run, name="cast-session", daemon=True).start()
    return session.get()


def get(uuid: str):
    with _lock:
        session = _sessions.get(uuid)
    return session.get() if session else None


def control(uuid: str, session_id: str, action: str, **values):
    with _lock:
        session = _sessions.get(uuid)
    if not session or session.id != session_id:
        raise ValueError("電視播放已變更，請重新確認")
    return session.command(action, **values)


def cancel(uuid: str):
    with _lock:
        session = _sessions.pop(uuid, None)
    if session:
        session.cancelled.set()
