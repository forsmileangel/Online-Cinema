import { Link } from "react-router-dom";
import type { Card } from "../types";
import { PosterCard } from "./PosterCard";
import { useSource } from "../source";

export function PosterRow({
  title,
  to,
  items,
}: {
  title: string;
  to?: string;
  items: Card[];
}) {
  const { prefix } = useSource();
  if (!items.length) return null;
  const href = to ? (to.startsWith("/") ? `${prefix}${to}` : to) : undefined;
  return (
    <section className="home-row">
      <div className="row-head">
        <h2>{title}</h2>
        {href ? (
          <Link className="more" to={href} data-tv="1">
            更多
          </Link>
        ) : null}
      </div>
      <div className="scroller">
        {items.map((item) => (
          <PosterCard key={item.id} item={item} />
        ))}
      </div>
    </section>
  );
}
