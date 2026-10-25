function pageItems(start: number, end: number, last: number): (number | "gap")[] {
  if (last <= 9) return Array.from({ length: last }, (_, i) => i + 1);
  const keep = new Set([1, last, start - 2, start - 1, start, end, end + 1, end + 2]);
  // A short run of shown pages is listed in full; a long one by its first and last page.
  if (end - start <= 4) for (let n = start; n <= end; n++) keep.add(n);
  const nums = [...keep].filter((n) => n >= 1 && n <= last).sort((a, b) => a - b);
  const out: (number | "gap")[] = [];
  let prev = 0;
  for (const n of nums) {
    if (prev && n - prev > 1) out.push("gap");
    out.push(n);
    prev = n;
  }
  return out;
}

/** 「第 3–7 頁（共 50 頁）」 beside a list's title; nothing for a list that fits on one page. */
export function PageRange({ start, end, pages, hasNext, line = false }: {
  start: number;
  end: number;
  pages?: number | null;
  hasNext?: boolean;
  /** On a line of its own rather than beside a heading. */
  line?: boolean;
}) {
  if (start === 1 && end === 1 && !hasNext && !(pages && pages > 1)) return null;
  return (
    <span className={line ? "page-range line" : "page-range"}>
      {end > start ? `第 ${start}–${end} 頁` : `第 ${start} 頁`}
      {pages && pages > 1 ? `（共 ${pages} 頁）` : ""}
    </span>
  );
}

/**
 * Jumps to another page. `page`..`end` are the pages already shown (the list grows by scrolling),
 * so 上一頁 goes before the first and 下一頁 after the last of them.
 */
export function Pager({
  page,
  end = page,
  pages,
  hasNext,
  onPage,
}: {
  page: number;
  end?: number;
  pages?: number | null;
  hasNext?: boolean;
  onPage: (n: number) => void;
}) {
  const last = pages && pages > 1 ? pages : 0;
  if (last) {
    return (
      <div className="pager">
        <button className="btn alt" type="button" data-tv="1" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          上一頁
        </button>
        {pageItems(page, end, last).map((n, i) =>
          n === "gap" ? (
            <span key={`g${i}`} className="gap">
              …
            </span>
          ) : (
            <button
              key={n}
              type="button"
              className={n >= page && n <= end ? "btn" : "btn alt"}
              aria-current={n >= page && n <= end ? "page" : undefined}
              data-tv="1"
              onClick={() => onPage(n)}
            >
              {n}
            </button>
          ),
        )}
        <button className="btn alt" type="button" data-tv="1" disabled={end >= last} onClick={() => onPage(end + 1)}>
          下一頁
        </button>
      </div>
    );
  }
  if (page <= 1 && !hasNext) return null;
  return (
    <div className="pager">
      {page > 1 ? (
        <button className="btn alt" type="button" data-tv="1" onClick={() => onPage(page - 1)}>
          上一頁
        </button>
      ) : null}
      {hasNext ? (
        <button className="btn alt" type="button" data-tv="1" onClick={() => onPage(end + 1)}>
          下一頁
        </button>
      ) : null}
    </div>
  );
}
