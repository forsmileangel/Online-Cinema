import { useEffect, useRef, useState } from "react";
import type { AppendListing } from "../hooks/useAppendListing";
import { columnsFor, useGridMetrics } from "../hooks/useGridMetrics";
import { gridShape, SkeletonGrid } from "./PosterGrid";

/** Seconds left until `at` (epoch ms), re-rendering once a second until then; 0 when past or unset. */
function useSecondsLeft(at: number) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!at || at <= Date.now()) return;
    const timer = window.setInterval(() => {
      setTick((n) => n + 1);
      if (Date.now() >= at) window.clearInterval(timer);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [at]);
  const left = at - Date.now();
  return left > 0 ? Math.ceil(left / 1000) : 0;
}

/** The end of a growing list: asks for the next page as it comes near, or says why it stopped. */
export function ListingMore({ view }: { view: AppendListing }) {
  const sentinel = useRef<HTMLDivElement>(null);
  const metrics = useGridMetrics();
  const observable = typeof IntersectionObserver !== "undefined";
  const armed = observable && view.ready && view.hasNext && !view.loading && !view.error && !view.paused;
  const { more, end } = view;
  const wait = useSecondsLeft(view.error?.retryAt || 0);

  useEffect(() => {
    const el = sentinel.current;
    if (!armed || !el) return;
    // Re-created after every page, so a screen taller than the list keeps filling up.
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) more(false);
    }, { rootMargin: "0px 0px 800px 0px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, [armed, more, end]);

  const shape = gridShape(view.items);
  const line = metrics ? columnsFor(metrics, shape) : 6;
  const appendError = view.error && view.error.page > view.start ? view.error : undefined;
  const button = view.ready && view.hasNext && !view.loading && !view.error && (view.paused || !observable);
  return (
    <div className="list-more">
      <div ref={sentinel} aria-hidden />
      {view.loading && view.items.length ? (
        <>
          <p className="muted" role="status">載入第 {view.loading} 頁…</p>
          <SkeletonGrid count={line} portrait={shape === " portrait"} />
        </>
      ) : null}
      {appendError ? (
        <div className="err" role="alert">
          第 {appendError.page} 頁載入失敗：{appendError.message}。
          <button className="btn alt" type="button" data-tv="1" disabled={wait > 0} onClick={view.retry}>
            {wait > 0 ? `${wait} 秒後可重試` : "重試"}
          </button>
        </div>
      ) : null}
      {button ? (
        <button className="btn alt" type="button" data-tv="1" onClick={() => more(true)}>
          繼續載入（第 {end + 1} 頁）
        </button>
      ) : null}
      {view.ready && !view.hasNext && !view.error && end > view.start ? (
        <p className="muted">已顯示全部 {view.items.length} 部</p>
      ) : null}
    </div>
  );
}
