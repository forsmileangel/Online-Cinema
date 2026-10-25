import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { PosterRow } from "../components/PosterRow";
import { SkeletonRows } from "../components/PosterGrid";
import { api } from "../api";
import { useGridMetrics } from "../hooks/useGridMetrics";
import { useScrollRestore } from "../hooks/useScrollRestore";
import { isPortrait, useSource, withSrc } from "../source";
import type { HomePayload, PickChip, PickGroup } from "../types";

/** 選片 folded to one line, so the first screen shows films; the top bar's 選片 link opens it in full. */
function PicksBar({ picks, source }: { picks: PickGroup[]; source: string }) {
  const { prefix, tv } = useSource();
  const loc = useLocation();
  const metrics = useGridMetrics();
  const asked = loc.hash === "#picks";
  const [open, setOpen] = useState(asked);
  const [tab, setTab] = useState(0);
  const ref = useRef<HTMLElement>(null);

  useLayoutEffect(() => {
    if (!asked) return;
    setOpen(true);
    // Also after a cold load, when the section did not exist yet as the link was followed.
    ref.current?.scrollIntoView({ block: "start" });
  }, [asked, loc.key]);

  const chipTo = (t: PickChip) => withSrc(t.slug ? `${prefix}/c/${t.kind}/${encodeURIComponent(t.slug)}` : `${prefix}/c/${t.kind}`, source);
  const chip = (t: PickChip) => (
    <Link key={`${t.kind}-${t.slug}`} className="chip" data-tv="1" to={chipTo(t)}>
      {t.name}
    </Link>
  );

  if (open) {
    return (
      <section className="picks" id="picks" ref={ref}>
        <div className="picks-bar">
          <h2>選片</h2>
          <button type="button" className="chip all" onClick={() => setOpen(false)}>收合 ▴</button>
        </div>
        {picks.map((g) => (
          <div key={g.title} className="picks-group">
            <div className="picks-title">{g.title}</div>
            <div className="chips">{g.items.map(chip)}</div>
          </div>
        ))}
      </section>
    );
  }

  const current = Math.min(tab, picks.length - 1);
  const limit = tv ? 8 : metrics && metrics.width < 560 ? 6 : 14;
  const shown = picks[current].items.slice(0, limit);
  const total = picks.reduce((n, g) => n + g.items.length, 0);
  return (
    <section className="picks folded" id="picks" ref={ref}>
      <div className="picks-bar">
        <h2>選片</h2>
        {picks.length > 1
          ? picks.map((g, i) => (
              <button key={g.title} type="button" className={`chip tab${i === current ? " on" : ""}`} aria-pressed={i === current} onClick={() => setTab(i)}>
                {g.title}
              </button>
            ))
          : null}
      </div>
      <div className="chips">
        {shown.map(chip)}
        {total > shown.length ? (
          <button type="button" className="chip all" onClick={() => setOpen(true)}>
            全部選片（{total}）▾
          </button>
        ) : null}
      </div>
    </section>
  );
}

export function Home() {
  const { source } = useSource();
  const [state, setState] = useState<{ source: string; data?: HomePayload; err: string }>(() => ({
    source,
    data: api.peekHome(source),
    err: "",
  }));
  const [retry, setRetry] = useState(0);
  const metrics = useGridMetrics();

  useEffect(() => {
    // A kept copy shows at once; it is still refreshed so 繼續觀看 reflects the latest progress.
    setState((prev) => (prev.source === source ? { ...prev, err: "" } : { source, data: api.peekHome(source), err: "" }));
    const controller = new AbortController();
    api
      .home(source, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setState({ source, data: next, err: "" }); })
      .catch((e: Error) => {
        if (!controller.signal.aborted) setState((prev) => ({ source, data: prev.source === source ? prev.data : undefined, err: e.message }));
      });
    return () => controller.abort();
  }, [source, retry]);

  const data = state.source === source ? state.data : api.peekHome(source);
  const err = state.source === source ? state.err : "";
  // Rows are cut to whole lines from the measured width, so restore only once that is known.
  useScrollRestore(!!data && metrics !== null);

  if (!data && err) return <div className="err">載入失敗：{err}。<button className="btn alt" type="button" onClick={() => setRetry(retry + 1)}>重新載入</button></div>;
  if (!data) return <SkeletonRows rows={3} portrait={isPortrait(source)} />;

  return (
    <div>
      {data.error ? (
        <div className="err" role="alert">
          {data.error}
          <button className="btn alt" type="button" onClick={() => setRetry(retry + 1)}>重新載入</button>
        </div>
      ) : null}
      {err ? (
        <p className="muted" role="status">
          片單更新失敗：{err}。<button className="btn alt" type="button" onClick={() => setRetry(retry + 1)}>重新載入</button>
        </p>
      ) : null}
      {data.picks?.length ? <PicksBar picks={data.picks} source={source} /> : null}
      <PosterRow title="繼續觀看" to={`/history?only=${encodeURIComponent(source)}`} items={data.continue_watching} lines={1} />
      {data.rows.map((row) => (
        <PosterRow
          key={row.kind}
          title={row.title}
          to={withSrc(`/c/${row.kind}`, source)}
          items={row.items}
          lines={2}
        />
      ))}
    </div>
  );
}
