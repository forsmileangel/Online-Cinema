import { createContext, useContext, useLayoutEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useSource } from "../source";

/**
 * Content width of <main> and the card sizes styles.css resolves there (card-size setting, phone, TV),
 * plus the page they were measured for.
 */
export type GridMetrics = { width: number; min: number; minP: number; gap: number; at: string };

/** Which page a measurement belongs to: pages differ in width (padding, a scrollbar or not) and TV sizes. */
export function measureKey(pathname: string, tv: boolean) {
  return `${pathname}|${tv}`;
}

export const GridMetricsContext = createContext<GridMetrics | null>(null);

function readMetrics(main: HTMLElement, at: string): GridMetrics {
  const style = getComputedStyle(main);
  const px = (name: string, fallback: number) => parseFloat(style.getPropertyValue(name)) || fallback;
  const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
  return {
    width: main.clientWidth - padding,
    min: px("--card-min", 184),
    minP: px("--card-min-p", 128),
    gap: px("--card-gap", 14),
    at,
  };
}

function same(a: GridMetrics, b: GridMetrics) {
  return a.width === b.width && a.min === b.min && a.minP === b.minP && a.gap === b.gap && a.at === b.at;
}

/**
 * Measures <main> before paint, so rows of cards can be cut to whole lines on their first render.
 * `page` (from measureKey) re-reads the sizes after navigation; the card-size setting has no resize to observe.
 */
export function useMeasureGrid(main: HTMLElement | null, page: string) {
  const [metrics, setMetrics] = useState<GridMetrics | null>(null);
  useLayoutEffect(() => {
    if (!main) return;
    const update = () => {
      const next = readMetrics(main, page);
      setMetrics((prev) => (prev && same(prev, next) ? prev : next));
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(main);
    return () => observer.disconnect();
  }, [main, page]);
  return main ? metrics : null;
}

/**
 * The measurement for the page being shown, or null until <main> has been measured for it.
 * Right after Back the previous page's sizes are still around; cutting rows (and restoring the
 * scroll position) with them would shift the page a moment later.
 */
export function useGridMetrics() {
  const metrics = useContext(GridMetricsContext);
  const { pathname } = useLocation();
  const { tv } = useSource();
  return metrics && metrics.at === measureKey(pathname, tv) ? metrics : null;
}

/** Columns the CSS `repeat(auto-fill, minmax(min(100%, X), 1fr))` grid makes at this width. */
export function columnsFor(m: GridMetrics, shape: "" | " portrait" | " mixed") {
  const min = shape === " portrait" ? m.minP : shape === " mixed" ? (m.min + m.minP) / 2 : m.min;
  return Math.max(1, Math.floor((m.width + m.gap) / (Math.min(min, m.width) + m.gap)));
}
