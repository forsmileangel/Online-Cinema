import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";

/**
 * What the TV remote's arrows can land on; the title under a poster is part of the card, not a second
 * stop. The player counts as one block (Enter plays, left/right seek); its own buttons are for the pointer.
 */
const SELECTOR = "[data-tv], .nav a, .chip, .more, select.field, button.btn, summary, .player";
const EDITABLE = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const BACK = new Set(["Backspace", "Escape", "GoBack", "BrowserBack"]);

type Dir = "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight";

function editing(el: Element | null) {
  return !!el && (EDITABLE.has(el.tagName) || (el as HTMLElement).isContentEditable);
}

/** Can take focus right now: shown, enabled, and not inside a closed <details> (its contents keep a box but refuse focus). */
function focusable(el: HTMLElement) {
  if ((el as HTMLButtonElement).disabled || el.getAttribute("aria-hidden") === "true") return false;
  const details = el.closest("details");
  if (details && !details.open && !el.closest("summary")) return false;
  const check = (el as HTMLElement & { checkVisibility?: (o: object) => boolean }).checkVisibility;
  return check ? check.call(el, { contentVisibilityAuto: true, visibilityProperty: true }) : el.offsetParent !== null;
}

function focusables(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>(SELECTOR)].filter(
    (el) => focusable(el) && (el.classList.contains("player") || !el.closest(".player")),
  );
}

/** The element a key press starts from: the focused control, or the player on a watch page. */
function origin(el: Element | null, watching: boolean): Element | null {
  if (el && el !== document.body && el !== document.documentElement) return el;
  return watching ? document.querySelector(".player") : null;
}

/**
 * The nearest control in that direction, by position on the page. A candidate must start past the
 * middle of the current element; being lined up with it counts for more than being close.
 */
export function nearest(dir: Dir, from: DOMRect, nodes: { el: HTMLElement; rect: DOMRect }[]): HTMLElement | null {
  const fx = (from.left + from.right) / 2;
  const fy = (from.top + from.bottom) / 2;
  let best: HTMLElement | null = null;
  let bestScore = Infinity;
  for (const { el, rect } of nodes) {
    if (rect.width <= 0 || rect.height <= 0) continue;
    let forward: number;
    let gap: number;
    let offset: number;
    if (dir === "ArrowDown" || dir === "ArrowUp") {
      if (dir === "ArrowDown" ? rect.top < fy : rect.bottom > fy) continue;
      forward = dir === "ArrowDown" ? rect.top - from.bottom : from.top - rect.bottom;
      gap = Math.max(0, rect.left - from.right, from.left - rect.right);
      offset = Math.abs((rect.left + rect.right) / 2 - fx);
    } else {
      if (dir === "ArrowRight" ? rect.left < fx : rect.right > fx) continue;
      forward = dir === "ArrowRight" ? rect.left - from.right : from.left - rect.right;
      gap = Math.max(0, rect.top - from.bottom, from.top - rect.bottom);
      offset = Math.abs((rect.top + rect.bottom) / 2 - fy);
    }
    const score = Math.max(0, forward) + gap * 3 + offset * 0.2;
    if (score < bestScore) {
      bestScore = score;
      best = el;
    }
  }
  return best;
}

function move(dir: Dir, watching: boolean) {
  const all = focusables();
  if (!all.length) return;
  const cur = document.activeElement as HTMLElement | null;
  const from = origin(cur, watching);
  if (!from) {
    // Nothing focused yet: start at the first control on screen.
    const first = all.find((el) => el.getBoundingClientRect().bottom > 0) || all[0];
    first.focus();
    return;
  }
  const rect = from.getBoundingClientRect();
  if (dir === "ArrowUp" && from.classList.contains("player")) {
    // On a wide screen the episode rail sits beside the player, where left/right would mean seeking:
    // up from the player goes to the current episode instead.
    const chip = document.querySelector<HTMLElement>(".ep-rail .chip.on") || document.querySelector<HTMLElement>(".ep-rail .chip");
    if (chip && focusable(chip) && chip.getBoundingClientRect().left >= rect.right - 1) {
      chip.focus();
      return;
    }
  }
  const nodes = all.filter((el) => el !== from && !from.contains(el)).map((el) => ({ el, rect: el.getBoundingClientRect() }));
  let next = nearest(dir, rect, nodes);
  if (!next && (dir === "ArrowLeft" || dir === "ArrowRight")) {
    // End of a row: carry on in page order so a row never traps the cursor.
    const i = all.indexOf(from as HTMLElement);
    if (i >= 0) next = all[dir === "ArrowRight" ? Math.min(all.length - 1, i + 1) : Math.max(0, i - 1)];
  }
  next?.focus();
}

export function TvKeys() {
  const loc = useLocation();
  const nav = useNavigate();
  const watching = loc.pathname.includes("/watch/");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const k = e.key;
      const target = e.target as HTMLElement | null;
      // Typing in the search box, or a dialog with its own keys: not ours.
      if (editing(target) || target?.closest("dialog[open]")) return;
      if (BACK.has(k)) {
        const root = loc.pathname === "/tv" || loc.pathname === "/tv/";
        if (!root) {
          e.preventDefault();
          nav(-1);
        }
        return;
      }
      if (!k.startsWith("Arrow")) return;
      if (watching && (k === "ArrowLeft" || k === "ArrowRight")) {
        // Left/right inside the player (or with nothing focused) seek; the player handles them.
        const inPlayer = !target || target === document.body || !!target.closest(".player");
        if (inPlayer) return;
      }
      e.preventDefault();
      e.stopPropagation();
      move(k as Dir, watching);
    };
    // Capture: runs before the player's own key handler, which stays out of the way once focus moved.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [loc.pathname, watching, nav]);

  return null;
}
