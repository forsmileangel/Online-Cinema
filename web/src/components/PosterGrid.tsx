import type { Card } from "../types";
import { PosterCard } from "./PosterCard";

export function PosterGrid({ items, large = false }: { items: Card[]; large?: boolean }) {
  if (!items.length) return <div className="empty">沒有找到影片。</div>;
  return (
    <div className={large ? "grid large" : "grid"}>
      {items.map((item) => (
        <PosterCard key={item.id} item={item} />
      ))}
    </div>
  );
}
