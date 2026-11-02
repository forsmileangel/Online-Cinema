"""Server-owned playback: browser lifetime never controls episode progression."""
from __future__ import annotations

import copy
import json
import queue
import threading
import time
import uuid as uuid_module
from urllib.parse import parse_qs, urlparse

from . import cast, cast_quality, catalog, db, hls_proxy, sites, next_buffer, offline, read_ahead
from .security import SiteBusy, assert_hls_url, safe_video_id

SERIES_SOURCES = {"hongguo", "chinaq", "gimy", "dramaq", "mmov", "mvffm"}
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
        self.last_checkpoint = 0.0
        self.checkpoint_state = None
        self.prefetched = None
        self.prefetching = False
        # Stream choice for the receiver: highest rendition, 720p only when it keeps stalling.
        self.renditions = None
        self.upstream = ""
        self.mime = ""
        self.media_title = ""
        self.governor = cast_quality.Governor(time.monotonic())
        self.snapshot = dict(uuid=self.uuid, session_id=self.id, content_id="", playing=False, paused=False,
                             idle=False, buffering=False, current_time=body.get("position_sec", 0), duration=0,
                             phase="waking" if body.get("wake") else "loading", error="", title=body.get("title", ""),
                             source=body.get("source", ""), video_id=body.get("video_id", ""),
                             episode_id=body.get("episode_id", ""), autoplay_next=bool(body.get("autoplay_next")), playlist=body.get("url", ""), pending_action="",
                             warning="", quality="source", quality_label="", quality_reason="", buffer_ahead=0)

    def get(self):
        with self.lock:
            return copy.deepcopy(self.snapshot)

    def publish(self, **values):
        with self.lock:
            if not self.cancelled.is_set():
                self.snapshot.update(values)
        self.warm_next()
        self.checkpoint()

    def checkpoint(self):
        # Only the registered owner may save; cancelled workers cannot revive it.
        with _lock:
            if _sessions.get(self.uuid) is not self or self.cancelled.is_set():
                return
            snapshot = self.get()
            phase = snapshot["phase"]
            if phase in {"loading", "waking", "reconnecting"}:
                return
            state = (phase, snapshot["content_id"], snapshot["episode_id"], snapshot["autoplay_next"])
            now = time.monotonic()
            if state == self.checkpoint_state and now - self.last_checkpoint < 5:
                return
            saved = "" if phase in {"stopped", "ended", "replaced"} else json.dumps({
                "body": self.body, "snapshot": snapshot, "episode_ids": self.episode_ids, "epoch": self.epoch,
            }, ensure_ascii=False)
            db.set_setting("cast_session:" + self.uuid, saved)
            self.last_checkpoint, self.checkpoint_state = now, state

    def warm_next(self):
        owner = "cast-" + self.id
        if not self.valid() or not self.prefetched or not self.snapshot["autoplay_next"] or self.snapshot["phase"] in TERMINAL or self.prefetched[2].playlist.startswith("/api/offline/media/"):
            next_buffer.cancel(owner)
            return
        state = self.snapshot
        ready = bool(not state.get("buffering") and (state.get("paused") or (state.get("playing") and state.get("current_time", 0) >= 30)))
        try:
            next_buffer.update(owner, self.prefetched[2].playlist, ready,
                               720 if "nesthub=1" in state.get("content_id", "") else 0)
        except Exception:
            pass  # A warm-up failure cannot stop receiver playback.

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
                detail = offline.fetch_detail(self.body["source"], self.body["video_id"], target)
                if self.valid() and self.epoch == epoch:
                    self.prefetched = (target, time.monotonic(), detail)
                    self.warm_next()
            except Exception:
                pass  # Retry on demand; a broken next episode cannot stop this one.
            finally:
                self.prefetching = False
        threading.Thread(target=resolve, name="cast-prefetch", daemon=True).start()

    def _load(self, episode: str, position=0):
        if not self.valid():
            return
        self.publish(phase="loading", error="", warning="", playing=False, paused=False, idle=False, buffering=False,
                     episode_id=episode, current_time=position, duration=0, playlist="")
        self.previous = None
        self.epoch += 1
        playlist = self.body.get("url", "")
        title = self.body.get("title", "")
        source = self.body.get("source")
        local = offline.local_detail(source, self.body["video_id"], episode or None) if self.body.get("video_id") else None
        if local or (source in SERIES_SOURCES and self.body.get("video_id")):
            cached = self.prefetched
            self.prefetched = None
            detail = local or (cached[2] if cached and cached[0] == episode and time.monotonic() - cached[1] < 120 else offline.fetch_detail(source, self.body["video_id"], episode or None))
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
        if source and self.body.get("video_id"):
            offline.playback(source, self.body["video_id"], episode)
        local_key = offline.media_key(playlist) if playlist.startswith("/api/offline/media/") else None
        parsed = urlparse(playlist)
        upstream = parse_qs(parsed.query).get("u", [])
        if not local_key and (parsed.scheme or parsed.netloc or parsed.path != "/api/hls" or len(upstream) != 1):
            raise ValueError("請從影片頁選擇可播放的串流")
        upstream = assert_hls_url(upstream[0]) if not local_key else ""
        if not self.valid():
            return
        origin = cast.lan_media_origin(self.uuid)
        cast.check_media_origin(origin)
        mime = "video/mp4" if local_key or urlparse(upstream).path.lower().endswith(".mp4") else "application/vnd.apple.mpegurl"
        marker = f"{self.id}-{self.epoch}"
        self.renditions, self.upstream, self.mime, self.media_title = None, upstream, mime, title
        if not local_key and mime == "application/vnd.apple.mpegurl" and not cast.is_nest_hub(self.uuid):
            try:
                self.renditions = hls_proxy.renditions(upstream, time.monotonic() + 15)
            except Exception:
                self.renditions = None  # The receiver gets the original playlist.
        best = (self.renditions or {}).get("best")
        read_ahead.add_receiver(cast.device_host(self.uuid))  # This computer reads ahead for the receiver.
        url = origin + offline.media_url(local_key) if local_key else hls_proxy.proxied_media(best["url"] if best else upstream, origin)
        self.governor.reset(time.monotonic())
        self.publish(playlist=playlist, title=title, episode_id=episode, warning="", quality="source",
                     quality_label=f"{best['height']}p" if best and best["height"] else "", quality_reason="", buffer_ahead=0)
        try:
            state = cast.play(url, mime, title, position, self.uuid, guard=self.valid, marker=marker, expected=cast.session_content(self.uuid, self.id) or self.snapshot["content_id"])
        except RuntimeError:
            fallback = (self.renditions or {}).get("full_hd")
            if not fallback or not self.valid():
                raise
            # A receiver without 4K decoding refuses the top rendition; 1080p still plays.
            self.renditions["best"] = fallback
            self.publish(quality_label=f"{fallback['height']}p")
            url = hls_proxy.proxied_media(fallback["url"], origin)
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
        ahead = read_ahead.status(self.receiver_marker())
        self.publish(**state, phase="paused" if state.get("paused") else "buffering" if state.get("buffering") else "playing", error="",
                     buffer_ahead=round(ahead["lead_seconds"]) if ahead else 0)
        self.save(state)
        verdict = self.governor.observe(state, ahead, time.monotonic())
        if verdict:
            self.downshift(verdict)

    def receiver_marker(self) -> str:
        """Read-ahead is kept per receiver, found by its address."""
        host = cast.device_host(self.uuid)
        read_ahead.add_receiver(host)
        return host

    def downshift(self, cause: str):
        """Drop to 720p only where that helps: a lower source rendition, or a TV-side bottleneck."""
        lower = (self.renditions or {}).get("lower")
        if cause == "upstream" and not lower:
            # A 720p conversion still needs every source byte; it cannot help a slow source.
            self.publish(quality_reason=cause, warning="片源速度跟不上，電腦已盡量預先緩衝；這部建議先離線下載")
            return
        try:
            label = self._reload(lower["url"] if lower else None, convert=not lower)
        except Exception as e:
            self.publish(warning=f"改用 720p 失敗，維持原畫質：{e}")
            return
        self.publish(quality="720p", quality_label=label or "720p", quality_reason=cause,
                     warning="片源速度不穩，已暫時改用 720p 播放" if cause == "upstream" else "電視端網路較慢，已暫時改用 720p 播放")

    def restore_quality(self):
        best = (self.renditions or {}).get("best")
        label = self._reload(best["url"] if best else None)
        self.governor.stop()  # The viewer chose this quality for the rest of the episode.
        self.publish(quality="source", quality_label=label, quality_reason="", warning="")

    def _reload(self, target: str | None, convert: bool = False) -> str:
        """Play another rendition from the current position; returns its label."""
        if not self.valid() or not self.upstream:
            raise ValueError("目前的串流無法切換畫質")
        position = float(self.snapshot.get("current_time") or 0)
        origin = cast.lan_media_origin(self.uuid)
        expected = cast.session_content(self.uuid, self.id) or self.snapshot["content_id"]
        best = (self.renditions or {}).get("best")
        source = target or (best["url"] if best else self.upstream)
        self.epoch += 1
        marker = f"{self.id}-{self.epoch}"
        url = hls_proxy.proxied_media(source, origin)
        transcoded = False
        if convert:
            # Same 720p path as the browser's: a 720p rendition when listed, else converted here.
            url = hls_proxy.nesthub_media_url(url, time.monotonic() + 25, strict=True)
            transcoded = parse_qs(urlparse(url).query).get("nesthub") == ["1"]
            if transcoded:
                url += "&web=1"
        primed = parse_qs(urlparse(url).query).get("u", [source])[0]
        try:
            read_ahead.prime(self.receiver_marker(), primed, position, convert=transcoded, web=transcoded)
        except Exception:
            pass  # The receiver's own requests open the window.
        state = cast.play(url, self.mime, self.media_title, position, self.uuid, guard=self.valid, marker=marker, expected=expected)
        self.previous = state
        self.governor.reset(time.monotonic())
        self.publish(**state, phase="paused" if state.get("paused") else "playing", error="")
        self.prefetch()
        if transcoded:
            return "720p（電腦轉檔）"
        heights = [r["height"] for r in (self.renditions or {}).values() if r and r["url"] == source and r["height"]]
        return f"{heights[0]}p" if heights else ("720p" if convert else "")

    def command(self, action: str, **values):
        if action not in {"pause", "resume", "seek", "stop", "episode", "autoplay", "retry", "detach", "volume", "mute", "quality"}:
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
        if action == "quality":
            self.restore_quality()
            return
        if action == "seek":
            self.governor.seeked(time.monotonic())
        expected = cast.session_content(self.uuid, self.id) or self.snapshot["content_id"]
        if action == "stop" and not expected:
            self.publish(phase="stopped", playing=False, paused=False)
            return
        audio = {key: values[key] for key in ("volume_level", "muted") if key in values} if action in {"volume", "mute"} else {}
        state = cast.control(action, values.get("position_sec"), self.uuid, expected,
                             guard=lambda: not self.cancelled.is_set(), **audio)
        self.save(self.previous if action == "stop" and self.previous else state, True)
        self.previous = state
        self.publish(**state, phase="stopped" if action == "stop" else "paused" if state.get("paused") else "playing", error="")

    def run(self, reconnect=False):
        failures = 0
        try:
            if reconnect:
                state = cast.reconnect(self.uuid, self.snapshot["content_id"], self.id, self.snapshot["title"])
                self.previous = state
                self.publish(**state, phase="paused" if state["paused"] else "buffering" if state["buffering"] else "playing", error="")
            else:
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
                if action in {"volume", "mute"}:
                    self.publish(error=str(e))
                else:
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
        db.set_setting("cast_session:" + session.uuid, "")
    threading.Thread(target=session.run, name="cast-session", daemon=True).start()
    return session.get()


