import { APP } from "./appConfig";

const OLD = "missav.";
const NEW = `${APP.storagePrefix}.`;

/** Move settings saved before the rename (missav.*) to the new keys so nothing resets. */
export function migrateLegacyStorage() {
  try {
    const old: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(OLD)) old.push(key);
    }
    for (const key of old) {
      const next = NEW + key.slice(OLD.length);
      if (localStorage.getItem(next) === null) {
        const value = localStorage.getItem(key);
        if (value !== null) localStorage.setItem(next, value);
      }
      localStorage.removeItem(key);
    }
  } catch {
    // Storage can be blocked; the app works without it.
  }
}
