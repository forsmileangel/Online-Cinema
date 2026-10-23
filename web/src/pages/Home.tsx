import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PosterRow } from "../components/PosterRow";
import { SkeletonRows } from "../components/PosterGrid";
import { api } from "../api";
import { useScrollRestore } from "../hooks/useScrollRestore";
import { useSource, withSrc } from "../source";
import type { HomePayload } from "../types";

export function Home() {
  const { source, prefix } = useSource();
  const [state, setState] = useState<{ source: string; data?: HomePayload; err: string }>(() => ({
    source,
    data: api.peekHome(source),
    err: "",
  }));
  const [retry, setRetry] = useState(0);

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
  useScrollRestore(!!data);

  if (!data && err) return <div className="err">載入失敗：{err}。<button className="btn alt" type="button" onClick={() => setRetry(retry + 1)}>重新載入</button></div>;
  if (!data) return <SkeletonRows rows={3} />;

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
      {data.picks?.length ? (
        <section className="picks" id="picks">
          <h2 className="h1">選片</h2>
          {data.picks.map((g) => (
            <div key={g.title} className="picks-group">
              <div className="picks-title">{g.title}</div>
              <div className="chips">
                {g.items.map((t) => (
                  <Link
                    key={`${t.kind}-${t.slug}`}
                    className="chip"
                    data-tv="1"
                    to={withSrc(t.slug ? `${prefix}/c/${t.kind}/${encodeURIComponent(t.slug)}` : `${prefix}/c/${t.kind}`, source)}
                  >
                    {t.name}
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </section>
      ) : null}
      <PosterRow title="繼續觀看" to={`/history?only=${encodeURIComponent(source)}`} items={data.continue_watching} />
      {data.rows.map((row) => (
        <PosterRow
          key={row.kind}
          title={row.title}
          to={withSrc(`/c/${row.kind}`, source)}
          items={row.items}
        />
      ))}
    </div>
  );
}
