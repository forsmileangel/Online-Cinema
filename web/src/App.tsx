import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import { TopBar } from "./components/TopBar";
import { PhonePair } from "./components/PhoneConnection";
import { PairPrompt } from "./components/PairPrompt";
import { TvKeys } from "./components/TvKeys";
import { Browse } from "./pages/Browse";
import { Home } from "./pages/Home";
import { Library } from "./pages/Library";
import { Search } from "./pages/Search";
import { SettingsPage } from "./pages/Settings";
import { OfflineLibrary } from "./components/Offline";
import { Watch } from "./pages/Watch";
import { api } from "./api";
import { useSource } from "./source";

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
    return () => { alive = false; };
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
    const p = loc.pathname;
    const listPage = /^\/(tv\/?)?$/.test(p) || /\/(search|favorites|history)$/.test(p) || p.includes("/c/");
    if (navType === "POP" && listPage) return;
    window.scrollTo(0, 0);
  }, [loc.pathname, loc.search, loc.hash, navType]);

  if (!pairCode && paired === null) return <div className="app-shell"><main className="page"><p role="status">連線中…</p></main></div>;
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
      <main className={watch ? "page page-watch" : "page"}>
        <Routes>
          <Route path="/connect" element={<PhonePair />} />
          {appRoutes("")}
          {appRoutes("/tv")}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
