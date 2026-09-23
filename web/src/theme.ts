export type ThemeTokens = {
  bg: string;
  bg2: string;
  card: string;
  text: string;
  muted: string;
  accent: string;
  accent2: string;
  line: string;
};

export type ThemePreset = {
  id: string;
  name: string;
  light?: boolean;
  tokens: ThemeTokens;
};

export const TOKEN_LABELS: { key: keyof ThemeTokens; label: string }[] = [
  { key: "bg", label: "背景" },
  { key: "bg2", label: "次背景" },
  { key: "card", label: "卡片" },
  { key: "text", label: "主文字" },
  { key: "muted", label: "次要文字" },
  { key: "accent", label: "強調色" },
  { key: "accent2", label: "強調色 2" },
  { key: "line", label: "邊線" },
];

export const PRESETS: ThemePreset[] = [
  {
    id: "cinema",
    name: "影院紅",
    tokens: {
      bg: "#0b0b10",
      bg2: "#14141c",
      card: "#1b1b26",
      text: "#f4f1ea",
      muted: "#9a958c",
      accent: "#e85d4c",
      accent2: "#ffb070",
      line: "#ffffff14",
    },
  },
  {
    id: "oled",
    name: "OLED 黑",
    tokens: {
      bg: "#000000",
      bg2: "#0a0a0a",
      card: "#141414",
      text: "#f2f2f2",
      muted: "#8a8a8a",
      accent: "#e11d48",
      accent2: "#fb7185",
      line: "#ffffff18",
    },
  },
  {
    id: "amber",
    name: "暖琥珀",
    tokens: {
      bg: "#140f0a",
      bg2: "#1c1610",
      card: "#2a2118",
      text: "#f6ead7",
      muted: "#b09a7a",
      accent: "#f59e0b",
      accent2: "#fbbf24",
      line: "#f59e0b33",
    },
  },
  {
    id: "steel",
    name: "冷鋼藍",
    tokens: {
      bg: "#0c1118",
      bg2: "#141b26",
      card: "#1c2533",
      text: "#e8eef6",
      muted: "#8b9bb0",
      accent: "#60a5fa",
      accent2: "#93c5fd",
      line: "#60a5fa33",
    },
  },
  {
    id: "jade",
    name: "墨玉綠",
    tokens: {
      bg: "#0a120e",
      bg2: "#121c16",
      card: "#1a2820",
      text: "#e6f2ea",
      muted: "#87a090",
      accent: "#34d399",
      accent2: "#6ee7b7",
      line: "#34d39933",
    },
  },
  {
    id: "wine",
    name: "葡萄酒",
    tokens: {
      bg: "#14080c",
      bg2: "#1c1014",
      card: "#2a181e",
      text: "#f6e8ec",
      muted: "#b08a94",
      accent: "#be123c",
      accent2: "#fb7185",
      line: "#be123c44",
    },
  },
  {
    id: "graphite",
    name: "石墨灰",
    tokens: {
      bg: "#121212",
      bg2: "#1a1a1a",
      card: "#242424",
      text: "#f5f5f5",
      muted: "#a3a3a3",
      accent: "#d4d4d4",
      accent2: "#e5e5e5",
      line: "#ffffff22",
    },
  },
  {
    id: "dusk",
    name: "暮粉",
    tokens: {
      bg: "#160d12",
      bg2: "#20141a",
      card: "#2c1c24",
      text: "#fde8f0",
      muted: "#c4a0ae",
      accent: "#f472b6",
      accent2: "#f9a8d4",
      line: "#f472b633",
    },
  },
  {
    id: "washi",
    name: "和紙淺色",
    light: true,
    tokens: {
      bg: "#f4efe6",
      bg2: "#ebe4d6",
      card: "#fffaf2",
      text: "#2a241c",
      muted: "#6b5e4e",
      accent: "#b45309",
      accent2: "#c2410c",
      line: "#2a241c22",
    },
  },
  {
    id: "snow",
    name: "雪地淺色",
    light: true,
    tokens: {
      bg: "#f8fafc",
      bg2: "#eef2f6",
      card: "#ffffff",
      text: "#0f172a",
      muted: "#64748b",
      accent: "#2563eb",
      accent2: "#1d4ed8",
      line: "#0f172a18",
    },
  },
  {
    id: "violet",
    name: "紫夜",
    tokens: {
      bg: "#12081c",
      bg2: "#1a1028",
      card: "#26183a",
      text: "#f3e8ff",
      muted: "#b4a0c8",
      accent: "#c084fc",
      accent2: "#e9d5ff",
      line: "#c084fc33",
    },
  },
  {
    id: "celadon",
    name: "青瓷",
    tokens: {
      bg: "#0c1412",
      bg2: "#141e1c",
      card: "#1c2a28",
      text: "#e6f4f0",
      muted: "#8aaea6",
      accent: "#2dd4bf",
      accent2: "#5eead4",
      line: "#2dd4bf33",
    },
  },
  {
    id: "sunset",
    name: "日落",
    tokens: {
      bg: "#1a0e08",
      bg2: "#241610",
      card: "#332018",
      text: "#ffedd5",
      muted: "#c4a484",
      accent: "#fb923c",
      accent2: "#fdba74",
      line: "#fb923c33",
    },
  },
  {
    id: "navy",
    name: "海軍",
    tokens: {
      bg: "#0a1224",
      bg2: "#121a30",
      card: "#1a243c",
      text: "#e0eaff",
      muted: "#8aa0c0",
      accent: "#38bdf8",
      accent2: "#7dd3fc",
      line: "#38bdf833",
    },
  },
  {
    id: "coffee",
    name: "咖啡",
    tokens: {
      bg: "#1c140e",
      bg2: "#261c16",
      card: "#332820",
      text: "#f5e6d3",
      muted: "#b8a090",
      accent: "#d6a07a",
      accent2: "#e8c4a8",
      line: "#d6a07a33",
    },
  },
  {
    id: "cyan",
    name: "青螢",
    tokens: {
      bg: "#061416",
      bg2: "#0c1c20",
      card: "#122830",
      text: "#e0f8ff",
      muted: "#7aa8b4",
      accent: "#22d3ee",
      accent2: "#67e8f9",
      line: "#22d3ee33",
    },
  },
  {
    id: "rosegold",
    name: "玫瑰金",
    tokens: {
      bg: "#1a1214",
      bg2: "#24181c",
      card: "#302024",
      text: "#f8e8ea",
      muted: "#c4a8ac",
      accent: "#e8b4b8",
      accent2: "#f5d0d4",
      line: "#e8b4b833",
    },
  },
  {
    id: "lava",
    name: "熔岩",
    tokens: {
      bg: "#140808",
      bg2: "#1e1010",
      card: "#2c1616",
      text: "#ffe4e6",
      muted: "#c09090",
      accent: "#f43f5e",
      accent2: "#fb7185",
      line: "#f43f5e44",
    },
  },
  {
    id: "mist",
    name: "霧紫",
    tokens: {
      bg: "#141018",
      bg2: "#1c1824",
      card: "#282430",
      text: "#eee8f8",
      muted: "#a89ab8",
      accent: "#a78bfa",
      accent2: "#c4b5fd",
      line: "#a78bfa33",
    },
  },
  {
    id: "forest",
    name: "森林",
    tokens: {
      bg: "#0c140c",
      bg2: "#141c14",
      card: "#1c281c",
      text: "#e8f5e8",
      muted: "#90b090",
      accent: "#4ade80",
      accent2: "#86efac",
      line: "#4ade8033",
    },
  },
  {
    id: "newsprint",
    name: "新聞紙",
    light: true,
    tokens: {
      bg: "#ece7dc",
      bg2: "#e2dccf",
      card: "#f7f3ea",
      text: "#1a1a1a",
      muted: "#5c574e",
      accent: "#111111",
      accent2: "#44403c",
      line: "#1a1a1a22",
    },
  },
  {
    id: "teal",
    name: "午夜青",
    tokens: {
      bg: "#041014",
      bg2: "#0a1a1e",
      card: "#102428",
      text: "#d5f5f0",
      muted: "#7aa8a0",
      accent: "#14b8a6",
      accent2: "#5eead4",
      line: "#14b8a633",
    },
  },
];

