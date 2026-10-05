import Hls from "hls.js";
import { api } from "../api";
import { useEffect, useRef, useState, type ChangeEvent, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import type { CastingController } from "../hooks/useCasting";

const VOL_KEY = "cinema.volume";
const QUALITY_KEY = "cinema.quality";
const RATE_KEY = "cinema.rate";
const RATES = [0.75, 1, 1.25, 1.5, 2];
// Seconds between in-place retries of the same stream before a fresh source is resolved.
const NETWORK_RETRIES = [1, 3, 6, 10, 15, 20];

function loadRate() {
  try {
    const rate = Number(localStorage.getItem(RATE_KEY));
    if (RATES.includes(rate)) return rate;
  } catch { /* Storage may be disabled. */ }
  return 1;
}

function saveRate(rate: number) {
  try { localStorage.setItem(RATE_KEY, String(rate)); } catch { /* ignore */ }
}

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
  startPaused = false,
  onProgress,
  onPlaybackStarted,
  onEnded,
  onError,
  remote,
  returnPosition,
  favorited,
  onToggleFav,
  episodeControls,
  nextEpisode,
}: {
  src: string;
  startAt?: number;
  startPaused?: boolean;
  onProgress?: (pos: number, dur: number) => void;
  onPlaybackStarted?: () => void;
  onEnded?: () => void;
  onError?: (position: number, paused: boolean) => void;
  remote?: CastingController;
  returnPosition?: number;
  favorited?: boolean;
  onToggleFav?: () => void;
  episodeControls?: ReactNode;
  nextEpisode?: { title: string; onPlay: () => void };
}) {
  const casting = !!remote?.active || !!remote?.restoring;
  const rootRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const castingRef = useRef(casting);
  castingRef.current = casting;
  const dragging = useRef(false);
  const clickWait = useRef<number | null>(null);
  const lastVol = useRef(loadVol() || 0.8);
  const [paused, setPaused] = useState(true);
  const pausedRef = useRef(true);
  pausedRef.current = paused;
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
  const playbackIntent = useRef(!startPaused);
  const expectedDuration = useRef(0);
  const failureReported = useRef(false);
  const [loading, setLoading] = useState(true);
  const [loadingShown, setLoadingShown] = useState(false);
  const [notice, setNotice] = useState("");
  const [osd, setOsd] = useState<{ text: string; id: number } | null>(null);
  const osdTimer = useRef<number | null>(null);
  const [rate, setRate] = useState(loadRate);
  const lastPointer = useRef("");
  const lastTap = useRef(0);
  const recovery = useRef({ media: 0, mediaAt: 0, network: 0, timer: 0 });
  const [show, setShow] = useState(true);
  const showRef = useRef(true);
  showRef.current = show;
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
    const video = videoRef.current;
    const pausedByUser = !playbackIntent.current || !!(video?.paused && !video.error && !video.ended);
    onErrorRef.current?.(playbackPosition.current, pausedByUser);
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
    if (!video || !(src.startsWith("/api/hls") || /^\/api\/offline\/media\/[a-f0-9]{64}\.mp4$/.test(src))) return;
    setPlayError("");
    failureReported.current = false;
    setQualityError("");
    setLoading(true);
    setVideoHeight(0);
    if (!usingWeb720) setLevels([]);
    setLevel(usingWeb720 ? -4 : -1);
    let hls: Hls | null = null;
    window.clearTimeout(recovery.current.timer);
    recovery.current = { media: 0, mediaAt: 0, network: 0, timer: 0 };
    setNotice("");
    let nativeMetadata: (() => void) | undefined;
    const resume = qualityResume.current;
    qualityResume.current = null;
    const start = resume?.position ?? Math.max(0, startAt);
    playbackPosition.current = start;
    playbackIntent.current = !(resume?.paused ?? startPaused);
    expectedDuration.current = 0;
    const beginPlayback = () => {
      if (resume?.paused ?? startPaused) { setLoading(false); return; }
      if (!castingRef.current) void video.play().catch(() => { setLoading(false); setPlayError("瀏覽器暫停了自動播放，請按播放繼續。"); });
    };
    video.volume = vol;
    video.muted = muted;
    video.defaultPlaybackRate = rate;
    video.playbackRate = rate;
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
        maxBufferLength: 180,
        maxMaxBufferLength: 600,
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
      hls.on(Hls.Events.FRAG_LOADED, () => {
        if (!recovery.current.network) return;
        recovery.current.network = 0;
        setNotice("");
      });
      hls.on(Hls.Events.ERROR, (_e, data) => {
        const instance = hls!;
        if (!data.fatal || hlsRef.current !== instance || castingRef.current) return;
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR && recoverMedia()) return;
        const state = recovery.current;
        const status = data.response?.code ?? 0;
        const manifest = [Hls.ErrorDetails.MANIFEST_LOAD_ERROR, Hls.ErrorDetails.MANIFEST_LOAD_TIMEOUT,
          Hls.ErrorDetails.MANIFEST_PARSING_ERROR].includes(data.details);
        // Keep playing what is buffered while the same stream is retried. Rejected
        // or expired URLs (4xx) and a broken manifest still need a fresh source.
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR && !manifest && !(status >= 400 && status < 500)
            && state.network < NETWORK_RETRIES.length) {
          const delay = NETWORK_RETRIES[state.network++];
          setNotice(`網路不穩，${delay} 秒後原地重試（第 ${state.network} 次），已緩衝的畫面會繼續播放`);
          window.clearTimeout(state.timer);
          state.timer = window.setTimeout(() => {
            if (hlsRef.current === instance) instance.startLoad(video.currentTime);
          }, delay * 1000);
          return;
        }
        playbackFailed();
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
      window.clearTimeout(recovery.current.timer);
      if (nativeMetadata) video.removeEventListener("loadedmetadata", nativeMetadata);
      hls?.destroy();
      hlsRef.current = null;
    };
    // vol/muted applied once when attaching; later changes go through applyVol
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, startAt, startPaused, playbackSrc]);

  useEffect(() => {
    // Short stalls (seeking into the buffer, a quick segment) should not flash a pill.
    if (!loading) { setLoadingShown(false); return; }
    const timer = window.setTimeout(() => setLoadingShown(true), 400);
    return () => window.clearTimeout(timer);
  }, [loading]);

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
      let ahead = 0;
      for (let i = 0; i < video.buffered.length; i++) {
        if (video.buffered.start(i) <= video.currentTime + 0.5 && video.buffered.end(i) >= video.currentTime) ahead = video.buffered.end(i);
      }
      setBuf(ahead);
      setPaused(video.paused);
      setLoading(video.readyState < 3 && !video.paused);
      if (!castingRef.current) {
        if (!video.error && !failureReported.current && Number.isFinite(video.duration) && video.duration > 0) {
          expectedDuration.current = Math.max(expectedDuration.current, video.duration);
          playbackPosition.current = video.currentTime;
          onProgress?.(video.currentTime, video.duration);
        }
      }
    };
    const onPlay = () => { playbackIntent.current = true; };
    const onPause = () => {
      // Native media errors/ended can set paused without a user pause command.
      if (!video.error && !video.ended && !failureReported.current) playbackIntent.current = false;
    };
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("timeupdate", onTime);
    video.addEventListener("progress", onTime);
    video.addEventListener("play", onTime);
    video.addEventListener("pause", onTime);
    const onWaiting = () => setLoading(true);
    const onPlaying = () => {
      setLoading(false);
      if (!recovery.current.network) setNotice("");
    };
    const onVolume = () => {
      setVol(video.volume);
      setMuted(video.muted || video.volume === 0);
    };
    video.addEventListener("waiting", onWaiting);
    video.addEventListener("playing", onPlaying);
    video.addEventListener("volumechange", onVolume);
    const onEnd = () => {
      if (castingRef.current || failureReported.current) return;
      const expected = Math.max(expectedDuration.current, Number.isFinite(video.duration) ? video.duration : 0);
      if (expected <= 0 || video.currentTime < expected - 2) playbackFailed();
      else onEnded?.();
    };
    video.addEventListener("ended", onEnd);
    return () => {
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
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
          seekBy(e.key === "ArrowRight" ? 10 : -10);
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
        e.preventDefault();
        seekBy(10);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        seekBy(-10);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        nudgeVolume(0.05);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        nudgeVolume(-0.05);
      } else if (e.key === "<" || e.key === ">") {
        stepRate(e.key === ">" ? 1 : -1);
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

  function flash(text: string) {
    setOsd({ text, id: Date.now() });
    if (osdTimer.current) window.clearTimeout(osdTimer.current);
    osdTimer.current = window.setTimeout(() => setOsd(null), 800);
  }

  function seekBy(delta: number) {
    if (!canSeek) return;
    if (casting) {
      void remote?.control("seek", Math.max(0, Math.min(duration, currentTime + delta)));
    } else {
      const v = videoRef.current;
      if (!v) return;
      v.currentTime = Math.max(0, Math.min(v.duration || 0, v.currentTime + delta));
    }
    flash(`${delta > 0 ? "快轉" : "倒轉"} ${Math.abs(delta)} 秒`);
    bumpUi();
  }

  function nudgeVolume(delta: number) {
    const v = videoRef.current;
    if (!v) return;
    const next = Math.min(1, Math.max(0, (v.muted ? 0 : v.volume) + delta));
    applyVol(next);
    flash(`音量 ${Math.round(next * 100)}`);
  }

  function changeRate(next: number) {
    setRate(next);
    saveRate(next);
    const v = videoRef.current;
    if (v) {
      v.defaultPlaybackRate = next;
      v.playbackRate = next;
    }
    flash(`${next}x 速度`);
  }

  function stepRate(step: number) {
    if (casting) return;
    const index = Math.max(0, Math.min(RATES.length - 1, RATES.indexOf(rate) + step));
    changeRate(RATES[index]);
  }

  function recoverMedia() {
    const hls = hlsRef.current;
    const state = recovery.current;
    if (!hls || castingRef.current) return false;
    const now = Date.now();
    // The element and hls.js can both report one decode failure.
    if (state.media && now - state.mediaAt < 1000) return true;
    if (now - state.mediaAt > 60000) state.media = 0;
    state.mediaAt = now;
    if (state.media >= 2) return false;
    if (state.media++ === 1) hls.swapAudioCodec();
    setNotice("畫面解碼中斷，正在原地修復…");
    hls.recoverMediaError();
    return true;
  }

  function bumpUi() {
    setShow(true);
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      if (lastPointer.current === "mouse" && rootRef.current?.querySelector(".controls")?.matches(":hover")) bumpUi();
      else if (!pausedRef.current) setShow(false);
    }, 2200);
  }

  function hideNow() {
    if (pausedRef.current || castingRef.current || dragging.current) return;
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
    setShow(false);
  }

  function onTap(e: MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const now = Date.now();
    if (clickWait.current && now - lastTap.current < 300) {
      window.clearTimeout(clickWait.current);
      clickWait.current = null;
      lastTap.current = 0;
      if (x < 0.35) seekBy(-10);
      else if (x > 0.65) seekBy(10);
      else toggleFs();
      return;
    }
    lastTap.current = now;
    clickWait.current = window.setTimeout(() => {
      clickWait.current = null;
      // On touch, the first tap only reveals the controls.
      if (!showRef.current && !pausedRef.current && !castingRef.current) bumpUi();
      else togglePlay();
    }, 300);
  }

  function onSurfaceClick(e: MouseEvent<HTMLDivElement>) {
    const el = e.target as HTMLElement;
    if (el.closest(".controls, .next-up")) return;
    if (lastPointer.current === "touch") { onTap(e); return; }
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
    if (el.closest(".controls, .next-up") || lastPointer.current === "touch") return;
    if (clickWait.current) {
      window.clearTimeout(clickWait.current);
      clickWait.current = null;
    }
    toggleFs();
  }

  const wheelRef = useRef<(e: WheelEvent) => void>(() => {});
  wheelRef.current = (e) => {
    if (casting) return;
    const v = videoRef.current;
    if (!v) return;
    e.preventDefault();
    nudgeVolume(e.deltaY < 0 ? 0.05 : -0.05);
  };

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    // React attaches wheel listeners as passive, which ignores preventDefault
    // and scrolls the page while the volume changes.
    const onWheel = (e: WheelEvent) => wheelRef.current(e);
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  function releaseSelect(e: ChangeEvent<HTMLSelectElement>) {
    // After a mouse pick, Space should pause rather than reopen the menu.
    if (lastPointer.current === "mouse") e.currentTarget.blur();
  }

  function releaseFocus(e: PointerEvent<HTMLDivElement>) {
    // A mouse click must not leave focus on a control, or Space re-presses it
    // instead of pausing. Keyboard and TV remote focus is left untouched.
    if (e.pointerType !== "mouse") return;
    const control = (e.target as HTMLElement).closest<HTMLElement>("button, input[type='range']");
    if (control) window.setTimeout(() => control.blur(), 0);
  }

  const shownT = scrub != null && duration ? scrub * duration : currentTime;
  const playPct = duration ? Math.min(100, Math.max(0, shownT / duration * 100)) : 0;
  const bufPct = !casting && duration ? (buf / duration) * 100 : 0;
  const shownVol = casting ? volumeDraft ?? (remoteMuted ? 0 : remoteLevel ?? 0) : muted ? 0 : vol;
  const shownMuted = casting ? remoteMuted : muted;
  const castQuality = new URLSearchParams(remote?.status?.content_id.split("?")[1] || "").get("nesthub") === "1"
    ? "最高 720p" : levels.length === 1 ? `${levels[0].h}p` : "投放自動";

  const waiting = casting ? !!(remote?.busy || remote?.status?.buffering) : qualityBusy || (!qualityError && !playError && !notice && loadingShown);
  const pill = casting ? (waiting ? "等待電視確認" : "") : qualityBusy ? "正在準備 720p…" : qualityError || playError || notice || (loadingShown ? "載入中" : "");
  const nextWindow = Math.min(20, duration * 0.15);
  const showNext = !!nextEpisode && !casting && duration > 30 && currentTime > 0 && duration - currentTime <= nextWindow;

  return (
    <div
      ref={rootRef}
      className={`player ${show || isPaused || casting ? "show" : ""}`}
      onPointerDown={(e) => { lastPointer.current = e.pointerType; }}
      onPointerMove={(e) => { if (e.pointerType !== "touch") bumpUi(); }}
      onMouseLeave={hideNow}
      onClick={onSurfaceClick}
      onDoubleClick={onSurfaceDblClick}
    >
      <video ref={videoRef} playsInline onResize={(e) => setVideoHeight(e.currentTarget.videoHeight)} onLoadedMetadata={(e) => setVideoHeight(e.currentTarget.videoHeight)} onPlay={() => { setPlayError(""); bumpUi(); if (!castingRef.current) onPlaybackStarted?.(); }} onError={() => { if (!recoverMedia()) playbackFailed(); }} />
      {isPaused ? (
        <div className="center-play">
          <span>▶</span>
        </div>
      ) : null}
      {pill ? <div className={`loading-pill${waiting ? " spin" : ""}`} role="status">{pill}</div> : null}
      <div className="overlay">
        <div className="controls" onPointerUp={releaseFocus}>
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
            {!casting ? <select
              className="field rate"
              aria-label="播放速度"
              title="播放速度（< 和 > 鍵）"
              value={rate}
              onChange={(e) => { changeRate(Number(e.target.value)); releaseSelect(e); }}
              onClick={(e) => e.stopPropagation()}
            >
              {RATES.map((r) => <option key={r} value={r}>{r === 1 ? "1x" : `${r}x`}</option>)}
            </select> : null}
            {casting ? <span className="quality-status" aria-label="投放畫質" title="投放端處理畫質；本機畫質選單只適用於本機播放">{castQuality}</span> : !isMp4 && Hls.isSupported() ? <select
              className="field"
              aria-label="播放畫質"
              title="記住畫質供下次播放使用；720p 轉換需要電腦處理，部分片源不支援"
              disabled={qualityBusy}
              value={level}
              onChange={(e) => { changeLevel(Number(e.target.value)); releaseSelect(e); }}
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
      {showNext ? (
        <button type="button" className="next-up" data-tv="1" onClick={(e) => { e.stopPropagation(); nextEpisode!.onPlay(); }}>
          下一集 ▶ <span>{nextEpisode!.title}</span>
        </button>
      ) : null}
      {osd ? <div key={osd.id} className="osd" aria-live="polite">{osd.text}</div> : null}
    </div>
  );
}
