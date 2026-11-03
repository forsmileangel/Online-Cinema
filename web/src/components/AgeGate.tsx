import { APP, storageKey } from "../appConfig";

const KEY = storageKey("ageOk");

export function ageOk(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function AgeGate({ onOk }: { onOk: () => void }) {
  return (
    <div className="gate">
      <div className="gate-card">
        <div className="brand-mark" style={{ marginBottom: 16 }} />
        <h1>{APP.name}（18+）</h1>
        <p className="muted">
          這是裝在你電腦上的本機介面，只連 <code>127.0.0.1</code>。內容來自公開成人網站，僅限成年後使用。
        </p>
        <div className="actions">
          <button
            className="btn"
            type="button"
            onClick={() => {
              localStorage.setItem(KEY, "1");
              onOk();
            }}
          >
            我已滿 18 歲
          </button>
        </div>
      </div>
    </div>
  );
}
