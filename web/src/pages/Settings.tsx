import { useEffect, useState } from "react";
import { api } from "../api";
import { APP } from "../appConfig";
import { AppearanceSettings } from "../components/AppearanceSettings";
import { PhoneConnection } from "../components/PhoneConnection";
import type { Settings } from "../types";

type AccountState = { loggedIn: boolean; name: string };

type AccountSource = {
  title: string;
  /** What the site calls the login name, and how the field behaves. */
  nameLabel: string;
  nameType: "email" | "text";
  nameMax: number;
  passwordMax: number;
  note: string;
  load: () => Promise<AccountState>;
  login: (name: string, password: string) => Promise<AccountState>;
  logout: () => Promise<AccountState>;
};

const ininavState = (s: { logged_in: boolean; email: string }): AccountState => ({ loggedIn: s.logged_in, name: s.email });
const huangguoState = (s: { logged_in: boolean; username: string }): AccountState => ({ loggedIn: s.logged_in, name: s.username });

const ININAV_ACCOUNT: AccountSource = {
  title: "ininAV",
  nameLabel: "信箱",
  nameType: "email",
  nameMax: 254,
  passwordMax: 1024,
  note: "未登入也能瀏覽與播放。登入後只儲存登入憑證與信箱，不儲存密碼；帳號操作限這台電腦。",
  load: () => api.ininavStatus().then(ininavState),
  login: (name, password) => api.ininavLogin(name, password).then(ininavState),
  logout: () => api.ininavLogout().then(ininavState),
};

const HUANGGUO_ACCOUNT: AccountSource = {
  title: "黃果短劇",
  nameLabel: "帳號",
  nameType: "text",
  nameMax: 20,
  passwordMax: 20,
  note: "未登入也能瀏覽與播放。登入後只儲存登入憑證與帳號，不儲存密碼；登入過期要再登入一次，帳號操作限這台電腦。",
  load: () => api.huangguoStatus().then(huangguoState),
  login: (name, password) => api.huangguoLogin(name, password).then(huangguoState),
  logout: () => api.huangguoLogout().then(huangguoState),
};

/** A member account at a source: the session is kept on this PC's server, never the password. */
function SourceAccount({ source }: { source: AccountSource }) {
  const [account, setAccount] = useState<AccountState | null>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    source.load().then((next) => { setAccount(next); setName(next.name); })
      .catch((e: Error) => setMsg(e.message));
  }, [source]);

  async function update(logout = false) {
    if (busy) return;
    setBusy(true);
    setMsg("");
    const submittedPassword = password;
    setPassword("");
    try {
      const next = logout ? await source.logout() : await source.login(name, submittedPassword);
      setAccount(next);
      setName(next.name);
      setMsg(logout ? "已登出" : "登入成功");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "帳號操作失敗，請重試");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label={`${source.title} 帳號`}>
      <h2 className="h1" style={{ fontSize: 20, marginTop: 28 }}>{source.title} 帳號</h2>
      <p className="muted">{source.note}</p>
      <p role="status">{account ? (account.loggedIn ? `已登入：${account.name}` : "尚未登入") : "登入狀態尚未載入"}</p>
      {account?.loggedIn ? (
        <button className="btn alt" type="button" disabled={busy} onClick={() => void update(true)}>
          {busy ? "登出中…" : `登出 ${source.title}`}
        </button>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); void update(); }}>
          <p><label>{source.nameLabel} <input className="field" type={source.nameType} autoComplete="username" required maxLength={source.nameMax}
            value={name} onChange={(e) => setName(e.target.value)} disabled={busy || !account} /></label></p>
          <p><label>密碼 <input className="field" type="password" autoComplete="current-password" required maxLength={source.passwordMax}
            value={password} onChange={(e) => setPassword(e.target.value)} disabled={busy || !account} /></label></p>
          <button className="btn" type="submit" disabled={busy || !account}>{busy ? "登入中…" : `登入 ${source.title}`}</button>
        </form>
      )}
      {msg ? <p className="status" role="status">{msg}</p> : null}
    </section>
  );
}

