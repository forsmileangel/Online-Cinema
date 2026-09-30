import { useEffect, useMemo, useRef, useState } from "react";

type Status = { phase: string; seconds: number; target: number; error: string };
const empty: Status = { phase: "waiting", seconds: 0, target: 600, error: "" };
const stamp = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

export function NextEpisodeBuffer({ url, title, enabled, castSession, qualityKey }: {
  url: string; title: string; enabled: boolean; castSession?: string; qualityKey: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<Status>(empty);
  const [attempt, setAttempt] = useState(0);
  const owner = useMemo(() => `web-${Date.now()}-${Math.random().toString(36).slice(2)}`, [url, attempt]);
  useEffect(() => {
    setStatus(empty);
    if (!enabled || (!url && !castSession)) return;
    if (url.startsWith("/api/offline/media/")) { setStatus({ ...empty, phase: "local" }); return; }
    let alive = true, busy = false, retry = attempt > 0;
    const selectedOwner = castSession ? `cast-${castSession}` : owner;
    const cancel = () => {
      if (!castSession) void fetch(`/api/playback/prefetch/${selectedOwner}`, { method: "DELETE", keepalive: true }).catch(() => {});
    };
    const pulse = async () => {
      if (!alive || busy) return;
      busy = true;
      try {
        const video = root.current?.closest(".player-wrap")?.querySelector("video");
        let ahead = 0;
        if (video) {
          for (let i = 0; i < video.buffered.length; i++) {
            if (video.buffered.start(i) <= video.currentTime + 0.25 && video.buffered.end(i) > video.currentTime) ahead = video.buffered.end(i) - video.currentTime;
          }
        }
        const ready = !!video && !video.ended && video.readyState >= 3 &&
          (ahead >= 60 || (video.duration > 0 && video.duration - video.currentTime < 60 && ahead >= video.duration - video.currentTime - 0.5));
        let height = 0;
        try { height = Math.max(0, Number(localStorage.getItem(qualityKey)) || 0); } catch { /* source quality */ }
        const response = await fetch(castSession ? `/api/playback/prefetch/${selectedOwner}` : "/api/playback/prefetch", castSession ? undefined : {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ owner, url, ready, height, retry }),
        });
        retry = false;
        if (response.ok && alive) setStatus(await response.json() as Status);
      } catch { /* A failed warm-up cannot stop current playback. */ }
      finally { busy = false; }
    };
    void pulse();
    const timer = window.setInterval(() => void pulse(), 3000);
    const container = root.current?.closest(".player-wrap");
    container?.addEventListener("waiting", pulse, true);
    container?.addEventListener("pause", pulse, true);
    window.addEventListener("pagehide", cancel);
    return () => {
      alive = false; window.clearInterval(timer); cancel();
      container?.removeEventListener("waiting", pulse, true);
      container?.removeEventListener("pause", pulse, true);
      window.removeEventListener("pagehide", cancel);
    };
  }, [url, enabled, castSession, owner, qualityKey, attempt]);
  if (!enabled || !title) return null;
  if (status.phase === "local") return <div className="muted" style={{ padding: "8px 16px" }}>下一集 {title}：已下載，將使用本地影片</div>;
  const message = status.phase === "complete" ? "預載完成" : status.phase === "waiting" ? "等待當集緩衝" : status.phase === "error" || status.phase === "unsupported" ? status.error : "預載中";
  return <div ref={root} className="muted" role="status" style={{ padding: "8px 16px" }}>
    下一集 {title}：{message} · {stamp(status.seconds)} / {stamp(status.target)}
    {status.phase === "error" && !castSession ? <button className="btn alt" type="button" onClick={() => setAttempt(n => n + 1)}>重試預載</button> : null}
  </div>;
}
