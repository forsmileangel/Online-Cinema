function pageItems(cur: number, last: number): (number | "gap")[] {
  if (last <= 9) return Array.from({ length: last }, (_, i) => i + 1);
  const keep = new Set([1, last, cur, cur - 1, cur + 1, cur - 2, cur + 2]);
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

export function Pager({
  page,
  pages,
  hasNext,
  onPage,
}: {
  page: number;
  pages?: number | null;
  hasNext?: boolean;
  onPage: (n: number) => void;
}) {
  const last = pages && pages > 1 ? pages : 0;
  if (last) {
    return (
      <div className="pager">
        <button className="btn alt" type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          上一頁
        </button>
        {pageItems(page, last).map((n, i) =>
          n === "gap" ? (
            <span key={`g${i}`} className="gap">
              …
            </span>
          ) : (
            <button
              key={n}
              type="button"
              className={n === page ? "btn" : "btn alt"}
              data-tv="1"
              onClick={() => onPage(n)}
            >
              {n}
            </button>
          ),
        )}
        <button className="btn alt" type="button" disabled={page >= last} onClick={() => onPage(page + 1)}>
          下一頁
        </button>
      </div>
    );
  }
  if (page <= 1 && !hasNext) return null;
  return (
    <div className="pager">
      {page > 1 ? (
        <button className="btn alt" type="button" onClick={() => onPage(page - 1)}>
          上一頁
        </button>
      ) : null}
      {hasNext ? (
        <button className="btn alt" type="button" onClick={() => onPage(page + 1)}>
          下一頁
        </button>
      ) : null}
    </div>
  );
}