/** The LAN addresses for a phone, worked out from the TV link (the server lists the IPs). */
function StaticPhoneAddresses({ s }: { s: Settings }) {
  const phoneUrl = s.tv_url ? new URL(s.tv_url) : null;
  if (phoneUrl) phoneUrl.pathname = "/";
  const addresses = phoneUrl ? (s.lan_ips || []).map((ip) => {
    const address = new URL(phoneUrl);
    address.hostname = ip;
    return address;
  }) : [];
  return (
    <>
      {s.lan_tv && addresses.length ? (
        <div aria-label="區網連線網址摘要" style={{ display: "flex", flexWrap: "wrap", gap: "8px 24px", marginBottom: 16 }}>
          {addresses.map((address, index) => (
            <p key={address.hostname} style={{ margin: 0 }}>
              <strong>{index === 0 ? "主要網址" : "備用網址"}</strong>：<a href={address.origin}>{address.origin}</a>
            </p>
          ))}
        </div>
      ) : null}
      <p className="muted">{s.lan_tv ? "已允許區網投放；服務重啟後生效。" : "目前僅限本機連線。"} 瀏覽器不會開啟原站廣告腳本。</p>
      <section aria-label="手機與平板連線">
        <h2 className="h1" style={{ fontSize: 20, marginTop: 28 }}>手機與平板連線</h2>
        <p className="muted">手機與這台電腦需連同一個 Wi-Fi，電腦上的服務也要保持執行。第一次請開啟下方「手機配對連結」，配對後即可使用一般網址。</p>
        {s.lan_tv && addresses.length ? addresses.map((address, index) => (
          <div key={address.hostname} style={{ marginBottom: 16 }}>
            <p><strong>{index === 0 ? "主要網址" : "備用網址"}</strong>：<a href={address.origin}>{address.origin}</a></p>
            <p className="muted">IP：<code>{address.hostname}</code>　連接埠：<code>{address.port || "80"}</code></p>
            <p style={{ overflowWrap: "anywhere" }}>手機配對連結：<a href={address.href}>{address.href}</a></p>
          </div>
        )) : <p className="muted">{s.lan_tv ? "目前沒有可用的區網 IP，請確認電腦已連上 Wi-Fi。" : "請先開啟下方「允許投放（區網）」並重啟服務，這裡就會顯示手機連線網址。"}</p>}
        <p className="muted">主要網址無法連線時，可試備用網址；兩者連到同一個服務。IP 可能在重新連線後改變，請以這裡顯示的最新網址為準。</p>
      </section>
    </>
  );
}