def get(uuid: str):
    restored = False
    with _lock:
        session = _sessions.get(uuid)
        if not session:
            saved = db.get_setting("cast_session:" + uuid, "")
            if not saved:
                return None
            try:
                saved = json.loads(saved)
                session = PlaybackSession(saved["body"])
                session.snapshot.update(saved["snapshot"])
                if session.uuid != uuid or not session.snapshot["content_id"] or session.snapshot["phase"] in {"stopped", "ended", "replaced"}:
                    return None
                session.id = session.snapshot["session_id"]
                session.episode_ids = saved["episode_ids"]
                session.epoch = saved["epoch"]
            except (ValueError, KeyError, TypeError):
                return None
            session.snapshot.update(phase="reconnecting", pending_action="", error="")
            _sessions[uuid] = session
            restored = True
    if restored:
        threading.Thread(target=session.run, kwargs={"reconnect": True}, name="cast-reconnect", daemon=True).start()
    return session.get()


FINISHED = frozenset({"stopped", "ended", "replaced", "error"})


def active_count() -> int:
    """TVs this server is currently playing to or starting; a restart would cut them off."""
    with _lock:
        sessions = list(_sessions.values())
    return sum(1 for s in sessions if s.get().get("phase") not in FINISHED)


def control(uuid: str, session_id: str, action: str, **values):
    get(uuid)
    with _lock:
        session = _sessions.get(uuid)
    if not session or session.id != session_id:
        raise ValueError("電視播放已變更，請重新確認")
    return session.command(action, **values)


def cancel(uuid: str):
    with _lock:
        session = _sessions.pop(uuid, None)
        db.set_setting("cast_session:" + uuid, "")
    if session:
        session.cancelled.set()
