import { useCallback, useEffect, useRef, useState } from "react";
import type { SetURLSearchParams } from "react-router-dom";
import type { Card, Listing } from "../types";

// One view never grows past this many pages; the page numbers below still reach the rest.
const MAX_SPAN = 50;
// This many empty pages in a row end the list even when the source says there is more.
const MAX_EMPTY_RUN = 3;

type Page = { page: number; listing: Listing };

export type ListFailure = { page: number; message: string; retryAt: number };

type State = {
  id: string;
  list: string;
  pages: Page[];
  /** Load pages up to this one (restoring ?to=, or one more for each scroll to the bottom). */
  want: number;
  /** Automatic loads left before 繼續載入 has to be pressed. */
  budget: number;
  error?: ListFailure;
  /** A page that added no new titles: the source repeats itself past its end. */
  exhausted: boolean;
  /** Empty pages in a row (everything on them filtered out); a few are skipped, not taken as the end. */
  emptyRun: number;
  /** The previous start's cards, shown dimmed while a jump to another page loads. */
  stale?: Page[];
};

type Options = {
  /** Identity of the list without the page: source, kind, slug, query, sort. */
  list: string;
  /** First page shown (?page=). */
  start: number;
  /** Last page shown before (?to=), restored on Back. */
  to: number;
  peek: (page: number, stale: boolean) => Listing | undefined;
  load: (page: number, signal: AbortSignal) => Promise<Listing>;
  autoPages: number;
};

function continues(listing: Listing, page: number) {
  return listing.has_next && (!listing.pages || page < listing.pages);
}

/** Pages start..target that are still kept, as far as they run on without a gap. */
function fromCache(start: number, target: number, peek: Options["peek"]) {
  const pages: Page[] = [];
  for (let page = start; page <= target; page++) {
    const listing = peek(page, true);
    if (!listing) break;
    pages.push({ page, listing });
    if (!continues(listing, page)) break;
  }
  return pages;
}

function cardKey(item: Card) {
  return `${item.source || ""}:${item.id}`;
}

function failure(page: number, e: unknown): ListFailure {
  const err = e as Error & { retryAfterSeconds?: number };
  const wait = err?.retryAfterSeconds && err.retryAfterSeconds > 0 ? err.retryAfterSeconds : 0;
  return { page, message: err?.message || "載入失敗", retryAt: wait ? Date.now() + wait * 1000 : 0 };
}

/**
 * A list that grows a page at a time as the reader scrolls (Browse, single-source Search).
 * Pages load strictly one after another: several sources only reach page N through page N-1
 * (cursors, links seen on the previous page) and some rate-limit their requests.
 */
