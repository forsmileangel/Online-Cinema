// Per-viewer search conveniences. Storage can be unavailable, so every access is guarded.
export type Scope = "source" | "group" | "all";

import { storageKey } from "./appConfig";

const SCOPE_KEY = storageKey("searchScope");
const RECENT_KEY = storageKey("recentSearches");
const RECENT_MAX = 10;

export function parseScope(raw: string | null | undefined): Scope {
  return raw === "group" || raw === "all" ? raw : "source";
}

export function loadScope(): Scope {
  try {
    return parseScope(localStorage.getItem(SCOPE_KEY));
  } catch {
    return "source";
  }
}

export function saveScope(scope: Scope) {
  try {
    localStorage.setItem(SCOPE_KEY, scope);
  } catch {
    /* ignore */
  }
}

export function loadRecent(): string[] {
  try {
    const list: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(list)
      ? list.filter((q): q is string => typeof q === "string" && q.trim() !== "").slice(0, RECENT_MAX)
      : [];
  } catch {
    return [];
  }
}

export function rememberSearch(query: string): string[] {
  const q = query.trim();
  const next = [q, ...loadRecent().filter((item) => item !== q)].slice(0, RECENT_MAX);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
  return next;
}

export function clearRecent() {
  try {
    localStorage.removeItem(RECENT_KEY);
  } catch {
    /* ignore */
  }
}

export function searchPath(prefix: string, q: string, src: string, scope: Scope = "source", page = 1) {
  const params = new URLSearchParams({ q, src });
  if (scope !== "source") params.set("scope", scope);
  if (page > 1) params.set("page", String(page));
  return `${prefix}/search?${params}`;
}
