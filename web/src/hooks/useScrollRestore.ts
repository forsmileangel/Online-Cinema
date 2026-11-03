import { useEffect, useLayoutEffect } from "react";
import { useLocation, useNavigationType } from "react-router-dom";
import { useSource } from "../source";

// Per history entry: the scroll position, and the card that was opened from it.
const positions = new Map<string, number>();
const opened = new Map<string, string>();

if (typeof window !== "undefined" && "scrollRestoration" in window.history) window.history.scrollRestoration = "manual";

export function rememberOpened(key: string, href: string) {
  opened.set(key, href);
  // Taken now: scrolling while the next page lays out must not count for this one.
  positions.set(key, window.scrollY);
}

/** The router's key for the current history entry, without subscribing a component to location changes. */
export function currentLocationKey(): string {
  return (window.history.state as { key?: string } | null)?.key ?? "default";
}

/** Whether the browser still shows this history entry (the router keeps its key in history.state). */
function isCurrent(key: string) {
  return (window.history.state?.key ?? "default") === key;
}

/** Saves this page's scroll position and puts it back when the page is reached again with Back/Forward. */
export function useScrollRestore(ready: boolean) {
  const { key } = useLocation();
  const navType = useNavigationType();
  const { tv } = useSource();

  useEffect(() => {
    let frame = 0;
    const save = () => {
      cancelAnimationFrame(frame);
      // The listener outlives the route change by a moment; by then the scroll belongs to the next page.
      frame = requestAnimationFrame(() => { if (isCurrent(key)) positions.set(key, window.scrollY); });
    };
    window.addEventListener("scroll", save, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", save);
    };
  }, [key]);

  useLayoutEffect(() => {
    if (!ready || navType !== "POP") return;
    const y = positions.get(key);
    if (y) window.scrollTo(0, y);
    const href = opened.get(key);
    if (tv && href) {
      // Otherwise the remote's next arrow press starts again from the top of the page.
      const card = [...document.querySelectorAll<HTMLAnchorElement>("a.poster-link")].find((a) => a.getAttribute("href") === href);
      card?.focus({ preventScroll: true });
    }
  }, [ready, key, navType, tv]);
}
