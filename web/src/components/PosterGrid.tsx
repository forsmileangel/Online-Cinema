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

function SkeletonCard() {
  return (
    <div className="card skeleton">
      <div className="poster" />
      <span className="skeleton-line" />
      <span className="skeleton-line short" />
    </div>
  );
}

export function SkeletonGrid({ count = 12 }: { count?: number }) {
  return (
    <div className="grid" aria-hidden>
      {Array.from({ length: count }, (_, i) => <SkeletonCard key={i} />)}
    </div>
  );
}

export function SkeletonRows({ rows = 2 }: { rows?: number }) {
  return (
    <div aria-hidden>
      {Array.from({ length: rows }, (_, row) => (
        <section className="home-row" key={row}>
          <div className="row-head">
            <span className="skeleton-line" />
          </div>
          <div className="scroller">
            {Array.from({ length: 6 }, (_, i) => <SkeletonCard key={i} />)}
          </div>
        </section>
      ))}
    </div>
  );
}
