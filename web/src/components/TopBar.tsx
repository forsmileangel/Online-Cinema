import { useEffect, useState, type FormEvent } from "react";
import { NavLink, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { loadRecent, loadScope, parseScope, rememberSearch, searchPath } from "../searchPrefs";
import { APP } from "../appConfig";
import { searchHint, sourceGroup, usePageSource, useSource } from "../source";
import type { SourceInfo } from "../types";

export function TopBar() {
  const loc = useLocation();
  const [params] = useSearchParams();
  const onSearchPage = loc.pathname.endsWith("/search");
  const urlQ = onSearchPage ? params.get("q") || "" : "";
  const [q, setQ] = useState(urlQ);
  const [recent, setRecent] = useState(loadRecent);
  const nav = useNavigate();
  const { source, setSource, sources, prefix, tv, showOriginal, setShowOriginal } = useSource();
  const pageSource = usePageSource();

  // The box shows what the current results are for, also after Back/Forward.
  useEffect(() => {
    if (onSearchPage) setQ(urlQ);
  }, [onSearchPage, urlQ]);

  function onSearch(e: FormEvent) {
    e.preventDefault();
    const query = q.trim();
    if (!query) return;
    setRecent(rememberSearch(query));
    nav(searchPath(prefix, query, pageSource, onSearchPage ? parseScope(params.get("scope")) : loadScope()));
  }

  function pickSource(next: string) {
    setSource(next);
    // On a results page, run the same search on the new source instead of starting over.
    if (onSearchPage && urlQ) nav(searchPath(prefix, urlQ, next, parseScope(params.get("scope"))));
    else nav(prefix || "/");
  }

  const groups: [string, SourceInfo[]][] = [];
  for (const s of sources) {
    const last = groups[groups.length - 1];
    if (last && last[0] === (s.group || "")) last[1].push(s);
    else groups.push([s.group || "", [s]]);
  }
  const option = (s: SourceInfo) => (
    <option key={s.id} value={s.id}>
      {s.label}
    </option>
  );

  return (
    <header className="topbar">
      <NavLink to={prefix || "/"} className="brand">
        <span className="brand-mark" />
        {APP.name}
        <span className="brand-version" title="目前執行的版本">V{__APP_VERSION__}</span>
      </NavLink>
      <form className="search" role="search" onSubmit={onSearch}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value.slice(0, 50))}
          onFocus={() => setRecent(loadRecent())}
          placeholder={searchHint(sourceGroup(sources, pageSource))}
          aria-label="搜尋"
          maxLength={50}
          enterKeyHint="search"
          list="recent-searches"
        />
        {q ? (
          <button type="button" className="search-clear" aria-label="清除搜尋文字" onClick={() => setQ("")}>
            ✕
          </button>
        ) : null}
        <datalist id="recent-searches">
          {recent.map((r) => (
            <option key={r} value={r} />
          ))}
        </datalist>
      </form>
      <label className="src-select">
        <span className="hide-sm" aria-hidden>來源</span>
        <select className="field" aria-label="來源" value={pageSource} onChange={(e) => pickSource(e.target.value)}>
          {groups.map(([group, list]) =>
            group ? (
              <optgroup key={group} label={group}>
                {list.map(option)}
              </optgroup>
            ) : (
              list.map(option)
            ),
          )}
        </select>
      </label>
      <nav className="nav">
        {APP.translateSource && pageSource === APP.translateSource ? (
          <button
            type="button"
            className="ghost"
            data-tv="1"
            onClick={() => setShowOriginal(!showOriginal)}
          >
            {showOriginal ? "顯示譯文" : "顯示原文"}
          </button>
        ) : null}
        <NavLink to={`${prefix}/`} end>
          首頁
        </NavLink>
        {!APP.noPicks.includes(source) ? (
          <NavLink to={`${prefix}/#picks`} className="hide-sm">
            選片
          </NavLink>
        ) : null}
        <NavLink to={`${prefix}/offline`}>離線影片</NavLink>
        <NavLink to={`${prefix}/favorites`} className={() => (/\/(favorites|history)$/.test(loc.pathname) ? "active" : "")}>
          收藏
        </NavLink>
        {tv ? null : (
          <NavLink to="/settings">
            設定
          </NavLink>
        )}
      </nav>
    </header>
  );
}
