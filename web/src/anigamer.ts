import Hls from "hls.js";

type Preparation = { ready: boolean; id?: string; ad?: string; seconds?: number };

async function post(path: string, signal?: AbortSignal): Promise<Preparation> {
  const response = await fetch(path, { method: "POST", credentials: "same-origin", signal });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "動畫瘋準備失敗，請重試");
  return data;
}

export async function prepareAnigamer(id: string, ep?: string, signal?: AbortSignal) {
  const query = ep ? `?ep=${encodeURIComponent(ep)}` : "";
  const prep = await post(`/api/anigamer/prepare/${encodeURIComponent(id)}${query}`, signal);
  if (prep.ready) return;
  if (!prep.id || !prep.ad || !prep.seconds) throw new Error("動畫瘋沒有可用的準備資料");
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");

  // Use an actual media element and elapsed playback, including during next-episode preparation.
  // Muted playback lets the current episode keep its audio and user controls.
  await new Promise<void>((resolve, reject) => {
    const video = document.createElement("video");
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    video.setAttribute("aria-hidden", "true");
    video.style.cssText = "position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;bottom:0;left:0";
    document.body.appendChild(video);
    const hls = Hls.isSupported() ? new Hls({ maxBufferLength: 35 }) : null;
    const controller = new AbortController();
    let started: Promise<Preparation> | undefined, acknowledged = false, finishing = false, settled = false, played = 0, previous = 0;
    const finish = (error?: unknown) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
      controller.abort();
      video.pause();
      hls?.destroy();
      video.removeAttribute("src");
      video.load();
      video.remove();
      void post(`/api/anigamer/ad/${prep.id}/cancel`).catch(() => {});
      if (error) reject(error); else resolve();
    };
    const abort = () => finish(new DOMException("Aborted", "AbortError"));
    const timeout = window.setTimeout(() => finish(new Error("動畫瘋準備逾時，請重試")), 90000);
    signal?.addEventListener("abort", abort, { once: true });
    video.onplaying = () => {
      if (settled) return;
      if (!started) started = post(`/api/anigamer/ad/${prep.id}/start`, controller.signal).then(data => {
        acknowledged = true; played = 0; previous = video.currentTime; return data;
      });
      void started.catch(finish);
    };
    video.ontimeupdate = () => {
      if (settled || !acknowledged) return;
      const delta = video.currentTime - previous;
      previous = video.currentTime;
      if (delta > 0 && delta < 2) played += delta;
      if (played < prep.seconds! || finishing || !started) return;
      finishing = true;
      video.pause();
      void started.then(() => post(`/api/anigamer/ad/${prep.id}/complete`, controller.signal)).then(() => finish(), finish);
    };
    video.onended = () => {
      if (!finishing) finish(new Error("動畫瘋準備片段不足，請重試"));
    };
    video.onerror = () => finish(new Error("動畫瘋準備片段無法播放，請重試"));
    const play = () => { void video.play().catch(() => finish(new Error("瀏覽器未允許準備播放，請點擊重試"))); };
    if (hls) {
      hls.loadSource(prep.ad!);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, play);
      hls.on(Hls.Events.ERROR, (_, data) => { if (data.fatal) finish(new Error("動畫瘋準備片段載入失敗，請重試")); });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = prep.ad!;
      video.onloadedmetadata = play;
    } else finish(new Error("此瀏覽器不支援動畫瘋播放"));
  });
}
