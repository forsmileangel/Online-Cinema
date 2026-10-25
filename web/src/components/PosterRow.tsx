import { Link } from "react-router-dom";
import type { Card } from "../types";
import { PosterCard } from "./PosterCard";
import { gridShape } from "./PosterGrid";
import { columnsFor, useGridMetrics } from "../hooks/useGridMetrics";
import { useSource } from "../source";

export function PosterRow({
  title,
  to,
  items,
  moreLabel = "更多",
  note,
  lines,
}: {
  title: string;
  to?: string;
  items: Card[];
  moreLabel?: string;
  note?: string | null;
  /** Show only this many full lines of cards (the rest is behind "更多"); all cards when unset. */
  lines?: number;
}) {
  const { prefix } = useSource();
  const metrics = useGridMetrics();
  if (!items.length) return null;
  const href = to ? (to.startsWith("/") ? `${prefix}${to}` : to) : undefined;
  const shape = gridShape(items);
  // Cards past the last line are not rendered at all, so a TV remote never focuses a hidden one.
  const cols = lines && metrics ? columnsFor(metrics, shape) : null;
  const shown = lines ? items.slice(0, (cols ?? 8) * lines) : items;
  return (
    <section className="home-row">
      <div className="row-head">
        <h2>{title}</h2>
        {href ? (
          <Link className="more" to={href} data-tv="1">
            {moreLabel}
          </Link>
        ) : null}
      </div>
      {note ? <p className="row-note">{note}</p> : null}
      <div className={`grid${shape}`} style={cols ? { gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` } : undefined}>
        {shown.map((item) => (
          <PosterCard key={`${item.source || ""}:${item.id}`} item={item} showSource={false} />
        ))}
      </div>
    </section>
  );
}
