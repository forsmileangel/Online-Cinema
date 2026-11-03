import { memo, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Card } from "../types";
import { APP } from "../appConfig";
import { isPortrait, useSource, useZh } from "../source";
import { currentLocationKey, rememberOpened } from "../hooks/useScrollRestore";

const SRC_BADGE: Record<string, string> = {
  anigamer: "巴哈姆特動畫瘋",
  missav: "MissAV",
  jable: "Jable",
  pornhub: "Pornhub",
  netflav: "Netflav",
  huangguo: "黃果短劇",
  huangguo2: "黃果短劇2",
  hongguo: "紅果短劇",
  avple: "AVPLE",
  javday: "JAVDAY",
  ininav: "ininAV",
  duanju51: "51短劇",
  "5lxtv": "5lxtv 短劇",
  "5lxtv-av": "5lxtv 影片",
  "91md": "91麻豆",
  "123av": "123AV",
  chinaq: "中國人線上看",
  gimy: "Gimy 劇迷",
  mmov: "MMOV 電影線上看",
  mvffm: "MVFFM 電影線上看",
  dramaq: "DramaQ",
};

export function retriedCoverUrl(cover: string, token: string) {
  if (!token) return cover;
  try {
    const url = new URL(cover, window.location.href);
    if (url.origin !== window.location.origin || (url.pathname !== "/api/img" && !url.pathname.startsWith("/api/img/"))) return cover;
    url.searchParams.set("retry", token);
    return `${url.pathname}${url.search}`;
  } catch {
    return cover;
  }
}

// Memoised: a list of 1,500 cards must not repaint when a translation batch or the route changes.
export const PosterCard = memo(function PosterCard({ item, onRemove, removeLabel = "移除", showSource = true, priority = false }: {
  item: Card;
  onRemove?: () => void;
  removeLabel?: string;
  /** Lists from one source leave the source name out. */
  showSource?: boolean;
  /** Among the first covers on the page: fetched at once, ahead of the rest. */
  priority?: boolean;
}) {
  const { prefix, source, showOriginal } = useSource();
  const { zhMap, queueZh } = useZh();
  const pct = item.progress && item.progress > 0.02 ? Math.round(item.progress * 100) : 0;
  const orig = item.original_title || item.title;
  const cover = (item.cover || "").trim();
  const [coverState, setCoverState] = useState({ cover, attempts: 0, failed: false, token: "" });
  const retrySequence = useRef(0);
  const currentCoverState = coverState.cover === cover
    ? coverState
    : { cover, attempts: 0, failed: false, token: "" };
  useEffect(() => {
    if (APP.translateSource && (item.source || source) === APP.translateSource && !showOriginal && orig) queueZh(orig);
  }, [orig, showOriginal, queueZh, item.source, source]);
  const label = showOriginal ? orig : zhMap[orig] || orig;
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
  const href = `${prefix}/watch/${encodeURIComponent(item.source || APP.defaultSource)}/${encodeURIComponent(item.id)}`;
  const opened = () => rememberOpened(currentLocationKey(), href);
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
                loading={priority ? "eager" : "lazy"}
                fetchPriority={priority ? "high" : "auto"}
                decoding="async"
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
});
