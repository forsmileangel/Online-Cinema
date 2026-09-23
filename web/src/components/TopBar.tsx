import { useState, type FormEvent } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useSource } from "../source";

export function TopBar() {
  const [q, setQ] = useState("");
  const nav = useNavigate();
  const { source, setSource, sources, prefix, tv } = useSource();

  function onSearch(e: FormEvent) {
    e.preventDefault();
    const query = q.trim();
    if (!query) return;
    nav(`${prefix}/search?q=${encodeURIComponent(query)}`);
  }

  return (
    <header className="topbar">
      <NavLink to={prefix || "/"} className="brand">
        <span className="brand-mark" />
        線上電影院
      </NavLink>
      <form className="search" onSubmit={onSearch}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value.slice(0, 50))}
          placeholder="搜尋劇名、關鍵字"
          maxLength={50}
          enterKeyHint="search"
        />
      </form>
      <label className="src-select">
        <span className="hide-sm">來源</span>
        <select
          className="field"
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            nav(prefix || "/");
          }}
        >
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      <nav className="nav">
        <NavLink to={`${prefix}/`} end>
          首頁
        </NavLink>
        <NavLink to={`${prefix}/#picks`} className="hide-sm">
          選片
        </NavLink>
        <NavLink to={`${prefix}/favorites`}>收藏</NavLink>
        {tv ? null : (
          <NavLink to="/settings" className="hide-sm">
            設定
          </NavLink>
        )}
      </nav>
    </header>
  );
}
