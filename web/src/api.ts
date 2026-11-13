import { APP } from "./appConfig";
import type { Alternative, Card, HistoryItem, HomePayload, Listing, MultiResult, Settings, VideoDetail } from "./types";

export type IninavStatus = { logged_in: boolean; email: string };
export type HuangguoStatus = { logged_in: boolean; username: string };
export type ConnectionInfo = {
  enabled: boolean;
  port: number;
  addresses: { ip: string; url: string; connect_url: string; listening: boolean }[];
};

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
  volume_level?: number | null;
  volume_muted?: boolean | null;
  volume_scope?: "device" | "stream";
  can_set_volume?: boolean;
  can_mute?: boolean;
};

export type CastDevice = { uuid: string; name: string; host: string; kind: string; online: boolean; mac: string; can_wake: boolean; model?: string; location?: string; manual_power_on?: boolean };
export type CastSession = CastStatus & {
  session_id: string; phase: string; error: string; pending_action: string;
  source: string; video_id: string; episode_id: string; autoplay_next: boolean; playlist: string;
  /** Receiver stream: source = highest rendition, 720p = lowered after stalls; buffer_ahead = seconds this PC holds ahead. */
  quality?: "source" | "720p"; quality_label?: string; quality_reason?: string; buffer_ahead?: number;
};

async function responseError(res: Response) {
  let msg = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    if (body?.detail) msg = String(body.detail);
  } catch {
    /* ignore */
  }
  const retryAfter = res.headers.get("Retry-After");
  const seconds = retryAfter == null ? 0 : /^\d+$/.test(retryAfter)
    ? Number(retryAfter) : Math.max(0, Math.ceil((Date.parse(retryAfter) - Date.now()) / 1000));
  return Object.assign(new Error(msg), { status: res.status, retryAfterSeconds: Number.isFinite(seconds) ? seconds : 0 });
}

// No request waits forever: a hung server shows a message instead of a spinner.
const DEFAULT_TIMEOUT = 20000;
const LIST_TIMEOUT = 30000;
/** Fired when the server refuses this device (not paired, or the code was changed); App shows the pairing page. */
export const UNAUTHORIZED_EVENT = "cinema:unauthorized";

async function req<T>(path: string, init?: RequestInit, timeoutMs = DEFAULT_TIMEOUT): Promise<T> {
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
      if (res.status === 401 && !path.startsWith("/api/tv/pair")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
      throw await responseError(res);
    }
    return await res.json() as T;
  } catch (e) {
    if (timedOut) throw new Error("等待回應逾時，請重試", { cause: e });
    // fetch rejects with a TypeError when the server cannot be reached at all.
    if (e instanceof TypeError) throw new Error("無法連線到伺服器，請確認服務是否在執行", { cause: e });
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

// Listings change slowly; keeping recent ones lets Back show the page at once.
const LIST_TTL = 10 * 60 * 1000;
// Back to a list that grew page by page brings back what was shown, even if it is older.
const BACK_TTL = 60 * 60 * 1000;
const kept = new Map<string, { at: number; data: unknown }>();

function keep(key: string, data: unknown) {
  kept.delete(key);
  kept.set(key, { at: Date.now(), data });
  for (const old of kept.keys()) {
    if (kept.size <= 200) break;
    kept.delete(old);
  }
}

function peek<T>(key: string, maxAge = LIST_TTL): T | undefined {
  const hit = kept.get(key);
  return hit && Date.now() - hit.at < maxAge ? (hit.data as T) : undefined;
}

async function list<T>(path: string, signal?: AbortSignal, timeoutMs = LIST_TIMEOUT): Promise<T> {
  const data = await req<T>(path, { signal }, timeoutMs);
  keep(path, data);
  return data;
}

const slowHome = (source?: string) => (APP.slowHome === null || APP.slowHome.includes(source || "") ? 35000 : undefined);
const slowList = (source?: string) => (APP.slowList.includes(source || "") ? 35000 : undefined);
const homePath = (source?: string) => withSource("/api/home", source);
const browsePath = (kind: string, slug: string | undefined, page: number, source?: string, sort?: string) =>
  withSource(
    `/api/browse/${encodeURIComponent(kind)}${slug ? `/${encodeURIComponent(slug)}` : ""}?page=${page}${sort ? `&sort=${encodeURIComponent(sort)}` : ""}`,
    source,
  );
const searchPath = (q: string, page: number, source?: string) =>
  withSource(`/api/search?q=${encodeURIComponent(q)}&page=${page}`, source);

/** Calls onResult once per source, as each source's search finishes. */
async function searchMulti(q: string, sources: string[], onResult: (result: MultiResult) => void, signal?: AbortSignal) {
  const res = await fetch(`/api/search/multi?q=${encodeURIComponent(q)}&sources=${encodeURIComponent(sources.join(","))}`, {
    signal,
    credentials: "same-origin",
    headers: { Accept: "application/x-ndjson" },
  });
  if (!res.ok) throw await responseError(res);
  if (!res.body) throw new Error("瀏覽器不支援逐筆讀取搜尋結果");
  const reader = res.body.getReader();
  // TextDecoder (not TextDecoderStream) so older TV browsers can read the stream too.
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += value ? decoder.decode(value, { stream: true }) : decoder.decode();
    let cut = buffer.indexOf("\n");
    while (cut >= 0) {
      const line = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 1);
      if (line) onResult(JSON.parse(line) as MultiResult);
      cut = buffer.indexOf("\n");
    }
    if (done) return;
  }
}

