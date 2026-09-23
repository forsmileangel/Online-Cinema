import { Link } from "react-router-dom";
import type { Card } from "../types";
import { useSource } from "../source";

const SRC_BADGE: Record<string, string> = {
  hongguo: "紅果短劇",
  chinaq: "中國人線上看",
  gimy: "Gimy 劇迷",
  dramaq: "DramaQ",
};

export function PosterCard({ item }: { item: Card }) {
  const { prefix, zhMap } = useSource();
  const pct = item.progress && item.progress > 0.02 ? Math.round(item.progress * 100) : 0;
  const orig = item.original_title || item.title;
  const label = zhMap[orig] || orig;
  return (
    <Link
      className="card"
      data-tv="1"
      to={`${prefix}/watch/${encodeURIComponent(item.source || "hongguo")}/${encodeURIComponent(item.id)}`}
    >
      <div className="poster">
        <img src={item.cover} alt="" loading="lazy" referrerPolicy="no-referrer" />
        <div className="play" aria-hidden>
          ▶
        </div>
        {item.duration ? <span className="dur">{item.duration}</span> : null}
        {pct ? (
          <div className="prog">
            <span style={{ width: `${pct}%` }} />
          </div>
        ) : null}
      </div>
      <div className="title">{label}</div>
      {item.source ? <div className="badge-src">{SRC_BADGE[item.source] || item.source}</div> : null}
    </Link>
  );
}
