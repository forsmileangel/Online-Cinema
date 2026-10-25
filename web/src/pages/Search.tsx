import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MultiSearch } from "../components/MultiSearch";
import { Pager } from "../components/Pager";
import { PosterGrid, SkeletonGrid } from "../components/PosterGrid";
import { api } from "../api";
import { useListing } from "../hooks/useListing";
import { useScrollRestore } from "../hooks/useScrollRestore";
import { clearRecent, loadRecent, loadScope, parseScope, saveScope, searchPath, type Scope } from "../searchPrefs";
import { sourceGroup, sourceLabel, usePageSource, useSource } from "../source";

export function Search() {
  const [params] = useSearchParams();
  const src = usePageSource();
  const q = (params.get("q") || "").trim();
  const scope = parseScope(params.get("scope"));
  const page = Math.max(1, Math.floor(Number(params.get("page")) || 1));

  if (!q) return <RecentSearches src={src} />;

  return (
    <div>
      <h1 className="h1">搜尋「{q}」</h1>
      <ScopeSwitch q={q} src={src} scope={scope} />
      {scope === "source" ? <SourceResults q={q} src={src} page={page} /> : <MultiSearch q={q} src={src} scope={scope} />}
    </div>
  );
}

function ScopeSwitch({ q, src, scope }: { q: string; src: string; scope: Scope }) {
  const { sources, prefix } = useSource();
  const group = sourceGroup(sources, src);
  const sameGroup = sources.filter((s) => s.group === group).length;
  const options: [Scope, string][] = [["source", `目前來源：${sourceLabel(sources, src)}`]];
  if (group && sameGroup > 1) options.push(["group", `${group}（${sameGroup} 個來源）`]);
  options.push(["all", `全部來源（${sources.length}）`]);
  return (
    <div className="chips scope-switch" role="group" aria-label="搜尋範圍">
      {options.map(([value, text]) => (
        <Link
          key={value}
          className={value === scope ? "chip on" : "chip"}
          aria-current={value === scope ? "true" : undefined}
          data-tv="1"
          to={searchPath(prefix, q, src, value)}
          onClick={() => saveScope(value)}
        >
          {text}
        </Link>
      ))}
    </div>
  );
}

function SourceResults({ q, src, page }: { q: string; src: string; page: number }) {
  const [params, setParams] = useSearchParams();
  const { sources } = useSource();
  const { data, err, busy, ready, retry } = useListing(
    `${src}\n${q}\n${page}`,
    `${src}\n${q}`,
    () => api.peekSearch(q, page, src),
    (signal) => api.search(q, page, src, signal),
  );
  useScrollRestore(ready);

  function setPage(n: number) {
    const next = new URLSearchParams(params);
    if (n > 1) next.set("page", String(n));
    else next.delete("page");
    setParams(next);
  }

  if (err) {
    return (
      <div>
        <div className="err">
          {err}。
          <button className="btn alt" type="button" onClick={retry}>
            重新搜尋
          </button>
        </div>
        <TryElsewhere q={q} src={src} />
      </div>
    );
  }
  if (!data) {
    return (
      <div>
        <p className="muted" role="status">搜尋中…</p>
        <SkeletonGrid />
      </div>
    );
  }
  return (
    <div>
      {data.notice ? <p className="muted" role="status">{data.notice}</p> : null}
      {busy ? <p className="muted" role="status">載入第 {page} 頁…</p> : null}
      {data.items.length ? (
        <PosterGrid items={data.items} busy={busy} />
      ) : (
        <div>
          <div className="empty">{sourceLabel(sources, src)} 沒有找到「{q}」。</div>
          <TryElsewhere q={q} src={src} />
        </div>
      )}
      <Pager page={page} pages={data.pages} hasNext={data.has_next} onPage={setPage} />
    </div>
  );
}

/** Next steps when the chosen source found nothing or could not search. */
function TryElsewhere({ q, src }: { q: string; src: string }) {
  const { sources, prefix } = useSource();
  const group = sourceGroup(sources, src);
  const others = sources.filter((s) => s.group === group && s.id !== src);
  return (
    <div className="try-elsewhere">
      <div className="chips">
        {others.length ? (
          <Link className="chip on" data-tv="1" to={searchPath(prefix, q, src, "group")}>
            搜尋所有{group}來源（{others.length + 1}）
          </Link>
        ) : null}
        <Link className={others.length ? "chip" : "chip on"} data-tv="1" to={searchPath(prefix, q, src, "all")}>
          搜尋全部來源
        </Link>
      </div>
      {others.length ? (
        <div className="chips">
          {others.map((s) => (
            <Link key={s.id} className="chip" data-tv="1" to={searchPath(prefix, q, s.id)}>
              改搜 {s.label}
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function RecentSearches({ src }: { src: string }) {
  const { prefix } = useSource();
  const [recent, setRecent] = useState(loadRecent);
  return (
    <div>
      <div className="empty">輸入劇名或關鍵字開始搜尋。</div>
      {recent.length ? (
        <section className="picks">
          <div className="picks-title">最近搜尋</div>
          <div className="chips">
            {recent.map((r) => (
              <Link key={r} className="chip" data-tv="1" to={searchPath(prefix, r, src, loadScope())}>
                {r}
              </Link>
            ))}
            <button
              type="button"
              className="chip"
              data-tv="1"
              onClick={() => {
                clearRecent();
                setRecent([]);
              }}
            >
              清除
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
