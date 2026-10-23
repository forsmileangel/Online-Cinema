import { useState, type FormEvent } from "react";
import { api } from "../api";

/** Shown on a phone, tablet or TV that has not been paired: type the 6-character code from the PC's settings page. */
export function PairPrompt() {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    const clean = code.replace(/\s+/g, "").toUpperCase();
    if (clean.length !== 6 || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.tvPair(clean);
      window.location.reload(); // Pages and settings load again with the pairing cookie.
    } catch (err) {
      setError(err instanceof Error ? err.message : "配對失敗，請重試");
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <main className="page pair-prompt">
        <h1 className="h1">連線到電腦</h1>
        <p>這台裝置還沒配對。請在電腦上打開本程式的「設定」頁，找到「配對碼」，在下面輸入 6 個字。</p>
        <form onSubmit={(e) => void submit(e)}>
          <input
            className="field pair-input"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase().slice(0, 8))}
            aria-label="配對碼"
            placeholder="配對碼"
            autoFocus
            autoCapitalize="characters"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            maxLength={8}
            enterKeyHint="go"
          />{" "}
          <button className="btn" type="submit" disabled={busy || code.replace(/\s+/g, "").length !== 6}>
            {busy ? "配對中…" : "配對"}
          </button>
        </form>
        {error ? <p role="alert">{error}</p> : null}
        <p className="muted">配對成功後會記住這台裝置 30 天。在電腦上換過配對碼的話，請輸入新的。</p>
      </main>
    </div>
  );
}