export const api = {
  web720p: (src: string, signal?: AbortSignal) => {
    const upstream = new URL(src, window.location.origin).searchParams.get("u") || "";
    return req<{ url: string }>(`/api/playback/720p?u=${encodeURIComponent(upstream)}`, { signal }, 30000);
  },
  health: () => req<{ ok: boolean; mirror?: string; version?: string }>("/api/health"),
  home: (source?: string, signal?: AbortSignal) =>
    list<HomePayload>(homePath(source), signal, slowHome(source)),
  peekHome: (source?: string) => peek<HomePayload>(homePath(source)),
  browse: (kind: string, slug?: string, page = 1, source?: string, signal?: AbortSignal, sort?: string) =>
    list<Listing>(browsePath(kind, slug, page, source, sort), signal, slowList(source)),
  peekBrowse: (kind: string, slug?: string, page = 1, source?: string, back = false, sort?: string) =>
    peek<Listing>(browsePath(kind, slug, page, source, sort), back ? BACK_TTL : LIST_TTL),
  search: (q: string, page = 1, source?: string, signal?: AbortSignal) =>
    list<Listing>(searchPath(q, page, source), signal, slowList(source)),
  peekSearch: (q: string, page = 1, source?: string, back = false) =>
    peek<Listing>(searchPath(q, page, source), back ? BACK_TTL : LIST_TTL),
  searchMulti,
  peekMulti: (key: string) => peek<Record<string, MultiResult>>(`multi\n${key}`),
  keepMulti: (key: string, results: Record<string, MultiResult>) => keep(`multi\n${key}`, results),
  history: (limit = 200) => req<{ items: HistoryItem[] }>(`/api/history?limit=${limit}`),
  alternatives: (source: string, id: string, title: string) =>
    req<{ items: Alternative[] }>(`/api/alternatives?${new URLSearchParams({ source, video_id: id, title: title.slice(0, 200) })}`, undefined, 40000),
  removeHistory: (id: string, source: string) =>
    req(withSource(`/api/history/${encodeURIComponent(id)}`, source), { method: "DELETE" }),
  video: async (id: string, source = APP.defaultSource, signal?: AbortSignal, ep?: string) => {
    // The 動畫瘋 preparation (and hls.js behind it) is only fetched when one is played.
    if (source === "anigamer") await (await import("./anigamer")).prepareAnigamer(id, ep, signal);
    const q = ep ? `?ep=${encodeURIComponent(ep)}` : "";
    return req<VideoDetail>(`/api/video/${encodeURIComponent(source)}/${encodeURIComponent(id)}${q}`, { signal }, 60000);
  },
  favorites: () => req<{ items: Card[] }>("/api/favorites"),
  addFavorite: (card: Card) =>
    req("/api/favorites", { method: "POST", body: JSON.stringify(card) }),
  removeFavorite: (id: string, source = APP.defaultSource) =>
    req(withSource(`/api/favorites/${encodeURIComponent(id)}`, source), { method: "DELETE" }),
  saveHistory: (body: {
    id: string;
    title: string;
    cover: string;
    position_sec: number;
    duration_sec: number;
    source?: string;
    episode_id?: string;
  }, keepalive = false) => req("/api/history", { method: "PUT", body: JSON.stringify(body), keepalive }),
  clearHistory: () => req("/api/history", { method: "DELETE" }),
  ininavStatus: () => req<IninavStatus>("/api/ininav/status"),
  ininavLogin: (email: string, password: string) =>
    req<IninavStatus>("/api/ininav/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  ininavLogout: () => req<IninavStatus>("/api/ininav/logout", { method: "POST" }),
  huangguoStatus: () => req<HuangguoStatus>("/api/huangguo/status"),
  huangguoLogin: (username: string, password: string) =>
    req<HuangguoStatus>("/api/huangguo/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  huangguoLogout: () => req<HuangguoStatus>("/api/huangguo/logout", { method: "POST" }),
  connection: (signal?: AbortSignal) => req<ConnectionInfo>("/api/connection", { signal, cache: "no-store" }, 10000),
  settings: () => req<Settings>("/api/settings"),
  saveSettings: (body: {
    forced_mirror?: string;
    locale?: string;
    source?: string;
    theme?: string;
    lan_tv?: boolean;
    in_kaohsiung?: boolean;
    trials?: Record<string, boolean>;
  }) => req<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  tvStatus: () => req<{ paired: boolean }>("/api/tv/status", undefined, 8000),
  tvPair: (code: string) =>
    req<{ ok: boolean }>("/api/tv/pair", { method: "POST", body: JSON.stringify({ code }) }),
  tvRotate: () => req<{ tv_code: string }>("/api/tv/rotate", { method: "POST" }),
  castDevices: (signal?: AbortSignal) =>
    req<{ devices: CastDevice[]; selected: string; origin: string; in_kaohsiung?: boolean }>(
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
  castSessionControl: (uuid: string, session_id: string, action: string, values: { position_sec?: number; episode_id?: string; autoplay_next?: boolean; volume_level?: number; muted?: boolean } = {}) =>
    req<CastSession>("/api/cast/session/control", { method: "POST", body: JSON.stringify({ uuid, session_id, action, ...values }) }, 10000),
  castDeviceMac: (uuid: string, mac: string) => req("/api/cast/device", { method: "PUT", body: JSON.stringify({ uuid, mac }) }, 10000),
  castControl: (action: string, uuid: string, content_id: string, position_sec?: number, signal?: AbortSignal) =>
    req<CastStatus>("/api/cast/control", { method: "POST", body: JSON.stringify({ action, position_sec, uuid, content_id }), signal }, 45000),
  castStatus: (uuid: string, signal?: AbortSignal) =>
    req<CastStatus>(`/api/cast/status?uuid=${encodeURIComponent(uuid)}`, { signal }, 15000),
  refreshMirrors: () => req<{ mirror: string }>("/api/mirrors/refresh", { method: "POST" }),
  translate: (body: { title: string; description: string; names?: string[] }) =>
    req<{ title: string; description: string; names: string[] }>("/api/translate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
