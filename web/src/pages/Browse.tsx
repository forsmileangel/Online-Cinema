import { useParams, useSearchParams } from "react-router-dom";
import { ListingMore } from "../components/ListingMore";
import { PageRange, Pager } from "../components/Pager";
import { PosterGrid, SkeletonGrid } from "../components/PosterGrid";
import { api } from "../api";
import { BROWSE_TITLES } from "../browseTitles";
import { pageParam, useAppendListing, useRangeInUrl } from "../hooks/useAppendListing";
import { useScrollRestore } from "../hooks/useScrollRestore";
import { usePageSource, useSource } from "../source";

export function Browse() {
  const source = usePageSource();
  const { tv } = useSource();
  const { kind = "", slug } = useParams();
  const [params, setParams] = useSearchParams();
  const page = pageParam(params.get("page"));
  const view = useAppendListing({
    list: `${source}\n${kind}\n${slug || ""}`,
    start: page,
    to: pageParam(params.get("to")),
    peek: (n, back) => api.peekBrowse(kind, slug, n, source, back),
    load: (n, signal) => api.browse(kind, slug, n, source, signal),
    autoPages: tv ? 5 : 10,
  });
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
      {view.head.notice ? <p className="muted" role="status">{view.head.notice}</p> : null}
      {view.busy ? <p className="muted" role="status">載入第 {page} 頁…</p> : null}
      <PosterGrid items={view.items} busy={view.busy} />
      <ListingMore view={view} />
      <Pager page={page} end={view.end} pages={view.pages} hasNext={view.hasNext} onPage={jump} />
    </div>
  );
}
