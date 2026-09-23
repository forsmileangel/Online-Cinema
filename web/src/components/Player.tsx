import Hls from "hls.js";
import { useEffect, useRef, useState, type MouseEvent, type PointerEvent, type WheelEvent } from "react";
import type { CastingController } from "../hooks/useCasting";

const VOL_KEY = "cinema.volume";

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
  episodeTitle,
  startAt = 0,
  onProgress,
  onEnded,
  onError,
  remote,
  returnPosition,
  favorited,
  onToggleFav,
}: {
  src: string;
  episodeTitle?: string;
  startAt?: number;
  onProgress?: (pos: number, dur: number) => void;
  onEnded?: () => void;
  onError?: () => void;
  remote?: CastingController;
  returnPosition?: number;
  favorited?: boolean;
  onToggleFav?: () => void;
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
  const [playError, setPlayError] = useState("");
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const [loading, setLoading] = useState(true);
  const [show, setShow] = useState(true);
  const [vol, setVol] = useState(() => loadVol());
  const [muted, setMuted] = useState(() => loadVol() === 0);
  const [scrub, setScrub] = useState<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const duration = casting ? remote?.status?.duration || 0 : d;
  const currentTime = casting ? remote?.status?.current_time || 0 : t;
  const isPaused = casting ? !!remote?.status?.paused : paused;
  const canSeek = casting ? !!remote?.canSeek : Number.isFinite(duration) && duration > 0;

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
    setLoading(true);
    let hls: Hls | null = null;
    let nativeMetadata: (() => void) | undefined;
    const start = startAt > 5 ? startAt : 0;
    video.volume = vol;
    video.muted = muted;
    const raw = decodeURIComponent(src);
    if (/\.mp4(\b|$)/i.test(raw)) {
      video.src = src;
      const onMeta = () => {
        if (start) video.currentTime = start;
        if (!castingRef.current) void video.play().catch(() => { setLoading(false); setPlayError("瀏覽器暫停了自動播放，請按播放繼續。"); });
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
        lowLatencyMode: false,
        startFragPrefetch: true,
        testBandwidth: false,
        maxBufferLength: 60,
        maxMaxBufferLength: 180,
        maxBufferSize: 80 * 1000 * 1000,
        backBufferLength: 45,
        abrEwmaDefaultEstimate: 8_000_000,
        xhrSetup(xhr) {
          xhr.withCredentials = false;
        },
      });
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        const ls = hls!.levels
          .map((l, i) => ({ h: l.height || 0, i }))
          .filter((x) => x.h);
        setLevels(ls);
        if (start) video.currentTime = start;
        if (!castingRef.current) void video.play().catch(() => { setLoading(false); setPlayError("瀏覽器暫停了自動播放，請按播放繼續。"); });
      });
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data.fatal && !castingRef.current) { setLoading(false); setPlayError("播放來源無法載入"); onErrorRef.current?.(); }
      });
      hlsRef.current = hls;
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src;
      nativeMetadata = () => {
        if (start) video.currentTime = start;
        if (!castingRef.current) void video.play().catch(() => { setLoading(false); setPlayError("瀏覽器暫停了自動播放，請按播放繼續。"); });
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
  }, [src, startAt]);

  useEffect(() => {
    const v = videoRef.current;
    if (casting) v?.pause();
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
    if (!hls) return;
    if (next === -2) {
      const cur = hls.currentLevel;
      const i = Math.max(-1, cur - 1);
      hls.currentLevel = i;
      setLevel(i);
      return;
    }
    if (next === -3) {
      const cur = hls.currentLevel < 0 ? hls.levels.length - 1 : hls.currentLevel;
      const i = Math.min(hls.levels.length - 1, cur + 1);
      hls.currentLevel = i;
      setLevel(i);
      return;
    }
    hls.currentLevel = next;
    setLevel(next);
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
  const shownVol = muted ? 0 : vol;

  return (
    <div
      className={`player ${show || isPaused || casting ? "show" : ""}`}
      onMouseMove={bumpUi}
      onClick={onSurfaceClick}
      onDoubleClick={onSurfaceDblClick}
      onWheel={onWheel}
    >
      <video ref={videoRef} playsInline onPlay={() => setPlayError("")} onError={() => { if (!castingRef.current) { setLoading(false); setPlayError("播放來源無法載入"); onErrorRef.current?.(); } }} />
      {playError && !casting ? <div className="loading-pill" role="status">{playError}</div> : null}
      {isPaused ? (
        <div className="center-play">
          <span>▶</span>
        </div>
      ) : null}
      {(casting ? remote?.busy || remote?.status?.buffering : loading) ? <div className="loading-pill">{casting ? "等待電視確認" : "載入中"}</div> : null}
      <div className="overlay">
        <div className="controls">
          {episodeTitle ? <div className="current-episode" aria-live="polite">目前播放：{episodeTitle}</div> : null}
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
            <div className="vol" onClick={(e) => e.stopPropagation()}>
              <button type="button" disabled={casting} onClick={toggleMute} title={casting ? "請使用電視調整音量" : "靜音 (M)"}>
                {muted || shownVol === 0 ? "靜音" : "聲音"}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={shownVol}
                aria-label="音量"
                disabled={casting}
                onChange={(e) => applyVol(Number(e.target.value))}
              />
              <span className="times">{Math.round(shownVol * 100)}</span>
            </div>
            <span className="spacer" />
            <select
              className="field"
              disabled={casting}
              value={level}
              onChange={(e) => changeLevel(Number(e.target.value))}
              onClick={(e) => e.stopPropagation()}
            >
              <option value={-1}>Auto</option>
              {levels.map((l) => (
                <option key={l.i} value={l.i}>
                  {l.h}p
                </option>
              ))}
            </select>
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
        </div>
      </div>
    </div>
  );
}
