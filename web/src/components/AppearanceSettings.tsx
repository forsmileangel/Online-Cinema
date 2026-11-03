import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { CARD_SIZES, loadCardSize, saveCardSize, type CardSize } from "../cardSize";
import {
  PRESETS,
  TOKEN_LABELS,
  loadSavedTheme,
  presetById,
  saveTheme,
  type SavedTheme,
  type ThemeTokens,
} from "../theme";

function hexOf(v: string) {
  if (v.startsWith("#") && (v.length === 7 || v.length === 9)) return v.slice(0, 7);
  return "#888888";
}

/** Theme presets, colour tuning and card size: per device, with the theme also kept on the server. */
export function AppearanceSettings({ onMessage }: { onMessage: (text: string) => void }) {
  const [theme, setTheme] = useState<SavedTheme>(() => loadSavedTheme());
  const [cardSize, setCardSize] = useState<CardSize>(loadCardSize);

  // Dragging a colour fires many changes a second; the server gets one write after the hand stops.
  const themeTimer = useRef(0);
  const themePending = useRef<SavedTheme | null>(null);
  const sendTheme = () => {
    window.clearTimeout(themeTimer.current);
    const next = themePending.current;
    themePending.current = null;
    if (next) void api.saveSettings({ theme: JSON.stringify(next) }).catch(() => {});
  };
  useEffect(() => sendTheme, []);
  function apply(next: SavedTheme) {
    setTheme(next);
    saveTheme(next);
    themePending.current = next;
    window.clearTimeout(themeTimer.current);
    themeTimer.current = window.setTimeout(sendTheme, 300);
  }

  function pickPreset(id: string) {
    const p = presetById(id);
    apply({ presetId: p.id, tokens: { ...p.tokens } });
    onMessage(`已套用「${p.name}」`);
  }

  function tune(key: keyof ThemeTokens, value: string) {
    apply({ presetId: theme.presetId, tokens: { ...theme.tokens, [key]: value } });
  }

  const preset = presetById(theme.presetId);
  return (
    <>
      <h2 className="h1" style={{ fontSize: 20, marginTop: 28 }}>外觀</h2>
      <p className="muted">先選一套模板，再用下面的色盤微調。</p>
      <div className="theme-grid">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            className={`theme-swatch ${theme.presetId === p.id ? "on" : ""}`}
            onClick={() => pickPreset(p.id)}
          >
            <div className="bars">
              <span style={{ background: p.tokens.bg }} />
              <span style={{ background: p.tokens.accent }} />
              <span style={{ background: p.tokens.text }} />
            </div>
            <div className="nm">{p.name}</div>
          </button>
        ))}
      </div>
      <p className="muted">微調「{preset.name}」</p>
      <div className="tune-grid">
        {TOKEN_LABELS.map((row) => (
          <label key={row.key} className="tune-row">
            <span>{row.label}</span>
            <input
              type="color"
              value={hexOf(theme.tokens[row.key])}
              onChange={(e) => tune(row.key, e.target.value)}
            />
          </label>
        ))}
      </div>
      <button
        className="btn alt"
        type="button"
        onClick={() => pickPreset(theme.presetId)}
      >
        恢復此模板
      </button>
      <p className="muted" style={{ marginTop: 20 }}>卡片大小：首頁與列表的縮圖。只影響這台裝置，電視模式固定用大卡。</p>
      <div className="chips" role="radiogroup" aria-label="卡片大小">
        {CARD_SIZES.map((option) => (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={cardSize === option.id}
            className={`chip${cardSize === option.id ? " on" : ""}`}
            onClick={() => { setCardSize(option.id); saveCardSize(option.id); }}
          >
            {option.label}
          </button>
        ))}
      </div>
    </>
  );
}
