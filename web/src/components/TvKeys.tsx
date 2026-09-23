import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";

function focusables(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>("[data-tv], a.card, .nav a, .chip, .more, select.field, button.btn")].filter(
    (el) => el.offsetParent !== null,
  );
}

function move(dir: string) {
  const nodes = focusables();
  if (!nodes.length) return;
  const cur = document.activeElement as HTMLElement | null;
  let i = cur ? nodes.indexOf(cur) : -1;
  if (dir === "ArrowRight" || dir === "ArrowDown") i = Math.min(nodes.length - 1, i + 1);
  else i = Math.max(0, i - 1);
  nodes[i]?.focus();
}

export function TvKeys() {
  const loc = useLocation();
  const nav = useNavigate();
  const watching = loc.pathname.includes("/watch/");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key;
      if (k === "Backspace" || k === "Escape" || k === "GoBack" || k === "BrowserBack") {
        const root = loc.pathname === "/tv" || loc.pathname === "/tv/";
        if (!root) {
          e.preventDefault();
          nav(-1);
        }
        return;
      }
      if (k === "MediaPlayPause" || k === "MediaPlay" || k === "MediaPause") return;
      if (watching) return;
      if (k.startsWith("Arrow")) {
        e.preventDefault();
        move(k);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [loc.pathname, watching, nav]);

  return null;
}
