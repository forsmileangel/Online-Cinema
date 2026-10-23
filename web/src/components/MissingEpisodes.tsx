import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useSource } from "../source";
import type { Alternative, Episode } from "../types";

/** 第7集 / 第 07 集 / 7 / EP7, from the label or else the id (same rules as the server). */
export function episodeNumber(title: string, id: string): number | null {
  for (const text of [title || "", id || ""]) {
    const match = /^\s*(?:第\s*)?0*(\d{1,4})\s*(?:集|話|话)?\s*$/.exec(text) || /第\s*0*(\d{1,4})\s*[集話话]/.exec(text)
      || /^\s*ep?\s*0*(\d{1,4})\s*$/i.exec(text);
    if (match) return Number(match[1]);
  }
  return null;
}

/** Numbers below the last episode that the list skips; empty when the labels are not all numbers. */
export function missingEpisodes(episodes: Episode[]): number[] {
  const numbers = episodes.map((e) => episodeNumber(e.title, e.id));
  if (!numbers.length || numbers.some((n) => n === null)) return [];
  const have = new Set(numbers as number[]);
  const top = Math.max(...(numbers as number[]));
  if (top > 400) return [];
  return Array.from({ length: top }, (_, i) => i + 1).filter((n) => !have.has(n));
}

/** [1,2,3,6] → "1–3、6" */
export function episodeRanges(numbers: number[]): string {
  const parts: string[] = [];
  for (let i = 0; i < numbers.length; i++) {
    let j = i;
    while (j + 1 < numbers.length && numbers[j + 1] === numbers[j] + 1) j++;
    parts.push(j > i ? `${numbers[i]}–${numbers[j]}` : `${numbers[i]}`);
    i = j;
  }
  return parts.join("、");
}

/** Says which episodes this source lacks and where else they can be watched. */
export function MissingEpisodes({ source, id, title, episodes }: { source: string; id: string; title: string; episodes: Episode[] }) {
  const { prefix } = useSource();
  const missing = useMemo(() => missingEpisodes(episodes), [episodes]);
  const key = `${source}\n${id}\n${missing.join(",")}`;
  const [found, setFound] = useState<{ key: string; items: Alternative[] | null }>({ key: "", items: null });

  useEffect(() => {
    if (!missing.length || !title) return;
    let alive = true;
    setFound({ key, items: null });
    api.alternatives(source, id, title)
      .then((r) => { if (alive) setFound({ key, items: r.items }); })
      .catch(() => { if (alive) setFound({ key, items: [] }); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, title]);

  if (!missing.length) return null;
  const items = found.key === key ? found.items : null;
  const useful = (items || []).filter((a) => a.episodes.some(([n]) => missing.includes(n)));
  return (
    <div className="ep-missing" role="status">
      <strong>這個來源缺第 {episodeRanges(missing)} 集。</strong>
      {items === null ? <span className="muted">正在找其他來源…</span> : null}
      {items !== null && !useful.length ? <span className="muted">其他同類來源也沒有找到這幾集。</span> : null}
      {useful.map((a) => {
        const first = a.episodes.find(([n]) => missing.includes(n));
        return (
          <Link key={`${a.source}:${a.id}`} data-tv="1"
            to={`${prefix}/watch/${encodeURIComponent(a.source)}/${encodeURIComponent(a.id)}${first ? `?ep=${encodeURIComponent(first[1])}` : ""}`}>
            {a.label} 有第 {episodeRanges(a.episodes.map(([n]) => n))} 集
          </Link>
        );
      })}
    </div>
  );
}
