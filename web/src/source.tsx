import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { api } from "./api";

type Src = { id: string; label: string };

type Ctx = {
  source: string;
  setSource: (s: string) => void;
  sources: Src[];
  prefix: string;
  tv: boolean;
  showOriginal: boolean;
  setShowOriginal: (v: boolean) => void;
  zhMap: Record<string, string>;
  queueZh: (text: string) => void;
};

const DEFAULT_SOURCES: Src[] = [
  { id: "hongguo", label: "紅果短劇" },
  { id: "chinaq", label: "中國人線上看" },
  { id: "gimy", label: "Gimy 劇迷" },
  { id: "dramaq", label: "DramaQ" },
];

const SourceCtx = createContext<Ctx>({
  source: "hongguo",
  setSource: () => {},
  sources: DEFAULT_SOURCES,
  prefix: "",
  tv: false,
  showOriginal: false,
  setShowOriginal: () => {},
  zhMap: {},
  queueZh: () => {},
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

  function setShowOriginal(v: boolean) {
    setShowOriginalState(v);
    try {
      localStorage.setItem("cinema.showOriginal", v ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

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

  function setSource(s: string) {
    const ids = sources.map((x) => x.id);
    const v = ids.includes(s) ? s : "hongguo";
    setSourceState(v);
    try {
      localStorage.setItem("cinema.source", v);
    } catch {
      /* ignore */
    }
    void api.saveSettings({ source: v }).catch(() => {});
  }

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

  return (
    <SourceCtx.Provider
      value={{ source, setSource, sources, prefix, tv, showOriginal, setShowOriginal, zhMap, queueZh }}
    >
      {children}
    </SourceCtx.Provider>
  );
}

export function useSource() {
  return useContext(SourceCtx);
}
