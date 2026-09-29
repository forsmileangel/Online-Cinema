import Hls from "hls.js";
import { api } from "../api";
import { useEffect, useRef, useState, type MouseEvent, type PointerEvent, type ReactNode, type WheelEvent } from "react";
import type { CastingController } from "../hooks/useCasting";

const VOL_KEY = "cinema.volume";
const QUALITY_KEY = "cinema.quality";

function loadQuality() {
  try {
    const height = Number(localStorage.getItem(QUALITY_KEY));
    if (Number.isFinite(height) && height > 0) return height;
  } catch { /* Storage may be disabled. */ }
  return -1;
}

function saveQuality(height: number) {
  try { localStorage.setItem(QUALITY_KEY, String(height)); } catch { /* ignore */ }
}

function fmt(t: number) {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = Math.floor(t % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function loadVol() {
  try {
    const n = Number(localStorage.getItem(VOL_KEY));
    if (Number.isFinite(n)) return Math.min(1, Math.max(0, n));
  } catch {
    /* ignore */
  }
  return 1;
}

function saveVol(n: number) {
  try {
    localStorage.setItem(VOL_KEY, String(n));
  } catch {
    /* ignore */
  }
}

export function Player({
  src,
  startAt = 0,
  onProgress,
  onEnded,
  onError,
  remote,
  returnPosition,
  favorited,
  onToggleFav,
  episodeControls,
}: {
  src: string;
  startAt?: number;
  onProgress?: (pos: number, dur: number) => void;
  onEnded?: () => void;
  onError?: (position: number) => void;
  remote?: CastingController;
  returnPosition?: number;
  favorited?: boolean;
  onToggleFav?: () => void;
  episodeControls?: ReactNode;
}) {
  const casting = !!remote?.active || !!remote?.restoring;
  const videoRef = useRef<HTMLVideoElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const castingRef = useRef(casting);
  castingRef.current = casting;
  const dragging = useRef(false);
  const clickWait = useRef<number | null>(null);
  const lastVol = useRef(loadVol() || 0.8);
  const [paused, setPaused] = useState(true);
  const [t, setT] = useState(0);
  const [d, setD] = useState(0);
  const [buf, setBuf] = useState(0);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const [hoverTime, setHoverTime] = useState(0);
  const [levels, setLevels] = useState<{ h: number; i: number }[]>([]);
  const [level, setLevel] = useState(-1);
  const [videoHeight, setVideoHeight] = useState(0);
  const [web720, setWeb720] = useState<{ src: string; url: string } | null>(null);
  const [qualityBusy, setQualityBusy] = useState(false);
  const [qualityError, setQualityError] = useState("");
  const qualityRequest = useRef<AbortController | null>(null);
  const qualityResume = useRef<{ position: number; paused: boolean } | null>(null);
  const usingWeb720 = web720?.src === src;
  const playbackSrc = usingWeb720 ? web720.url : src;
  const isMp4 = /\.mp4(\b|$)/i.test(decodeURIComponent(src));

  useEffect(() => () => { qualityRequest.current?.abort(); }, [src]);

  async function convert720(preservePlayback: boolean) {
    qualityRequest.current?.abort();
    const controller = new AbortController();
    qualityRequest.current = controller;
    setQualityBusy(true);
    setQualityError("");
    try {
      const prepared = await api.web720p(src, controller.signal);
      if (controller.signal.aborted || castingRef.current) return false;
      const video = videoRef.current;
      if (preservePlayback && video) qualityResume.current = { position: video.currentTime, paused: video.paused };
      saveQuality(720);
      setWeb720({ src, url: prepared.url });
      return true;
    } catch (e) {
      if (!controller.signal.aborted) setQualityError(e instanceof Error ? e.message : "720p 準備失敗，請重試");
      return false;
    } finally {
      if (qualityRequest.current === controller) setQualityBusy(false);
    }
  }
  const [playError, setPlayError] = useState("");
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const playbackPosition = useRef(startAt);
  const failureReported = useRef(false);
  const [loading, setLoading] = useState(true);
  const [show, setShow] = useState(true);
  const [vol, setVol] = useState(() => loadVol());
  const [muted, setMuted] = useState(() => loadVol() === 0);
  const [volumeDraft, setVolumeDraft] = useState<number | null>(null);
  const volumeDraftRef = useRef<number | null>(null);
  const lastRemoteVol = useRef(0.2);
  const [scrub, setScrub] = useState<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const duration = casting ? remote?.status?.duration || 0 : d;
  const currentTime = casting ? remote?.status?.current_time || 0 : t;
  const isPaused = casting ? !!remote?.status?.paused : paused;
  const canSeek = casting ? !!remote?.canSeek : Number.isFinite(duration) && duration > 0;
  const remoteLevel = remote?.status?.volume_level;
  const remoteMuted = !!remote?.status?.volume_muted;
  const hasRemoteVolume = !!remote?.status?.can_set_volume || !!remote?.status?.can_mute;

  function playbackFailed() {
    if (castingRef.current || failureReported.current) return;
    failureReported.current = true;
    setLoading(false);
    setPlayError("播放來源無法載入");
    onErrorRef.current?.(playbackPosition.current);
  }

  useEffect(() => {
    if (remoteLevel != null && remoteLevel > 0) lastRemoteVol.current = remoteLevel;
  }, [remoteLevel]);

  useEffect(() => {
    volumeDraftRef.current = null;
    setVolumeDraft(null);
    lastRemoteVol.current = remoteLevel != null && remoteLevel > 0 ? remoteLevel : 0.2;
  }, [remote?.active?.uuid, remote?.active?.session_id]);

  useEffect(() => {
    if (!remote?.busy && volumeDraftRef.current == null) setVolumeDraft(null);
  }, [remote?.busy, remoteLevel]);

  function changeVolume(next: number) {
    if (!casting) { applyVol(next); return; }
    if (!remote?.canSetVolume) return;
    volumeDraftRef.current = next;
    setVolumeDraft(next);
  }

  function commitVolume() {
    const next = volumeDraftRef.current;
    volumeDraftRef.current = null;
    if (next == null) return;
    if (!remote?.canSetVolume) { setVolumeDraft(null); return; }
    // Commit once on release, rather than queueing a request per slider pixel.
    void remote.setVolume(next);
  }

  useEffect(() => {
    dragging.current = false;
    setScrub(null);
    setHoverX(null);
  }, [src, casting, canSeek]);

  function applyVol(next: number, mute = false) {
    const v = videoRef.current;
    const n = Math.min(1, Math.max(0, next));
    if (v) {
      v.volume = n;
      v.muted = mute || n === 0;
    }
    setVol(n);
    setMuted(mute || n === 0);
    if (n > 0) lastVol.current = n;
    saveVol(mute ? 0 : n);
  }

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src.startsWith("/api/hls")) return;
    setPlayError("");
    failureReported.current = false;
    setQualityError("");
    setLoading(true);
    setVideoHeight(0);
    if (!usingWeb720) setLevels([]);
    setLevel(usingWeb720 ? -4 : -1);
    let hls: Hls | null = null;
    let nativeMetadata: (() => void) | undefined;
    const resume = qualityResume.current;
    qualityResume.current = null;
    const start = resume?.position ?? Math.max(0, startAt);
    playbackPosition.current = start;
    const beginPlayback = () => {
      if (resume?.paused) { setLoading(false); return; }
      if (!castingRef.current) void video.play().catch(() => { setLoading(false); setPlayError("瀏覽器暫停了自動播放，請按播放繼續。"); });
    };
    video.volume = vol;
    video.muted = muted;
    if (isMp4) {
      video.src = playbackSrc;
      const onMeta = () => {
        if (start) video.currentTime = start;
        beginPlayback();
      };
      video.addEventListener("loadedmetadata", onMeta, { once: true });
      return () => {
        video.removeEventListener("loadedmetadata", onMeta);
        video.removeAttribute("src");
      };
    }
    if (Hls.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        autoStartLoad: false,
        lowLatencyMode: false,
        startFragPrefetch: true,
        testBandwidth: false,
        maxBufferLength: 60,
        maxMaxBufferLength: 180,
        maxBufferSize: 80 * 1000 * 1000,
        backBufferLength: 45,
        abrEwmaDefaultEstimate: 8_000_000,
        fragLoadPolicy: { default: {
          ...Hls.DefaultConfig.fragLoadPolicy.default,
          // The proxy may retry over another adapter before sending a segment.
          maxTimeToFirstByteMs: usingWeb720 ? 50000 : 22000,
          timeoutRetry: { ...Hls.DefaultConfig.fragLoadPolicy.default.timeoutRetry!, maxNumRetry: 2 },
          errorRetry: { ...Hls.DefaultConfig.fragLoadPolicy.default.errorRetry!, maxNumRetry: 2 },
        } },
        xhrSetup(xhr) {
          xhr.withCredentials = false;
        },
      });
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        const instance = hls!;
        const ls = instance.levels.map((l, i) => ({ h: l.height || 0, i })).filter((x) => x.h);
        if (!usingWeb720) setLevels(ls);
        const preferred = loadQuality();
        const matching = ls.filter((l) => l.h <= preferred).sort((a, b) => b.h - a.h)[0];
        const begin = (selected: number) => {
          if (hlsRef.current !== instance) return;
          instance.loadLevel = selected;
          setLevel(usingWeb720 ? -4 : selected);
          instance.startLoad(start);
          beginPlayback();
        };
        if (!usingWeb720 && preferred === 720 && !matching) {
          void convert720(false).then((converted) => { if (!converted) begin(-1); });
        } else {
          begin(!usingWeb720 && preferred > 0 ? matching?.i ?? ls[0]?.i ?? -1 : -1);
        }
      });
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data.fatal) playbackFailed();
      });
      hlsRef.current = hls;
      hls.loadSource(playbackSrc);
      hls.attachMedia(video);
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = playbackSrc;
      nativeMetadata = () => {
        if (start) video.currentTime = start;
        beginPlayback();
      };
      video.addEventListener("loadedmetadata", nativeMetadata, { once: true });
    }
    return () => {
      if (nativeMetadata) video.removeEventListener("loadedmetadata", nativeMetadata);
      hls?.destroy();
      hlsRef.current = null;
    };
    // vol/muted applied once when attaching; later changes go through applyVol
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, startAt, playbackSrc]);

  useEffect(() => {
    const v = videoRef.current;
    if (casting) {
      qualityRequest.current?.abort();
      setQualityBusy(false);
      v?.pause();
    }
  }, [casting]);

  useEffect(() => {
    const v = videoRef.current;
    if (v && returnPosition != null && !casting) v.currentTime = returnPosition;
  }, [returnPosition, casting]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const onTime = () => {
      if (castingRef.current && !video.paused) video.pause();
      setT(video.currentTime);
      setD(video.duration || 0);
      if (video.buffered.length) {
        setBuf(video.buffered.end(video.buffered.length - 1));
      }
      setPaused(video.paused);
      setLoading(video.readyState < 3 && !video.paused);
      if (!castingRef.current) {
        if (Number.isFinite(video.duration) && video.duration > 0) playbackPosition.current = video.currentTime;
        onProgress?.(video.currentTime, video.duration || 0);
        if (!video.paused && video.duration > 1 && video.currentTime >= video.duration - 0.2) onEnded?.();
      }
    };
    video.addEventListener("timeupdate", onTime);
    video.addEventListener("progress", onTime);
    video.addEventListener("play", onTime);
    video.addEventListener("pause", onTime);
    const onWaiting = () => setLoading(true);
    const onPlaying = () => setLoading(false);
    const onVolume = () => {
      setVol(video.volume);
      setMuted(video.muted || video.volume === 0);
    };
    video.addEventListener("waiting", onWaiting);
    video.addEventListener("playing", onPlaying);
    video.addEventListener("volumechange", onVolume);
    const onEnd = () => {
      if (!castingRef.current) onEnded?.();
    };
    video.addEventListener("ended", onEnd);
    return () => {
      video.removeEventListener("timeupdate", onTime);
      video.removeEventListener("progress", onTime);
      video.removeEventListener("play", onTime);
      video.removeEventListener("pause", onTime);
      video.removeEventListener("waiting", onWaiting);
      video.removeEventListener("playing", onPlaying);
      video.removeEventListener("volumechange", onVolume);
      video.removeEventListener("ended", onEnd);
    };
  }, [onProgress, onEnded]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (["INPUT", "SELECT", "TEXTAREA", "BUTTON", "A"].includes(tag)) return;
      if (casting) {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          e.preventDefault();
          if (canSeek) seekTo((currentTime + (e.key === "ArrowRight" ? 10 : -10)) / duration);
        } else if (e.key === " " || e.code === "Space" || e.key === "MediaPlayPause" || e.key === "Enter") {
          e.preventDefault();
          togglePlay();
        } else if (e.key === "m" || e.key === "M") {
          e.preventDefault();
          toggleMute();
        }
        return;
      }
      const v = videoRef.current;
      if (!v) return;
      if (e.key === " " || e.code === "Space" || e.key === "MediaPlayPause" || e.key === "MediaPlay" || e.key === "MediaPause" || e.key === "Enter") {
        e.preventDefault();
        if (e.key === "MediaPause") v.pause();
        else if (e.key === "MediaPlay") void v.play();
        else v.paused ? void v.play() : v.pause();
      } else if (e.key === "ArrowRight") {
        v.currentTime = Math.min(v.duration || 0, v.currentTime + 10);
      } else if (e.key === "ArrowLeft") {
        v.currentTime = Math.max(0, v.currentTime - 10);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        applyVol((v.muted ? 0 : v.volume) + 0.05);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        applyVol((v.muted ? 0 : v.volume) - 0.05);
      } else if (e.key === "f" || e.key === "F") {
        toggleFs();
      } else if (e.key === "m" || e.key === "M") {
        toggleMute();
      } else if (e.key === "[") {
        changeLevel(-2);
      } else if (e.key === "]") {
        changeLevel(-3);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function ratioFromEvent(e: PointerEvent) {
    const el = barRef.current;
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  }

  function seekTo(ratio: number) {
    if (!canSeek) return;
    const position = Math.min(1, Math.max(0, ratio)) * duration;
    if (casting) {
      void remote?.control("seek", position);
      return;
    }
    const v = videoRef.current;
    if (!v || !v.duration) return;
    v.currentTime = position;
  }

  function onBarDown(e: PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    if (!canSeek) return;
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    const ratio = ratioFromEvent(e);
    setHoverX(ratio);
    setHoverTime(ratio * duration);
    setScrub(ratio);
  }
  function onBarMove(e: PointerEvent<HTMLDivElement>) {
    if (!canSeek) return;
    const ratio = ratioFromEvent(e);
    setHoverX(ratio);
    setHoverTime(ratio * duration);
    if (dragging.current) setScrub(ratio);
  }
  function onBarUp(e: PointerEvent<HTMLDivElement>) {
    e.stopPropagation();
    if (!dragging.current) return;
    const ratio = ratioFromEvent(e);
    dragging.current = false;
    setScrub(null);
    setHoverX(null);
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    seekTo(ratio);
  }

  function cancelScrub() {
    dragging.current = false;
    setScrub(null);
    setHoverX(null);
  }

  function togglePlay() {
    if (casting) {
      if (remote?.canControl) void remote.control(isPaused ? "resume" : "pause");
      return;
    }
    const v = videoRef.current;
    if (!v) return;
    v.paused ? void v.play() : v.pause();
  }

  function toggleMute() {
    if (casting) {
      if (!remote?.canMute) return;
      if (remoteLevel === 0 && remote.canSetVolume) void remote.setVolume(lastRemoteVol.current);
      else void remote.setMuted(!remoteMuted);
      return;
    }
    const v = videoRef.current;
    if (!v) return;
    if (!v.muted && v.volume > 0) {
      applyVol(v.volume, true);
    } else {
      applyVol(lastVol.current > 0.02 ? lastVol.current : 0.8, false);
    }
  }

  function toggleFs() {
    const wrap = videoRef.current?.closest<HTMLElement>(".player-screen") || videoRef.current?.parentElement;
    if (!wrap) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void wrap.requestFullscreen();
  }

  function changeLevel(next: number) {
    const hls = hlsRef.current;
    if (!hls || qualityBusy || casting) return;
    if (next === -2 || next === -3) {
      const current = levels.findIndex((l) => l.i === level);
      const index = Math.max(-1, Math.min(levels.length - 1, current + (next === -2 ? -1 : 1)));
      next = levels[index]?.i ?? -1;
    }
    if (next === -4) { void convert720(true); return; }
    saveQuality(levels.find((l) => l.i === next)?.h ?? -1);
    setQualityError("");
    if (usingWeb720) {
      const video = videoRef.current;
      if (video) qualityResume.current = { position: video.currentTime, paused: video.paused };
      setWeb720(null);
    } else {
      hls.nextLevel = next;
      setLevel(next);
    }
  }

  function bumpUi() {
    setShow(true);
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      if (!paused) setShow(false);
    }, 2200);
  }

  function onSurfaceClick(e: MouseEvent<HTMLDivElement>) {
    const el = e.target as HTMLElement;
    if (el.closest(".controls")) return;
    if (clickWait.current) {
      window.clearTimeout(clickWait.current);
      clickWait.current = null;
      return;
    }
    clickWait.current = window.setTimeout(() => {
      clickWait.current = null;
      togglePlay();
    }, 220);
  }

  function onSurfaceDblClick(e: MouseEvent<HTMLDivElement>) {
    e.preventDefault();
    const el = e.target as HTMLElement;
    if (el.closest(".controls")) return;
    if (clickWait.current) {
      window.clearTimeout(clickWait.current);
      clickWait.current = null;
    }
    toggleFs();
  }

  function onWheel(e: WheelEvent<HTMLDivElement>) {
    if (casting) return;
    const v = videoRef.current;
    if (!v) return;
    e.preventDefault();
    const cur = v.muted ? 0 : v.volume;
    applyVol(cur + (e.deltaY < 0 ? 0.05 : -0.05));
  }

  const shownT = scrub != null && duration ? scrub * duration : currentTime;
  const playPct = duration ? Math.min(100, Math.max(0, shownT / duration * 100)) : 0;
  const bufPct = !casting && duration ? (buf / duration) * 100 : 0;
  const shownVol = casting ? volumeDraft ?? (remoteMuted ? 0 : remoteLevel ?? 0) : muted ? 0 : vol;
  const shownMuted = casting ? remoteMuted : muted;
  const castQuality = new URLSearchParams(remote?.status?.content_id.split("?")[1] || "").get("nesthub") === "1"
    ? "最高 720p" : levels.length === 1 ? `${levels[0].h}p` : "投放自動";

  return (
    <div
      className={`player ${show || isPaused || casting ? "show" : ""}`}
      onMouseMove={bumpUi}
      onClick={onSurfaceClick}
      onDoubleClick={onSurfaceDblClick}
      onWheel={onWheel}
    >
      <video ref={videoRef} playsInline onResize={(e) => setVideoHeight(e.currentTarget.videoHeight)} onLoadedMetadata={(e) => setVideoHeight(e.currentTarget.videoHeight)} onPlay={() => setPlayError("")} onError={playbackFailed} />
      {!casting && (qualityBusy || qualityError) ? <div className="loading-pill" role="status">{qualityBusy ? "正在準備 720p…" : qualityError}</div> : null}
      {playError && !casting && !qualityBusy && !qualityError ? <div className="loading-pill" role="status">{playError}</div> : null}
      {isPaused ? (
        <div className="center-play">
          <span>▶</span>
        </div>
      ) : null}
      {!qualityBusy && !qualityError && (casting ? remote?.busy || remote?.status?.buffering : loading) ? <div className="loading-pill">{casting ? "等待電視確認" : "載入中"}</div> : null}
      <div className="overlay">
        <div className="controls">
          <div
            className="seek"
            ref={barRef}
            role="slider"
            tabIndex={canSeek ? 0 : -1}
            aria-label={casting ? "電視播放進度" : "播放進度"}
            aria-disabled={!canSeek}
            aria-valuemin={0}
            aria-valuemax={Number.isFinite(duration) ? duration : 0}
            aria-valuenow={Number.isFinite(shownT) ? shownT : 0}
            aria-valuetext={`${fmt(shownT)} / ${fmt(duration)}`}
            title={!canSeek && casting ? "等待電視連線與影片長度確認後即可跳轉" : undefined}
            onPointerDown={onBarDown}
            onPointerMove={onBarMove}
            onPointerUp={onBarUp}
            onPointerCancel={cancelScrub}
            onLostPointerCapture={cancelScrub}
            onKeyDown={(e) => {
              if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
              e.preventDefault();
              e.stopPropagation();
              if (canSeek) seekTo(e.key === "Home" ? 0 : e.key === "End" ? 1 : (currentTime + (e.key === "ArrowRight" ? 10 : -10)) / duration);
            }}
            onPointerLeave={() => { if (!dragging.current) setHoverX(null); }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="seek-track">
              <div className="seek-buf" style={{ width: `${bufPct}%` }} />
              <div className="seek-play" style={{ width: `${playPct}%` }} />
              <div className="seek-knob" style={{ left: `${playPct}%` }} />
            </div>
            {hoverX != null ? (
              <div className="seek-tip" style={{ left: `${hoverX * 100}%` }}>
                {fmt(hoverTime)}
              </div>
            ) : null}
          </div>
          <div className="ctrl-row">
            <button type="button" disabled={casting && !remote?.canControl} aria-label={casting ? isPaused ? "電視續播" : "電視暫停" : isPaused ? "播放" : "暫停"} onClick={(e) => { e.stopPropagation(); togglePlay(); }}>
              {isPaused ? "▶" : "❚❚"}
            </button>
            <span className="times">
              {casting ? "電視 " : ""}{fmt(shownT)} / {fmt(duration)}
            </span>
            {!casting || hasRemoteVolume ? <div className="vol" onClick={(e) => e.stopPropagation()}
              title={casting ? remote?.status?.volume_scope === "stream" ? "影片音量；電視本身音量仍由遙控器調整" : "投放裝置音量" : "本機音量"}>
              <button type="button" disabled={casting && !remote?.canMute} onClick={toggleMute}
                aria-label={casting ? shownMuted || shownVol === 0 ? "取消投放靜音" : "投放靜音" : muted ? "取消靜音" : "靜音"} title="靜音 (M)">
                {shownMuted || shownVol === 0 ? "靜音" : "聲音"}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={shownVol}
                aria-label={casting ? "投放音量" : "音量"}
                disabled={casting && !remote?.canSetVolume}
                onChange={(e) => changeVolume(Number(e.target.value))}
                onPointerUp={commitVolume}
                onKeyUp={commitVolume}
                onBlur={commitVolume}
                onPointerCancel={() => { volumeDraftRef.current = null; setVolumeDraft(null); }}
              />
              <span className="times">{Math.round(shownVol * 100)}</span>
            </div> : <span className="times">{remote?.uncertain ? "音量暫不可用" : remote?.status?.can_set_volume === false ? "音量請用遙控器" : "讀取投放音量…"}</span>}
            <span className="spacer" />
            {casting ? <span className="quality-status" aria-label="投放畫質" title="投放端處理畫質；本機畫質選單只適用於本機播放">{castQuality}</span> : !isMp4 && Hls.isSupported() ? <select
              className="field"
              aria-label="播放畫質"
              title="記住畫質供下次播放使用；720p 轉換需要電腦處理，部分片源不支援"
              disabled={qualityBusy}
              value={level}
              onChange={(e) => changeLevel(Number(e.target.value))}
              onClick={(e) => e.stopPropagation()}
            >
              <option value={-1}>{levels.length > 1 ? "自動" : !usingWeb720 && videoHeight ? `來源 ${videoHeight}p` : "來源畫質"}</option>
              {levels.map((l) => <option key={l.i} value={l.i}>{l.h}p</option>)}
              {!levels.some((l) => l.h <= 720) ? <option value={-4}>720p（轉換）</option> : null}
            </select> : <span className="quality-status" aria-label="播放畫質">{videoHeight ? `來源 ${videoHeight}p` : "來源畫質"}</span>}
            <div className="fs-col" onClick={(e) => e.stopPropagation()}>
              <button type="button" onClick={(e) => { e.stopPropagation(); toggleFs(); }}>
                全螢幕
              </button>
              {onToggleFav ? (
                <button
                  type="button"
                  className={favorited ? "on" : ""}
                  data-tv="1"
                  aria-pressed={!!favorited}
                  aria-label={favorited ? "取消收藏" : "收藏"}
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleFav();
                  }}
                >
                  {favorited ? "已收藏" : "收藏"}
                </button>
              ) : null}
            </div>
          </div>
          {episodeControls}
        </div>
      </div>
    </div>
  );
}
