import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PosterRow } from "../components/PosterRow";
import { api } from "../api";
import { useSource } from "../source";
import type { HomePayload } from "../types";

export function Home() {
  const { source, prefix } = useSource();
  const [data, setData] = useState<HomePayload | null>(null);
  const [err, setErr] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    setData(null);
    setErr("");
    const controller = new AbortController();
    api
      .home(source, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setData(next); })
      .catch((e: Error) => { if (!controller.signal.aborted) setErr(e.message); });
    return () => controller.abort();
  }, [source, retry]);

  if (err) return <div className="err">載入失敗：{err}。<button className="btn alt" type="button" onClick={() => setRetry(retry + 1)}>重新載入</button></div>;
  if (!data) return <div className="empty">載入片單中…</div>;

  return (
    <div>
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
                    to={t.slug ? `${prefix}/c/${t.kind}/${encodeURIComponent(t.slug)}` : `${prefix}/c/${t.kind}`}
                  >
                    {t.name}
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </section>
      ) : null}
      <PosterRow title="繼續觀看" items={data.continue_watching} />
      {data.rows.map((row) => (
        <PosterRow
          key={row.kind}
          title={row.title}
          to={`/c/${row.kind}`}
          items={row.items}
        />
      ))}
    </div>
  );
}
