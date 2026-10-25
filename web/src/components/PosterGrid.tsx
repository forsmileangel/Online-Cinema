import { columnsFor, useGridMetrics } from "../hooks/useGridMetrics";
import { isPortrait } from "../source";
import type { Card } from "../types";
import { PosterCard } from "./PosterCard";

/** The card shape a list needs: all posters, all stills, or a mix of both. */
export function gridShape(items: Card[]): "" | " portrait" | " mixed" {
  const portrait = items.every((item) => isPortrait(item.source));
  if (portrait) return " portrait";
  return items.some((item) => isPortrait(item.source)) ? " mixed" : "";
}

export function PosterGrid({
  items,
  busy = false,
  empty = "沒有找到影片。",
  onRemove,
  removeLabel,
}: {
  items: Card[];
  /** Another page is loading; the current cards stay visible but dimmed. */
  busy?: boolean;
  empty?: string;
  onRemove?: (item: Card) => void;
  removeLabel?: string;
}) {
  if (!items.length) return <div className="empty">{empty}</div>;
  // A source name under every card only helps when the list mixes sources.
  const showSource = new Set(items.map((item) => item.source || "")).size > 1;
  return (
    <div className={`grid${gridShape(items)}${busy ? " is-busy" : ""}`} aria-busy={busy || undefined}>
      {items.map((item) => (
        <PosterCard
          key={`${item.source || ""}:${item.id}`}
          item={item}
          showSource={showSource}
          onRemove={onRemove ? () => onRemove(item) : undefined}
          removeLabel={removeLabel}
        />
      ))}
    </div>
  );
}

function SkeletonCard({ portrait = false }: { portrait?: boolean }) {
  return (
    <div className={`card skeleton${portrait ? " portrait" : ""}`}>
      <div className={`poster${portrait ? " portrait" : ""}`} />
      <span className="skeleton-line" />
      <span className="skeleton-line short" />
    </div>
  );
}

export function SkeletonGrid({ count = 12, portrait = false }: { count?: number; portrait?: boolean }) {
  return (
    <div className={`grid${portrait ? " portrait" : ""}`} aria-hidden>
      {Array.from({ length: count }, (_, i) => <SkeletonCard key={i} portrait={portrait} />)}
    </div>
  );
}

/** Placeholder home rows: two lines of cards in the shape the source uses. */
export function SkeletonRows({ rows = 2, portrait = false }: { rows?: number; portrait?: boolean }) {
  const metrics = useGridMetrics();
  const shape = portrait ? " portrait" : "";
  const cols = metrics ? columnsFor(metrics, shape) : null;
  return (
    <div aria-hidden>
      {Array.from({ length: rows }, (_, row) => (
        <section className="home-row" key={row}>
          <div className="row-head">
            <span className="skeleton-line" />
          </div>
          <div className={`grid${shape}`} style={cols ? { gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` } : undefined}>
            {Array.from({ length: (cols ?? 8) * 2 }, (_, i) => <SkeletonCard key={i} portrait={portrait} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
