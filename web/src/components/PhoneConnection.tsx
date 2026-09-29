import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, type ConnectionInfo } from "../api";

export function PhoneConnection({ enabled, code }: { enabled?: boolean; code?: string }) {
  const [info, setInfo] = useState<ConnectionInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    async function refresh() {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      setBusy(true);
      setCopied("");
      try {
        const next = await api.connection(controller.signal);
        if (!controller.signal.aborted) { setInfo(next); setError(""); }
      } catch (e) {
        if (!controller.signal.aborted) {
          setInfo(null);
          setError(e instanceof Error ? e.message : "無法偵測連線");
        }
      } finally {
        pending = false;
        if (!controller.signal.aborted) setBusy(false);
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30000);
    window.addEventListener("focus", refresh);
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("online", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [enabled, code, revision]);

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied("已複製手機連結");
    } catch {
      setCopied("請選取完整連結後手動複製。");
    }
  }

  return <section className="phone-connection" aria-labelledby="phone-connection-title">
    <div className="row-head">
      <h2 id="phone-connection-title">手機連線</h2>
      <button className="btn alt" type="button" disabled={busy} onClick={() => setRevision((n) => n + 1)}>
        {busy ? "偵測中…" : "重新偵測"}
      </button>
    </div>
    <p className="muted">手機與電腦連上同一個 Wi-Fi，使用下方連結。網址會隨目前網路自動更新。</p>
    {error ? <p role="status">目前無法確認連線：{error}。請確認電腦上的服務仍在執行後重新偵測。</p> : !info ? <p role="status">正在偵測目前網路…</p> : <>
      {!info.enabled ? <p role="status">目前僅限本機。請在下方開啟「允許投放（區網）」並重啟服務，才能用手機連線。</p>
        : !info.addresses.length ? <p role="status">尚未偵測到區網 IP。請讓電腦連上 Wi-Fi 或有線網路。</p>
        : <>
          <p>連接埠（Port）：<strong>{info.port}</strong></p>
          {!info.addresses.some((a) => a.listening) ? <p role="status">已允許區網，但服務尚未在目前區網位址回應。若剛開啟此設定，請重啟「Online Cinema Server」或 start.bat。</p> : null}
          {info.addresses.length > 1 ? <p className="muted">偵測到多個區網位址，請使用與手機同一個網路的位址。</p> : null}
          {info.addresses.map((address) => <div className="phone-address" key={address.ip}>
            <div className="phone-address-head">
              {address.listening ? <a href={address.connect_url} target="_blank" rel="noreferrer">{address.url}</a> : <code>{address.url}</code>}
              <span className="muted">{address.listening ? "本機服務有回應" : "此位址尚未回應"}</span>
            </div>
            {address.listening ? <>
              <label className="muted" htmlFor={`phone-${address.ip}`}>首次連線用完整配對連結：</label>
              <div className="phone-link-row">
                <input className="field" id={`phone-${address.ip}`} readOnly value={address.connect_url} onFocus={(e) => e.currentTarget.select()} />
                <button className="btn alt" type="button" onClick={() => void copy(address.connect_url)}>複製連結</button>
              </div>
            </> : null}
          </div>)}
          <p className="muted">電腦需保持開機、服務持續執行；手機不要輸入 127.0.0.1。若無法連線，請確認使用相同 Wi-Fi，且防火牆允許連接埠 {info.port}。</p>
        </>}
    </>}
    <p className="muted">頁面開啟時每 30 秒更新，回到此頁或恢復連線時也會重新偵測。</p>
    {copied ? <p role="status">{copied}</p> : null}
  </section>;
}

export function PhonePair() {
  const { search } = useLocation();
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const code = new URLSearchParams(search).get("c");
    setError("");
    if (!code) { setError("連結缺少配對碼，請使用電腦設定頁上的完整手機連結。"); return; }
    void api.tvPair(code).then(() => {
      // Reload after pairing so source settings and the home page use the new cookie.
      if (!cancelled) window.location.replace("/");
    }).catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [search, retry]);
  return <div>
    <h1 className="h1">手機連線</h1>
    {error ? <>
      <p role="alert">{error} 請確認配對碼未更換，或從電腦取得最新連結。</p>
      <button className="btn alt" type="button" onClick={() => setRetry((n) => n + 1)}>重試連線</button>
    </> : <p role="status">正在配對，完成後將開啟首頁…</p>}
  </div>;
}