export function useAppendListing({ list, start, to, peek, load, autoPages }: Options) {
  const id = `${list}\n${start}`;
  const target = Math.min(Math.max(to, start), start + MAX_SPAN - 1);
  const [state, setState] = useState<State>(() => ({
    id, list, pages: fromCache(start, target, peek), want: target, budget: autoPages, exhausted: false, emptyRun: 0,
  }));
  const peekRef = useRef(peek);
  peekRef.current = peek;
  const loadRef = useRef(load);
  loadRef.current = load;

  // A new list, or a jump to another page of the same list.
  useEffect(() => {
    setState((prev) => (prev.id === id ? prev : {
      id, list, pages: fromCache(start, target, peekRef.current), want: target, budget: autoPages, exhausted: false, emptyRun: 0,
      stale: prev.list === list ? (prev.pages.length ? prev.pages : prev.stale) : undefined,
    }));
    // target and autoPages only matter when the list changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const current = state.id === id;
  const pages = current ? state.pages : [];
  // Until the effect above catches up with a jump, the cards on screen are already the stale ones.
  const stale = current ? state.stale : state.list === list ? (state.pages.length ? state.pages : state.stale) : undefined;
  const last = pages.length ? pages[pages.length - 1] : null;
  const next = last ? last.page + 1 : start;
  const canGrow = current && !state.exhausted && (!last || continues(last.listing, last.page)) && next < start + MAX_SPAN;
  const fetching = canGrow && !state.error && next <= state.want;

  useEffect(() => {
    if (!fetching) return;
    const controller = new AbortController();
    const kept = peekRef.current(next, false);
    (kept ? Promise.resolve(kept) : loadRef.current(next, controller.signal))
      .then((listing) => {
        if (controller.signal.aborted) return;
        setState((prev) => {
          if (prev.id !== id) return prev;
          const seen = new Set(prev.pages.flatMap((p) => p.listing.items.map(cardKey)));
          const empty = listing.items.length === 0;
          const repeated = !empty && prev.pages.length > 0 && listing.items.every((item) => seen.has(cardKey(item)));
          const emptyRun = empty ? prev.emptyRun + 1 : 0;
          return {
            ...prev,
            pages: [...prev.pages, { page: next, listing }],
            exhausted: repeated || emptyRun >= MAX_EMPTY_RUN,
            emptyRun,
            stale: undefined,
          };
        });
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setState((prev) => (prev.id === id ? { ...prev, error: failure(next, e), stale: prev.pages.length ? undefined : prev.stale } : prev));
      });
    return () => controller.abort();
  }, [id, fetching, next]);

  const more = useCallback((manual: boolean) => {
    setState((prev) => {
      if (prev.id !== id || prev.error) return prev;
      const lastPage = prev.pages.length ? prev.pages[prev.pages.length - 1].page : start - 1;
      if (prev.want > lastPage) return prev;
      if (!manual && prev.budget <= 0) return prev;
      return { ...prev, want: lastPage + 1, budget: (manual ? autoPages : prev.budget) - 1 };
    });
  }, [id, start, autoPages]);

  const retry = useCallback(() => {
    setState((prev) => (prev.id === id ? { ...prev, error: undefined } : prev));
  }, [id]);

  const shown = pages.length ? pages : stale || [];
  const seen = new Set<string>();
  const items: Card[] = [];
  for (const page of shown) {
    for (const item of page.listing.items) {
      const key = cardKey(item);
      if (seen.has(key)) continue;
      seen.add(key);
      items.push(item);
    }
  }
  const head = shown[0]?.listing;
  const total = shown.reduce((n, p) => Math.max(n, p.listing.pages || 0), 0) || undefined;
  const error = current ? state.error : undefined;
  return {
    /** First page of the view: title and notice. */
    head,
    items,
    /** First and last page now shown. */
    start,
    end: last ? last.page : start,
    pages: total,
    hasNext: canGrow,
    /** The next page is on its way (by scrolling, a button, or restoring ?to=). */
    loading: fetching ? next : 0,
    error,
    /** Automatic loading paused; 繼續載入 asks for the next page. */
    paused: canGrow && !fetching && !error && state.budget <= 0,
    /** Cards of the page jumped from, shown dimmed until the new page arrives. */
    busy: !pages.length && !!stale?.length,
    /** Everything Back asked for is shown (or loading stopped), so the scroll position can be restored. */
    ready: current && !!last && (last.page >= target || !canGrow || !!error),
    more,
    retry,
  };
}

export type AppendListing = ReturnType<typeof useAppendListing>;

/** Keeps ?to= in step with the pages shown, so Back brings the whole view back. */
export function useRangeInUrl(view: AppendListing, params: URLSearchParams, setParams: SetURLSearchParams) {
  const { ready, end, start } = view;
  useEffect(() => {
    if (!ready) return;
    const want = end > start ? String(end) : null;
    if (params.get("to") === want) return;
    const next = new URLSearchParams(params);
    if (want) next.set("to", want);
    else next.delete("to");
    // keepScroll: only the remembered range changes; the reader stays where they are.
    setParams(next, { replace: true, state: { keepScroll: true } });
  }, [ready, end, start, params, setParams]);
}

export function pageParam(raw: string | null) {
  return Math.max(1, Math.floor(Number(raw) || 1));
}