const STORAGE = "cinema.theme";

const CSS_MAP: Record<keyof ThemeTokens, string> = {
  bg: "--bg",
  bg2: "--bg-2",
  card: "--card",
  text: "--text",
  muted: "--muted",
  accent: "--accent",
  accent2: "--accent-2",
  line: "--line",
};

export function presetById(id: string): ThemePreset {
  return PRESETS.find((p) => p.id === id) || PRESETS[0];
}

export function applyTokens(tokens: ThemeTokens, light = false) {
  const root = document.documentElement;
  (Object.keys(CSS_MAP) as (keyof ThemeTokens)[]).forEach((k) => {
    root.style.setProperty(CSS_MAP[k], tokens[k]);
  });
  root.style.setProperty("color-scheme", light ? "light" : "dark");
  root.setAttribute("data-theme", light ? "light" : "dark");
}

export type SavedTheme = { presetId: string; tokens: ThemeTokens };

export function loadSavedTheme(): SavedTheme {
  try {
    const raw = localStorage.getItem(STORAGE);
    if (raw) {
      const parsed = JSON.parse(raw) as SavedTheme;
      if (parsed?.tokens?.bg && parsed?.presetId) return parsed;
    }
  } catch {
    /* ignore */
  }
  const p = PRESETS[0];
  return { presetId: p.id, tokens: { ...p.tokens } };
}

export function saveTheme(saved: SavedTheme) {
  try {
    localStorage.setItem(STORAGE, JSON.stringify(saved));
  } catch {
    /* ignore */
  }
  const preset = presetById(saved.presetId);
  applyTokens(saved.tokens, !!preset.light);
}

export function bootTheme() {
  const saved = loadSavedTheme();
  const preset = presetById(saved.presetId);
  applyTokens(saved.tokens, !!preset.light);
}
