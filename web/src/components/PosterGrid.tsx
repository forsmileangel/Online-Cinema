import { isPortrait, useSource } from "../source";
import type { Card } from "../types";
import { PosterCard } from "./PosterCard";

export function PosterGrid({
  items,
  large = false,
  busy = false,
  empty = "沒有找到影片。",
  onRemove,
  removeLabel,
}: {
  items: Card[];
  large?: boolean;
  /** Another page is loading; the current cards stay visible but dimmed. */
  busy?: boolean;
  empty?: string;
  onRemove?: (item: Card) => void;
  removeLabel?: string;
}) {
  const { tv } = useSource();
  if (!items.length) return <div className="empty">{empty}</div>;
  // Big cards are for a TV across the room; elsewhere the usual density shows more titles.
  const portrait = items.every((item) => isPortrait(item.source));
  const mixed = !portrait && items.some((item) => isPortrait(item.source));
  return (
    <div className={`grid${large && tv ? " large" : ""}${portrait ? " portrait" : mixed ? " mixed" : ""}${busy ? " is-busy" : ""}`} aria-busy={busy || undefined}>
      {items.map((item) => (
        <PosterCard
          key={`${item.source || ""}:${item.id}`}
          item={item}
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

export function SkeletonGrid({ large = false, count = 12 }: { large?: boolean; count?: number }) {
  return (
    <div className={`grid${large ? " large" : ""}`} aria-hidden>
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