export function SettingsPage() {
  const [s, setS] = useState<Settings | null>(null);
  const [msg, setMsg] = useState("");
  const [savingLocation, setSavingLocation] = useState(false);

  useEffect(() => {
    api.settings().then(setS).catch((e: Error) => setMsg(e.message));
  }, []);

  const failed = (what: string) => (e: Error) => setMsg(`${what}失敗：${e.message}`);

  async function saveMirror(v: string) {
    try {
      const next = await api.saveSettings({ forced_mirror: v });
      setS(next);
      setMsg("已更新鏡像");
    } catch (e) { failed("更新鏡像")(e as Error); }
  }

  async function saveLocale(v: string) {
    try {
      const next = await api.saveSettings({ locale: v });
      setS(next);
      setMsg("已更新片源語言，請回首頁重新載入");
    } catch (e) { failed("更新片源語言")(e as Error); }
  }

  function clearHistory() {
    if (!window.confirm("確定要清除所有觀看紀錄嗎？清除後無法復原。")) return;
    void api.clearHistory().then(() => setMsg("觀看紀錄已清除")).catch(failed("清除觀看紀錄"));
  }

  if (!s) return <div className="empty">{msg || "載入設定…"}</div>;

  return (
    <div>
      <h1 className="h1">設定</h1>
      {APP.features.phoneConnection ? (
        <>
          <p className="muted">{s.lan_tv ? "已允許區網連線。" : "目前僅限本機連線。"} 瀏覽器不會開啟原站廣告腳本。</p>
          <PhoneConnection enabled={s.lan_tv} code={s.tv_code} />
        </>
      ) : <StaticPhoneAddresses s={s} />}

      {APP.features.ininav ? <SourceAccount source={ININAV_ACCOUNT} /> : null}
      {APP.features.huangguoAccount ? <SourceAccount source={HUANGGUO_ACCOUNT} /> : null}

      <AppearanceSettings onMessage={setMsg} />

      <h2 className="h1" style={{ fontSize: 20, marginTop: 36 }}>投放到電視</h2>
      <p className="muted">
        {APP.cast.intro}變更區網允許設定後請重啟 Windows 工作排程器中的「{APP.task}」工作；使用手動啟動時再重開 start.bat，並在防火牆放行 {APP.port}。選片在電腦上點，電視播放選定內容；劇集可由伺服器接續播放，遙控器支援依電視而異。
      </p>
      <p>
        <label>
          <input
            type="checkbox"
            checked={!!s.lan_tv}
            onChange={(e) =>
              void api.saveSettings({ lan_tv: e.target.checked }).then((n) => {
                setS(n);
                setMsg(e.target.checked ? `已開啟投放。請重啟「${APP.task}」工作；手動啟動時重開 start.bat。` : "已改回僅本機，重啟服務後生效。");
              }).catch(failed("更新投放設定"))
            }
          />{" "}
          允許投放（區網）
        </label>
      </p>
      {APP.features.kaohsiung ? (
        <p>
          <label>
            <input type="checkbox" checked={!!s.in_kaohsiung} disabled={savingLocation} onChange={(e) => {
              const enabled = e.target.checked;
              setSavingLocation(true);
              void api.saveSettings({ in_kaohsiung: enabled }).then((next) => {
                setS(next);
                setMsg(enabled ? "已顯示高雄 Philips 電視，無需重啟服務。" : "已隱藏高雄電視；既有投放不會中斷。");
              }).catch((e: Error) => setMsg(e.message)).finally(() => setSavingLocation(false));
            }} />{" "}
            我在高雄
          </label>
          <span className="muted"> · 勾選後才顯示高雄 Philips 電視，其他電視照常顯示。</span>
        </p>
      ) : null}
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
              }).catch(failed("更換配對碼"))
            }
          >
            更換配對碼
          </button>
        </div>
      ) : null}

      {APP.features.mirrors ? (
        <>
          <h2 className="h1" style={{ fontSize: 20, marginTop: 36 }}>片源</h2>
          <p>
            片源語言（MissAV）：{" "}
            <select
              className="field"
              aria-label="片源語言"
              value={s.locale || "zh"}
              onChange={(e) => void saveLocale(e.target.value)}
            >
              <option value="zh">繁體中文</option>
              <option value="ja">日本語</option>
              <option value="cn">簡體中文</option>
            </select>
          </p>
          <p className="muted">標題、簡介、女優名跟網站語系走。介面按鈕維持繁體。預設是繁體中文。</p>
          <p>
            目前鏡像：<strong>{s.mirror || "未連上"}</strong>
          </p>
          <p>
            強制指定：{" "}
            <select
              className="field"
              aria-label="強制指定鏡像"
              value={s.forced_mirror || ""}
              onChange={(e) => void saveMirror(e.target.value)}
            >
              <option value="">自動挑選</option>
              {(s.mirrors || []).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </p>
          <div className="actions">
            <button
              className="btn alt"
              type="button"
              onClick={() => void api.refreshMirrors().then((r) => setMsg(`已改用 ${r.mirror}`)).catch(failed("探測鏡像"))}
            >
              重新探測鏡像
            </button>
          </div>
        </>
      ) : null}

      <h2 className="h1" style={{ fontSize: 20, marginTop: 36 }}>觀看紀錄</h2>
      <div className="actions">
        <button
          className="btn alt"
          type="button"
          onClick={clearHistory}
        >
          清除觀看紀錄
        </button>
      </div>
      {msg ? <p className="status" role="status">{msg}</p> : null}
    </div>
  );
}
