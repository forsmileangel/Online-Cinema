import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { api } from "./api";
import type { SourceInfo } from "./types";

type Src = SourceInfo;

type Ctx = {
  source: string;
  setSource: (s: string) => void;
  sources: Src[];
  prefix: string;
  tv: boolean;
  showOriginal: boolean;
  setShowOriginal: (v: boolean) => void;
};

// Translations arrive in batches while a list is shown; they live in their own context so only the
// cards that read them re-render, not every subscriber of the source settings.
type ZhCtx = {
  zhMap: Record<string, string>;
  queueZh: (text: string) => void;
};

const ZhContext = createContext<ZhCtx>({ zhMap: {}, queueZh: () => {} });

// Shown until /api/settings answers; the server list is the source of truth.
const DEFAULT_SOURCES: Src[] = [
  { id: "mvffm", label: "MVFFM 電影線上看", group: "影劇" },
  { id: "chinaq", label: "中國人線上看", group: "影劇" },
  { id: "gimy", label: "Gimy 劇迷", group: "影劇" },
  { id: "mmov", label: "MMOV 電影線上看", group: "影劇" },
  { id: "dramaq", label: "DramaQ", group: "影劇" },
  { id: "hongguo", label: "紅果短劇", group: "短劇" },
  { id: "anigamer", label: "巴哈姆特動畫瘋", group: "動畫" },
];

const SourceCtx = createContext<Ctx>({
  source: "hongguo",
  setSource: () => {},
  sources: DEFAULT_SOURCES,
  prefix: "",
  tv: false,
  showOriginal: false,
  setShowOriginal: () => {},
});

export function SourceProvider({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const tv = loc.pathname === "/tv" || loc.pathname.startsWith("/tv/");
  const prefix = tv ? "/tv" : "";
  const [sources, setSources] = useState<Src[]>(DEFAULT_SOURCES);
  const [source, setSourceState] = useState(() => {
    try {
      return localStorage.getItem("cinema.source") || "hongguo";
    } catch {
      return "hongguo";
    }
  });
  const [showOriginal, setShowOriginalState] = useState(() => {
    try {
      return localStorage.getItem("cinema.showOriginal") === "1";
    } catch {
      return false;
    }
  });

  const setShowOriginal = useCallback((v: boolean) => {
    setShowOriginalState(v);
    try {
      localStorage.setItem("cinema.showOriginal", v ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, []);

  const [zhMap, setZhMap] = useState<Record<string, string>>({});
  const zhMapRef = useRef<Record<string, string>>({});
  const pendingZh = useRef<Set<string>>(new Set());
  const inflightZh = useRef<Set<string>>(new Set());
  const zhTimer = useRef<number>(0);

  const flushZh = useRef<() => Promise<void>>(async () => {});
  flushZh.current = async () => {
    const batch = [...pendingZh.current].slice(0, 40);
    for (const t of batch) {
      pendingZh.current.delete(t);
      inflightZh.current.add(t);
    }
    if (!batch.length) return;
    try {
      const res = await api.translate({ title: "", description: "", names: batch });
      const next = { ...zhMapRef.current };
      batch.forEach((orig, i) => {
        const zh = (res.names || [])[i];
        if (zh && zh !== orig) next[orig] = zh;
      });
      zhMapRef.current = next;
      setZhMap(next);
    } catch {
      /* keep English until a later retry */
    } finally {
      for (const t of batch) inflightZh.current.delete(t);
      if (pendingZh.current.size) void flushZh.current();
    }
  };

  const queueZh = useCallback((text: string) => {
    const t = (text || "").trim();
    if (!t || zhMapRef.current[t] || pendingZh.current.has(t) || inflightZh.current.has(t)) return;
    pendingZh.current.add(t);
    window.clearTimeout(zhTimer.current);
    zhTimer.current = window.setTimeout(() => {
      void flushZh.current();
    }, 50);
  }, []);

  const setSource = useCallback((s: string) => {
    const ids = sources.map((x) => x.id);
    const v = ids.includes(s) ? s : "hongguo";
    setSourceState(v);
    try {
      localStorage.setItem("cinema.source", v);
    } catch {
      /* ignore */
    }
    void api.saveSettings({ source: v }).catch(() => {});
  }, [sources]);

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        if (s.sources?.length) setSources(s.sources);
        const ids = (s.sources || []).map((x) => x.id);
        if (s.source && (ids.length === 0 || ids.includes(s.source))) {
          setSourceState(s.source);
          try {
            localStorage.setItem("cinema.source", s.source);
          } catch {
            /* ignore */
          }
        }
      })
      .catch(() => {});
  }, []);

  // The same object until something in it changes, so a translation batch or a route change does not
  // re-render every card that only wants the prefix.
  const value = useMemo(
    () => ({ source, setSource, sources, prefix, tv, showOriginal, setShowOriginal }),
    [source, setSource, sources, prefix, tv, showOriginal, setShowOriginal],
  );
  const zh = useMemo(() => ({ zhMap, queueZh }), [zhMap, queueZh]);
  return (
    <SourceCtx.Provider value={value}>
      <ZhContext.Provider value={zh}>{children}</ZhContext.Provider>
    </SourceCtx.Provider>
  );
}

export function useSource() {
  return useContext(SourceCtx);
}

/** Chinese titles for sources whose listings arrive in English (123AV), filled in as batches return. */
export function useZh() {
  return useContext(ZhContext);
}

export function sourceGroup(sources: Src[], id: string) {
  return sources.find((s) => s.id === id)?.group || "";
}

export function sourceLabel(sources: Src[], id: string) {
  return sources.find((s) => s.id === id)?.label || id;
}

/** Listing links carry their source so Back and saved links never land on another site's list. */
export function withSrc(path: string, src: string) {
  return `${path}${path.includes("?") ? "&" : "?"}src=${encodeURIComponent(src)}`;
}

/** The source a search or list page shows: its ?src= when valid, otherwise the selected source. */
export function usePageSource() {
  const { source, sources } = useSource();
  const { pathname } = useLocation();
  const [params] = useSearchParams();
  const listPage = pathname.endsWith("/search") || pathname.includes("/c/");
  // A video page shows the source of the video, not the globally chosen one.
  const watching = /\/watch\/([^/]+)\//.exec(pathname)?.[1];
  const wanted = watching ? decodeURIComponent(watching) : listPage ? params.get("src") || "" : "";
  return wanted && sources.some((s) => s.id === wanted) ? wanted : source;
}

export function searchHint(group: string) {
  if (group === "成人影片") return "搜尋番號、女優、關鍵字";
  if (group === "影劇") return "搜尋片名、演員、關鍵字";
  if (group === "動畫") return "搜尋動畫名稱";
  return "搜尋劇名、關鍵字";
}

/** Sources whose covers are posters (measured 2:3 to 4:5); the rest are 16:9 or 3:2 stills. */
const PORTRAIT_SOURCES = new Set(["hongguo", "huangguo", "huangguo2", "duanju51", "chinaq", "gimy", "mmov", "mvffm", "dramaq"]);
export const isPortrait = (source?: string) => !!source && PORTRAIT_SOURCES.has(source);
