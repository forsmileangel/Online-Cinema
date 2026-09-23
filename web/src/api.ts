import type { Card, HomePayload, Listing, Settings, VideoDetail } from "./types";

export type CastStatus = {
  uuid: string;
  content_id: string;
  playing: boolean;
  paused: boolean;
  idle: boolean;
  buffering: boolean;
  current_time: number;
  duration: number;
  warning?: string;
};

export type CastDevice = { uuid: string; name: string; host: string; kind: string; online: boolean; mac: string; can_wake: boolean };
export type CastSession = CastStatus & {
  session_id: string; phase: string; error: string; pending_action: string;
  source: string; video_id: string; episode_id: string; autoplay_next: boolean; playlist: string;
};

async function req<T>(path: string, init?: RequestInit, timeoutMs?: number): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  let timedOut = false;
  const timer = timeoutMs ? setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs) : undefined;
  if (init?.signal?.aborted) controller.abort();
  init?.signal?.addEventListener("abort", abort, { once: true });
  try {
    const res = await fetch(path, {
      ...init,
      signal: controller.signal,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers || {}),
      },
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body?.detail) msg = String(body.detail);
      } catch {
        /* ignore */
      }
      throw new Error(msg);
    }
    return await res.json() as T;
  } catch (e) {
    if (timedOut) throw new Error("等待回應逾時，請重試");
    throw e;
  } finally {
    clearTimeout(timer);
    init?.signal?.removeEventListener("abort", abort);
  }
}

function withSource(path: string, source?: string) {
  if (!source) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}source=${encodeURIComponent(source)}`;
}

export const api = {
  health: () => req<{ ok: boolean }>("/api/health"),
  home: (source?: string, signal?: AbortSignal) =>
    req<HomePayload>(withSource("/api/home", source), { signal }, 35000),
  browse: (kind: string, slug?: string, page = 1, source?: string, signal?: AbortSignal) => {
    const extra = slug ? `/${encodeURIComponent(slug)}` : "";
    return req<Listing>(
      withSource(`/api/browse/${encodeURIComponent(kind)}${extra}?page=${page}`, source),
      { signal }, source === "chinaq" || source === "dramaq" ? 35000 : undefined,
    );
  },
  search: (q: string, page = 1, source?: string, signal?: AbortSignal) =>
    req<Listing>(withSource(`/api/search?q=${encodeURIComponent(q)}&page=${page}`, source), { signal }, source === "chinaq" || source === "dramaq" ? 35000 : undefined),
  video: (id: string, source = "hongguo", signal?: AbortSignal, ep?: string) => {
    const q = ep ? `?ep=${encodeURIComponent(ep)}` : "";
    return req<VideoDetail>(`/api/video/${encodeURIComponent(source)}/${encodeURIComponent(id)}${q}`, { signal }, 60000);
  },
  favorites: () => req<{ items: Card[] }>("/api/favorites"),
  addFavorite: (card: Card) =>
    req("/api/favorites", { method: "POST", body: JSON.stringify(card) }),
  removeFavorite: (id: string, source = "hongguo") =>
    req(withSource(`/api/favorites/${encodeURIComponent(id)}`, source), { method: "DELETE" }),
  saveHistory: (body: {
    id: string;
    title: string;
    cover: string;
    position_sec: number;
    duration_sec: number;
    source?: string;
    episode_id?: string;
  }) => req("/api/history", { method: "PUT", body: JSON.stringify(body) }),
  clearHistory: () => req("/api/history", { method: "DELETE" }),
  settings: () => req<Settings>("/api/settings"),
  saveSettings: (body: {
    source?: string;
    theme?: string;
    lan_tv?: boolean;
  }) => req<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  tvPair: (code: string) =>
    req<{ ok: boolean }>("/api/tv/pair", { method: "POST", body: JSON.stringify({ code }) }),
  tvRotate: () => req<{ tv_code: string }>("/api/tv/rotate", { method: "POST" }),
  castDevices: (signal?: AbortSignal) =>
    req<{ devices: CastDevice[]; selected: string; origin: string }>(
      "/api/cast/devices",
      { signal }, 45000,
    ),
  castSelect: (uuid: string, signal?: AbortSignal) =>
    req("/api/cast/select", { method: "POST", body: JSON.stringify({ uuid }), signal }, 45000),
  castPlay: (body: { url: string; title: string; position_sec: number; uuid: string }, signal?: AbortSignal) =>
    req<CastStatus>("/api/cast/play", { method: "POST", body: JSON.stringify(body), signal }, 45000),
  castSession: (uuid = "", signal?: AbortSignal) => req<CastSession | null>(`/api/cast/session?uuid=${encodeURIComponent(uuid)}`, { signal }, 10000),
  castSessionPlay: (body: { url: string; title: string; position_sec: number; uuid: string; source: string; video_id: string; episode_id: string; autoplay_next: boolean; wake: boolean }, signal?: AbortSignal) =>
    req<CastSession>("/api/cast/play", { method: "POST", body: JSON.stringify({ ...body, managed: true }), signal }, 10000),
  castSessionControl: (uuid: string, session_id: string, action: string, values: { position_sec?: number; episode_id?: string; autoplay_next?: boolean } = {}) =>
    req<CastSession>("/api/cast/session/control", { method: "POST", body: JSON.stringify({ uuid, session_id, action, ...values }) }, 10000),
  castDeviceMac: (uuid: string, mac: string) => req("/api/cast/device", { method: "PUT", body: JSON.stringify({ uuid, mac }) }, 10000),
  castControl: (action: string, uuid: string, content_id: string, position_sec?: number, signal?: AbortSignal) =>
    req<CastStatus>("/api/cast/control", { method: "POST", body: JSON.stringify({ action, position_sec, uuid, content_id }), signal }, 45000),
  castStatus: (uuid: string, signal?: AbortSignal) =>
    req<CastStatus>(`/api/cast/status?uuid=${encodeURIComponent(uuid)}`, { signal }, 15000),
  translate: (body: { title: string; description: string; names?: string[] }) =>
    req<{ title: string; description: string; names: string[] }>("/api/translate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
