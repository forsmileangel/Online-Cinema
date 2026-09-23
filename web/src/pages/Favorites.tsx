import { useEffect, useState } from "react";
import { PosterGrid } from "../components/PosterGrid";
import { api } from "../api";
import type { Card } from "../types";

export function Favorites() {
  const [items, setItems] = useState<Card[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .favorites()
      .then((r) => setItems(r.items))
      .catch((e: Error) => setErr(e.message));
  }, []);

  if (err) return <div className="err">{err}</div>;
  if (!items) return <div className="empty">載入收藏…</div>;

  return (
    <div>
      <h1 className="h1">收藏</h1>
      <PosterGrid items={items} />
    </div>
  );
}
