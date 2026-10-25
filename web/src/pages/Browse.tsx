import { useRef } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ListingMore } from "../components/ListingMore";
import { PageRange, Pager } from "../components/Pager";
import { PosterGrid, SkeletonGrid } from "../components/PosterGrid";
import { api } from "../api";
import { BROWSE_TITLES } from "../browseTitles";
import { pageParam, useAppendListing, useRangeInUrl } from "../hooks/useAppendListing";
import { useScrollRestore } from "../hooks/useScrollRestore";
import { usePageSource, useSource, withSrc } from "../source";
import type { SortOption } from "../types";

/** The source's own orders for this list; a change starts again from page 1 at the top. */
function SortBar({ sorts, current, params, source }: {
  sorts: SortOption[]; current: string; params: URLSearchParams; source: string;
}) {
  const { prefix } = useSource();
  if (sorts.length < 2) return null;
  const orders = sorts.filter((o) => !o.kind);
  const target = (o: SortOption) => {
    if (o.kind) return withSrc(`${prefix}/c/${o.kind}${o.slug ? `/${encodeURIComponent(o.slug)}` : ""}`, source);
    const next = new URLSearchParams(params);
    next.delete("page");
    next.delete("to");
    // The site's default order needs no ?sort=, so links from before sorting existed match it.
    if (o.id === orders[0]?.id) next.delete("sort");
    else next.set("sort", o.id);
    return { search: `?${next}` };
  };
  return (
    <nav className="chips sort-bar" aria-label="排序">
      {sorts.map((o) => (
        <Link key={o.id} className={o.id === current ? "chip on" : "chip"} aria-current={o.id === current ? "true" : undefined}
              data-tv="1" to={target(o)}>
          {o.label}
        </Link>
      ))}
    </nav>
  );
}

export function Browse() {
  const source = usePageSource();
  const { tv } = useSource();
  const { kind = "", slug } = useParams();
  const [params, setParams] = useSearchParams();
  const page = pageParam(params.get("page"));
  const sort = params.get("sort") || "";
  const list = `${source}\n${kind}\n${slug || ""}`;
  const view = useAppendListing({
    list: `${list}\n${sort}`,
    start: page,
    to: pageParam(params.get("to")),
    peek: (n, back) => api.peekBrowse(kind, slug, n, source, back, sort),
    load: (n, signal) => api.browse(kind, slug, n, source, signal, sort),
    autoPages: tv ? 5 : 10,
  });
  // While another order loads, its options stay on screen with the new one marked.
  const sortsSeen = useRef<{ list: string; sorts: SortOption[] }>({ list: "", sorts: [] });
  if (view.head?.sorts) sortsSeen.current = { list, sorts: view.head.sorts };
  const sorts = sortsSeen.current.list === list ? sortsSeen.current.sorts : [];
  const firstOrder = sorts.find((o) => !o.kind)?.id || "";
  const currentSort = view.head?.sort ?? (sort || firstOrder);
  const sortBar = <SortBar sorts={sorts} current={currentSort} params={params} source={source} />;
  useScrollRestore(view.ready);
  useRangeInUrl(view, params, setParams);

  function jump(n: number) {
    const next = new URLSearchParams(params);
    next.delete("to");
    if (n > 1) next.set("page", String(n));
    else next.delete("page");
    setParams(next);
  }

  const heading = view.head?.title || (slug ? decodeURIComponent(slug) : "") || BROWSE_TITLES[kind] || kind;
  if (view.error && view.error.page === page) {
    return (
      <div className="err">
        {view.error.message}。<button className="btn alt" type="button" onClick={view.retry}>重新載入</button>
        {page > 1 ? <button className="btn alt" type="button" onClick={() => jump(1)}>從第 1 頁開始</button> : null}
      </div>
    );
  }
  if (!view.head) {
    return (
      <div>
        <h1 className="h1">{heading}</h1>
        {sortBar}
        <SkeletonGrid />
      </div>
    );
  }

  return (
    <div>
      <h1 className="h1">
        {heading}
        <PageRange start={page} end={view.end} pages={view.pages} hasNext={view.hasNext} />
      </h1>
      {sortBar}
      {view.head.notice ? <p className="muted" role="status">{view.head.notice}</p> : null}
      {view.busy ? <p className="muted" role="status">載入第 {page} 頁…</p> : null}
      <PosterGrid items={view.items} busy={view.busy} />
      <ListingMore view={view} />
      <Pager page={page} end={view.end} pages={view.pages} hasNext={view.hasNext} onPage={jump} />
    </div>
  );
}
