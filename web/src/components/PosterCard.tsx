import { useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { Card } from "../types";
import { isPortrait, useSource } from "../source";
import { rememberOpened } from "../hooks/useScrollRestore";

const SRC_BADGE: Record<string, string> = {
  anigamer: "巴哈姆特動畫瘋",
  hongguo: "紅果短劇",
  chinaq: "中國人線上看",
  gimy: "Gimy 劇迷",
  mmov: "MMOV 電影線上看",
  mvffm: "MVFFM 電影線上看",
  dramaq: "DramaQ",
};

function retriedCoverUrl(cover: string, token: string) {
  if (!token) return cover;
  try {
    const url = new URL(cover, window.location.href);
    if (url.origin !== window.location.origin || url.pathname !== "/api/img") return cover;
    url.searchParams.set("retry", token);
    return `${url.pathname}${url.search}`;
  } catch {
    return cover;
  }
}

export function PosterCard({ item, onRemove, removeLabel = "移除", showSource = true }: {
  item: Card;
  onRemove?: () => void;
  removeLabel?: string;
  /** Lists from one source leave the source name out. */
  showSource?: boolean;
}) {
  const { prefix, zhMap } = useSource();
  const loc = useLocation();
  const pct = item.progress && item.progress > 0.02 ? Math.round(item.progress * 100) : 0;
  const orig = item.original_title || item.title;
  const cover = (item.cover || "").trim();
  const [coverState, setCoverState] = useState({ cover, attempts: 0, failed: false, token: "" });
  const retrySequence = useRef(0);
  const currentCoverState = coverState.cover === cover
    ? coverState
    : { cover, attempts: 0, failed: false, token: "" };
  const label = zhMap[orig] || orig;
  const retryToken = () => `${Date.now()}-${++retrySequence.current}`;
  const onCoverError = () => {
    const token = retryToken();
    setCoverState((previous) => {
      const current = previous.cover === cover
        ? previous
        : { cover, attempts: 0, failed: false, token: "" };
      return current.attempts === 0
        ? { ...current, attempts: 1, token }
        : { ...current, failed: true };
    });
  };
  const onManualRetry = () => {
    const token = retryToken();
    setCoverState((previous) => {
      const current = previous.cover === cover
        ? previous
        : { cover, attempts: 0, failed: false, token: "" };
      return { ...current, attempts: current.attempts + 1, failed: false, token };
    });
  };
  const href = `${prefix}/watch/${encodeURIComponent(item.source || "hongguo")}/${encodeURIComponent(item.id)}`;
  const opened = () => rememberOpened(loc.key, href);
  return (
    <div className={`card${isPortrait(item.source) ? " portrait" : ""}`}>
      <div className="poster-shell">
        <Link className="poster-link" data-tv="1" to={href} aria-label={label} onClick={opened}>
          <div className={`poster${isPortrait(item.source) ? " portrait" : ""}`}>
            {cover && !currentCoverState.failed ? (
              <img
                key={`${cover}-${currentCoverState.token}`}
                src={retriedCoverUrl(cover, currentCoverState.token)}
                alt=""
                loading="lazy"
                referrerPolicy="no-referrer"
                onError={onCoverError}
              />
            ) : (
              <div className="poster-placeholder">
                {cover ? "縮圖載入失敗" : "暫無縮圖"}
              </div>
            )}
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
        </Link>
        {onRemove ? (
          <button
            className="card-remove"
            type="button"
            data-tv="1"
            title={removeLabel}
            aria-label={`${removeLabel}：${label}`}
            onClick={onRemove}
          >
            ✕
          </button>
        ) : null}
        {cover && currentCoverState.failed ? (
          <button
            className="btn alt poster-retry"
            type="button"
            data-tv="1"
            aria-label={`重新載入縮圖：${label}`}
            onClick={onManualRetry}
          >
            重試縮圖
          </button>
        ) : null}
      </div>
      <Link className="card-title-link" to={href} tabIndex={-1} onClick={opened}>
        <div className="title" title={label}>{label}</div>
      </Link>
      {showSource && item.source ? <div className="badge-src">{SRC_BADGE[item.source] || item.source}</div> : null}
    </div>
  );
}
