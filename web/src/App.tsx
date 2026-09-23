import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { TopBar } from "./components/TopBar";
import { TvKeys } from "./components/TvKeys";
import { Browse } from "./pages/Browse";
import { Favorites } from "./pages/Favorites";
import { Home } from "./pages/Home";
import { Search } from "./pages/Search";
import { SettingsPage } from "./pages/Settings";
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
      <Route path={`${p}/favorites`} element={<Favorites />} />
      {p ? null : <Route path="/settings" element={<SettingsPage />} />}
    </>
  );
}

export function App() {
  const loc = useLocation();
  const nav = useNavigate();
  const { tv } = useSource();
  const watch = loc.pathname.includes("/watch/");

  useEffect(() => {
    if (!tv) return;
    const c = new URLSearchParams(loc.search).get("c");
    if (!c) return;
    void api
      .tvPair(c)
      .then(() => nav("/tv", { replace: true }))
      .catch(() => {});
  }, [tv, loc.search, nav]);

  useEffect(() => {
    if (loc.hash) {
      const el = document.getElementById(loc.hash.slice(1));
      if (el) {
        el.scrollIntoView({ block: "start" });
        return;
      }
    }
    window.scrollTo(0, 0);
  }, [loc.pathname, loc.search, loc.hash]);

  return (
    <div className={`app-shell${tv ? " tv-mode" : ""}`}>
      {tv ? <TvKeys /> : null}
      <TopBar />
      <main className={watch ? "page page-watch" : "page"}>
        <Routes>
          {appRoutes("")}
          {appRoutes("/tv")}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
