import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Pager } from "../components/Pager";
import { PosterGrid } from "../components/PosterGrid";
import { api } from "../api";
import { useSource } from "../source";
import type { Listing } from "../types";

export function Search() {
  const { source } = useSource();
  const [params] = useSearchParams();
  const q = params.get("q") || "";
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Listing | null>(null);
  const [err, setErr] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    setPage(1);
  }, [q, source]);

  useEffect(() => {
    if (!q) return;
    setErr("");
    setData(null);
    const controller = new AbortController();
    api
      .search(q, page, source, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setData(next); })
      .catch((e: Error) => { if (!controller.signal.aborted) setErr(e.message); });
    return () => controller.abort();
  }, [q, page, source, retry]);

  if (!q) return <div className="empty">輸入劇名或關鍵字開始搜尋。</div>;
  if (err) return <div className="err">{err}。<button className="btn alt" type="button" onClick={() => setRetry(retry + 1)}>重新搜尋</button></div>;
  if (!data) return <div className="empty">搜尋中…</div>;

  return (
    <div>
      <h1 className="h1">搜尋「{q}」</h1>
      {data.notice ? <p className="muted" role="status">{data.notice}</p> : null}
      <PosterGrid items={data.items} large />
      <Pager page={page} pages={data.pages} hasNext={data.has_next} onPage={setPage} />
    </div>
  );
}
