// Card size for the home page and lists (Settings → 外觀). Kept per device, like the volume;
// styles.css turns it into the --card-* sizes. TV mode always uses its own big cards.
export type CardSize = "s" | "m" | "l";

import { storageKey } from "./appConfig";

const STORAGE = storageKey("cardSize");

export const CARD_SIZES: { id: CardSize; label: string }[] = [
  { id: "s", label: "小" },
  { id: "m", label: "中" },
  { id: "l", label: "大" },
];

export function loadCardSize(): CardSize {
  try {
    const saved = localStorage.getItem(STORAGE);
    if (saved === "s" || saved === "m" || saved === "l") return saved;
  } catch {
    /* Storage may be disabled. */
  }
  return "m";
}

function applyCardSize(size: CardSize) {
  document.documentElement.dataset.cardSize = size;
}

export function saveCardSize(size: CardSize) {
  try {
    localStorage.setItem(STORAGE, size);
  } catch {
    /* ignore */
  }
  applyCardSize(size);
}

export function bootCardSize() {
  applyCardSize(loadCardSize());
}
