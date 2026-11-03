import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import { TopBar } from "./components/TopBar";
import { PhonePair } from "./components/PhoneConnection";
import { PairPrompt } from "./components/PairPrompt";
import { TvKeys } from "./components/TvKeys";
import { Browse } from "./pages/Browse";
import { Home } from "./pages/Home";
import { Search } from "./pages/Search";
import { api, UNAUTHORIZED_EVENT } from "./api";

// The home page needs none of these: each is fetched the first time it is opened.
const Watch = lazy(() => import("./pages/Watch").then((m) => ({ default: m.Watch })));
const Library = lazy(() => import("./pages/Library").then((m) => ({ default: m.Library })));
const SettingsPage = lazy(() => import("./pages/Settings").then((m) => ({ default: m.SettingsPage })));
const OfflineLibrary = lazy(() => import("./components/Offline").then((m) => ({ default: m.OfflineLibrary })));
import { GridMetricsContext, measureKey, useMeasureGrid } from "./hooks/useGridMetrics";
import { useSource } from "./source";

/** Pages that are mostly cards: they use the full width and put their own scroll back on Back/Forward. */
function isListPage(p: string) {
  return /^\/(tv\/?)?$/.test(p) || /\/(search|favorites|history)$/.test(p) || p.includes("/c/");
}

function appRoutes(prefix: string) {
  const p = prefix;
  return (
    <>
      <Route path={p ? p : "/"} element={<Home />} />
      {p ? <Route path={`${p}/`} element={<Home />} /> : null}
      <Route path={`${p}/search`} element={<Search />} />
      <Route path={`${p}/c/:kind`} element={<Browse />} />
      <Route path={`${p}/c/:kind/:slug`} element={<Browse />} />
      <Route path={`${p}/watch/:source/:id`} element={<Watch />} />
      <Route path={`${p}/watch/:id`} element={<Watch />} />
      <Route path={`${p}/offline`} element={<OfflineLibrary />} />
      <Route path={`${p}/favorites`} element={<Library tab="favorites" />} />
      <Route path={`${p}/history`} element={<Library tab="history" />} />
      {p ? null : <Route path="/settings" element={<SettingsPage />} />}
    </>
  );
}

export function App() {
  const loc = useLocation();
  const nav = useNavigate();
  const { tv } = useSource();
  const navType = useNavigationType();
  const watch = loc.pathname.includes("/watch/");
  const list = isListPage(loc.pathname);
  const [mainEl, setMainEl] = useState<HTMLElement | null>(null);
  const metrics = useMeasureGrid(mainEl, measureKey(loc.pathname, tv));
  // A pairing link (?c=) holds the pages back until the cookie is set; otherwise their first
  // requests are refused and show "請先用配對碼連電視" (and /?c= never paired at all).
  // /connect has its own pairing page.
  const pairCode = loc.pathname === "/connect" ? null : new URLSearchParams(loc.search).get("c");
  const [refused, setRefused] = useState({ code: "", message: "" });
  // Without a pairing link, a device the PC has not paired yet is asked for the code.
  const [paired, setPaired] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    api.tvStatus().then((r) => { if (alive) setPaired(r.paired); }).catch(() => { if (alive) setPaired(true); });
    // A refused request (pairing expired, code changed) brings the pairing page up at once.
    const refused = () => { if (alive) setPaired(false); };
    window.addEventListener(UNAUTHORIZED_EVENT, refused);
    return () => { alive = false; window.removeEventListener(UNAUTHORIZED_EVENT, refused); };
  }, []);

  useEffect(() => {
    if (!pairCode) return;
    let cancelled = false;
    void api
      .tvPair(pairCode)
      .then(() => {
        if (cancelled) return;
        if (tv) nav("/tv", { replace: true });
        else window.location.replace("/"); // Reload settings and listings after the pairing cookie is set.
      })
      .catch((e: Error) => { if (!cancelled) setRefused({ code: pairCode, message: e.message }); });
    return () => { cancelled = true; };
  }, [tv, pairCode, nav]);

  useEffect(() => {
    if (loc.hash) {
      const el = document.getElementById(loc.hash.slice(1));
      if (el) {
        el.scrollIntoView({ block: "start" });
        return;
      }
    }
    // Back/Forward to a list page: the page puts its own position back once its items are shown.
    if (navType === "POP" && isListPage(loc.pathname)) return;
    // A list that only records how far it has grown (?to=, ?n=) stays where the reader is.
    if ((loc.state as { keepScroll?: boolean } | null)?.keepScroll) return;
    window.scrollTo(0, 0);
    // loc.state comes with loc.search: the same navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loc.pathname, loc.search, loc.hash, navType]);

  useEffect(() => {
    document.documentElement.classList.toggle("is-list", list);
  }, [list]);

  // The phone pairing link works on a device that is not paired yet: that is what it is for.
  if (loc.pathname === "/connect") {
    return (
      <div className="app-shell">
        <main className="page"><PhonePair /></main>
      </div>
    );
  }
  // Pages render while the pairing check is still out; a refusal switches to the pairing page.
  if (!pairCode && paired === false) return <PairPrompt />;

  if (pairCode) {
    const failed = refused.code === pairCode;
    return (
      <div className="app-shell">
        <main className="page">
          <h1 className="h1">{tv ? "電視連線" : "手機與平板連線"}</h1>
          {failed ? (
            <>
              <p role="alert">配對失敗：{refused.message}。請確認配對碼沒有更換，或回電腦的設定頁複製最新連結。</p>
              <button className="btn alt" type="button" onClick={() => window.location.reload()}>重試連線</button>
            </>
          ) : (
            <p role="status">正在配對，完成後會自動開啟首頁…</p>
          )}
        </main>
      </div>
    );
  }

  return (
    <div className={`app-shell${tv ? " tv-mode" : ""}`}>
      {tv ? <TvKeys /> : null}
      <TopBar />
      <main ref={setMainEl} className={watch ? "page page-watch" : list ? "page page-wide" : "page"}>
        <GridMetricsContext.Provider value={metrics}>
          <Suspense fallback={<div className="empty" role="status">載入中…</div>}>
            <Routes>
              {appRoutes("")}
              {appRoutes("/tv")}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </GridMetricsContext.Provider>
      </main>
    </div>
  );
}
