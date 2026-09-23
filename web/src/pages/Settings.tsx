import { useEffect, useState } from "react";
import { api } from "../api";
import type { Settings } from "../types";
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

export function SettingsPage() {
  const [s, setS] = useState<Settings | null>(null);
  const [msg, setMsg] = useState("");
  const [theme, setTheme] = useState<SavedTheme>(() => loadSavedTheme());

  useEffect(() => {
    api.settings().then(setS).catch((e: Error) => setMsg(e.message));
  }, []);

  function apply(next: SavedTheme) {
    setTheme(next);
    saveTheme(next);
    void api.saveSettings({ theme: JSON.stringify(next) }).catch(() => {});
  }

  function pickPreset(id: string) {
    const p = presetById(id);
    apply({ presetId: p.id, tokens: { ...p.tokens } });
    setMsg(`已套用「${p.name}」`);
  }

  function tune(key: keyof ThemeTokens, value: string) {
    apply({ presetId: theme.presetId, tokens: { ...theme.tokens, [key]: value } });
  }

  if (!s) return <div className="empty">{msg || "載入設定…"}</div>;
  const preset = presetById(theme.presetId);

  return (
    <div>
      <h1 className="h1">設定</h1>
      <p className="muted">{s.lan_tv ? "已允許區網投放；服務重啟後生效。" : "目前僅限本機連線。"} 瀏覽器不會開啟原站廣告腳本。</p>

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

      <h2 className="h1" style={{ fontSize: 20, marginTop: 36 }}>投放到電視</h2>
      <p className="muted">
        播放頁會有「投放這支影片」。支援 Chromecast／Google TV 與 LG／DLNA 電視，與電腦同一 Wi-Fi。變更後請重啟 Windows 工作排程器中的「Online Cinema Server」工作；使用手動啟動時再重開 start.bat，並在防火牆放行 6970。選片在電腦上點，電視播放選定內容；劇集可由伺服器接續播放，遙控器支援依電視而異。
      </p>
      <p>
        <label>
          <input
            type="checkbox"
            checked={!!s.lan_tv}
            onChange={(e) =>
              void api.saveSettings({ lan_tv: e.target.checked }).then((n) => {
                setS(n);
                setMsg(e.target.checked ? "已開啟投放。請重啟「Online Cinema Server」工作；手動啟動時重開 start.bat。" : "已改回僅本機，重啟服務後生效。");
              })
            }
          />{" "}
          允許投放（區網）
        </label>
      </p>
      {s.lan_tv ? (
        <div>
          <p>
            電視請開：<code>{s.tv_url || "（沒有區網 IP）"}</code>
          </p>
          <p>
            配對碼：<strong style={{ letterSpacing: "0.2em", fontSize: 22 }}>{s.tv_code}</strong>
          </p>
          <button
            className="btn alt"
            type="button"
            onClick={() =>
              void api.tvRotate().then((r) => {
                setS({ ...s, tv_code: r.tv_code });
                setMsg("已換新配對碼，請用新網址開電視");
              })
            }
          >
            更換配對碼
          </button>
        </div>
      ) : null}

      <h2 className="h1" style={{ fontSize: 20, marginTop: 36 }}>觀看紀錄</h2>
      <div className="actions">
        <button
          className="btn alt"
          type="button"
          onClick={() => void api.clearHistory().then(() => setMsg("觀看紀錄已清除"))}
        >
          清除觀看紀錄
        </button>
      </div>
      {msg ? <p className="status">{msg}</p> : null}
    </div>
  );
}
