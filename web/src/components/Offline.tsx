import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useSource } from "../source";
import type { Episode } from "../types";
import "./Offline.css";

type Download = { id: string; source: string; video_id: string; episode: string; title: string; episode_title: string; height: number; phase: string; progress: number; error: string; url?: string; size?: number; retry_attempt?: number; retry_at?: number; created?: number; prepare_progress?: number; prepare_mode?: string };
type Cleanup = { enabled: boolean; days: number; last_run: number; deleted: number; errors: number };
const ACTIVE = ["downloading", "preparing", "cancelling", "deleting", "retrying", "queued"];
const active = (item: Download) => ACTIVE.includes(item.phase);
const unfinished = (item: Download) => ["error", "cancelled", "delete_error"].includes(item.phase);
const MODES: Record<string, string> = { copy: "直接封裝", gpu: "顯示卡加速", cpu: "CPU 轉檔" };
const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(bytes >= 100 * 1024 ** 3 ? 0 : 2)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
async function request(url: string, body?: object, method = "GET") {
  const response = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "下載操作失敗，請重試");
  return result;
}
export function notifyOfflinePlayback(source: string, video_id: string, episode: string) {
  return request("/api/offline/playback", { source, video_id, episode }, "POST");
}
function resume(item: Download, restart = false) {
  return request("/api/offline", { source: item.source, video_id: item.video_id, episode: item.episode, height: item.height, restart, title: item.title, episode_title: item.episode_title }, "POST");
}
/** The value that moves for this phase: downloaded share, or the MP4 being written. */
const moving = (item: Download) => item.phase === "preparing" ? item.prepare_progress || 0 : item.progress;
/** Download states; `watching` (the panel is open) polls while something moves, otherwise it only checks now and then. */
function useDownloads(source?: string, id?: string, watching = true) {
  const [items, setItems] = useState<Download[]>([]);
  const [cleanup, setCleanup] = useState<Cleanup | null>(null);
  const [freeBytes, setFreeBytes] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  // Recent (time, value) readings per download, for the remaining-time estimate.
  const samples = useRef(new Map<string, { phase: string; points: [number, number][] }>());
  useEffect(() => {
    let alive = true, busy = false, timer = 0;
    const refresh = async () => {
      window.clearTimeout(timer);
      // Hidden tabs stop polling; showing the page again refreshes at once.
      if (!alive || busy || document.hidden) return;
      busy = true;
      let next = 15000;
      try {
        const result = await request(`/api/offline?${new URLSearchParams({ ...(source ? { source } : {}), ...(id ? { video_id: id } : {}) })}`);
        if (alive) {
          const now = Date.now() / 1000;
          for (const item of result.items as Download[]) {
            const value = moving(item);
            const kept = samples.current.get(item.id);
            const points = kept && kept.phase === item.phase && kept.points.length && value >= kept.points[kept.points.length - 1][1] ? kept.points : [];
            samples.current.set(item.id, { phase: item.phase, points: [...points, [now, value] as [number, number]].filter(([t]) => now - t <= 180) });
          }
          setItems(result.items); setCleanup(result.cleanup || null); setFreeBytes(typeof result.free_bytes === "number" ? result.free_bytes : null); setLoaded(true); setError("");
          // Poll quickly only while something is moving; a closed panel just keeps its summary roughly right.
          const working = (result.items as Download[]).some(active);
          next = watching ? (working ? 3000 : 15000) : working ? 60000 : 0;
        }
      } catch (err) { if (alive) setError(err instanceof Error ? err.message : "無法取得下載狀態"); next = watching ? 5000 : 0; }
      finally { busy = false; }
      if (alive && next && !document.hidden) timer = window.setTimeout(() => void refresh(), next);
    };
    const visible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener("visibilitychange", visible);
    void refresh();
    return () => { alive = false; window.clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [source, id, revision, watching]);
  function eta(item: Download) {
    const points = samples.current.get(item.id)?.points || [];
    if (points.length < 2) return "";
    const [t0, v0] = points[0], [t1, v1] = points[points.length - 1];
    if (t1 - t0 < 10 || v1 <= v0) return "";
    const seconds = (100 - v1) / ((v1 - v0) / (t1 - t0));
    if (seconds < 60) return "不到 1 分鐘";
    const minutes = Math.round(seconds / 60);
    return minutes < 60 ? `約 ${minutes} 分鐘` : `約 ${Math.floor(minutes / 60)} 小時 ${minutes % 60} 分`;
  }
  return { items, cleanup, setCleanup, freeBytes, loaded, error, eta, refresh: () => setRevision(n => n + 1) };
}
function wait(retryAt?: number) {
  const seconds = Math.max(0, Math.ceil((retryAt || 0) - Date.now() / 1000));
  return seconds < 90 ? `約 ${seconds} 秒` : `約 ${Math.round(seconds / 60)} 分鐘`;
}
function label(item: Download, eta = "") {
  const left = eta ? ` · 剩餘${eta}` : "";
  if (item.phase === "deleting") return "正在停止下載並刪除，完成後會自動移除";
  if (item.phase === "delete_error") return `刪除未完成：${item.error}`;
  if (item.phase === "cancelling") return `正在取消下載 · 已保留 ${item.progress}% · 等待目前連線結束`;
  if (item.phase === "retrying") return `${item.error || "連線中斷"} · 已保留 ${item.progress}% · ${wait(item.retry_at)}後自動重試${(item.retry_attempt || 0) > 1 ? `（第 ${item.retry_attempt} 次）` : ""}`;
  if (item.phase === "complete") return `已下載${item.size ? ` · ${size(item.size)}` : ""} · 優先本地播放`;
  if (item.phase === "queued") return "排隊中";
  if (item.phase === "preparing") return `整理成 MP4${item.prepare_mode && MODES[item.prepare_mode] ? `（${MODES[item.prepare_mode]}）` : ""} ${item.prepare_progress || 0}%${left}`;
  if (item.phase === "downloading") return `下載中 ${item.progress}%${left}`;
  return item.error || "下載中斷";
}
function DownloadRow({ item, refresh, eta }: { item: Download; refresh: () => void; eta: string }) {
  const { prefix, sources } = useSource();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function act(restart = false) {
    setBusy(true); setError("");
    try {
      if (item.phase === "delete_error") {
        const result = await request("/api/offline/delete", { ids: [item.id] }, "POST");
        if (result.errors.length) throw new Error(result.errors[0].error);
      }
      else if (active(item)) await request(`/api/offline/${item.id}/cancel`, {}, "POST");
      else await resume(item, restart);
      refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "操作失敗"); }
    finally { setBusy(false); }
  }
  const bar = ["downloading", "preparing", "retrying", "cancelling"].includes(item.phase);
  return <div className="dl-row">
    <div className="dl-name">{item.title} · {item.episode_title} <span className="muted">{sources.find(s => s.id === item.source)?.label} · {item.height ? "720p" : "來源畫質"}</span></div>
    {bar ? <div className={`dl-bar${item.phase === "preparing" ? " is-preparing" : item.phase === "downloading" ? "" : " is-paused"}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={moving(item)}>
      <span style={{ width: `${Math.min(100, moving(item))}%` }} />
    </div> : null}
    <div role="status" className={`dl-state${item.phase === "error" || item.phase === "delete_error" ? " is-error" : ""}`}>{label(item, eta)}</div>
    <div className="dl-actions">
      {item.phase === "complete" ? <Link className="btn alt" to={`${prefix}/watch/${encodeURIComponent(item.source)}/${encodeURIComponent(item.video_id)}?ep=${encodeURIComponent(item.episode)}`}>播放本地影片</Link> : <button className="btn alt" disabled={busy || item.phase === "cancelling" || item.phase === "deleting"} onClick={() => void act()}>{item.phase === "deleting" ? "刪除中…" : item.phase === "delete_error" ? "重試刪除" : item.phase === "cancelling" || busy && active(item) ? "取消中…" : active(item) ? "取消下載" : "繼續下載"}</button>}
      {!active(item) && item.phase !== "complete" && item.phase !== "delete_error" ? <button className="btn alt" disabled={busy} onClick={() => void act(true)}>重新下載</button> : null}
    </div>
    {error ? <p role="alert">{error}</p> : null}
  </div>;
}

export function OfflinePanel({ source, id, title, episodes, current, local, onReady }: {
  source: string; id: string; title: string; episodes: Episode[]; current: string; local: boolean;
  onReady: (items: { episode: string; url: string }[]) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { items, error, eta, refresh } = useDownloads(source, id, expanded);
  const dialog = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [height, setHeight] = useState(720);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { dialog.current?.close(); setSelected(new Set()); setActionError(""); setMessage(""); }, [source, id]);
  useEffect(() => { onReady(items.filter(i => i.phase === "complete" && i.url).map(i => ({ episode: i.episode, url: i.url! }))); }, [items, onReady]);
  const choices = episodes.length ? episodes : [{ id: current, title: "目前影片" }];
  const unavailable = (episode: string) => items.some(item => item.episode === episode && (active(item) || item.phase === "complete" || item.phase === "delete_error"));
  const available = choices.filter(ep => !unavailable(ep.id));
  const chosen = available.filter(ep => selected.has(ep.id));
  const done = items.filter(item => item.phase === "complete").length;
  function open() {
    setSelected(new Set(available.some(ep => ep.id === current) ? [current] : []));
    setActionError(""); setMessage("");
    dialog.current?.showModal();
  }
  async function download() {
    if (!chosen.length || busy) return;
    setBusy(true); setActionError(""); setMessage("");
    const pending = new Set(chosen.map(ep => ep.id));
    let added = 0;
    try {
      for (const ep of chosen) {
        setMessage(`正在加入下載佇列：${added + 1} / ${chosen.length}`);
        await request("/api/offline", { source, video_id: id, episode: ep.id, height, title, episode_title: episodes.length ? ep.title : "" }, "POST");
        pending.delete(ep.id); added++;
      }
      dialog.current?.close();
    } catch (err) { setActionError(err instanceof Error ? err.message : "無法開始下載"); }
    finally {
      setSelected(pending);
      setMessage(`已加入 ${added} 集。${pending.size ? `尚有 ${pending.size} 集未加入，保留勾選供你重試。` : "依序下載，完成後優先本地播放。"}`);
      refresh(); setBusy(false);
    }
  }
  return <details style={{ padding: "8px 16px" }} open={expanded} onToggle={e => setExpanded(e.currentTarget.open)} onKeyDown={e => e.stopPropagation()}>
    <summary>離線下載{local ? " · 目前使用本地影片" : ""}{done ? ` · 已下載 ${done} 集` : ""}{items.some(active) ? " · 下載進行中" : ""}</summary>
    <p className="muted">點擊才下載整集；完成後保留在 D:\AI工作區\離線影片。720p 適合平板與 Nest Hub，會用顯示卡加速整理，來源本來就是 720p 時直接封裝。中斷後保留已下載部分並自動續傳；播放時下載會自動讓路，不會被取消。</p>
    <button className="btn" disabled={busy} onClick={open}>選擇集數下載</button>
    <dialog ref={dialog} className="offline-download-dialog" aria-label="選擇離線下載集數" onKeyDown={e => e.stopPropagation()} onCancel={e => { if (busy) e.preventDefault(); }}>
      <div className="offline-download-header"><h2>選擇離線下載集數</h2><button className="btn alt" disabled={busy} onClick={() => dialog.current?.close()}>關閉</button></div>
      <p className="muted">勾選想下載的集數，可一次加入多集；不會切換目前播放的影片。</p>
      <div className="offline-download-actions">
        <button className="btn alt" disabled={busy || !available.length} onClick={() => setSelected(new Set(available.map(ep => ep.id)))}>全選可下載集數</button>
        <button className="btn alt" disabled={busy || !selected.size} onClick={() => setSelected(new Set())}>清除勾選</button>
        <span>已勾選 {chosen.length} 集</span>
      </div>
      <div className="offline-download-episodes">
        {choices.map(ep => {
          const existing = items.find(item => item.episode === ep.id);
          const disabled = unavailable(ep.id);
          return <label key={ep.id} className="offline-download-episode">
            <input type="checkbox" aria-label={`下載 ${ep.title}`} disabled={busy || disabled} checked={!disabled && selected.has(ep.id)} onChange={e => {
              const checked = e.target.checked;
              setSelected(prev => { const next = new Set(prev); if (checked) next.add(ep.id); else next.delete(ep.id); return next; });
            }} />
            <span>{ep.title}{ep.id === current ? " · 目前播放" : ""}</span>
            {existing ? <small className="muted">{label(existing, eta(existing))}</small> : null}
          </label>;
        })}
      </div>
      <div className="offline-download-actions offline-download-footer">
        <select className="field" aria-label="下載畫質" disabled={busy} value={height} onChange={e => setHeight(Number(e.target.value))}><option value={720}>720p</option><option value={0}>來源畫質（不轉換）</option></select>
        <button className="btn" disabled={busy || !chosen.length} onClick={() => void download()}>{busy ? "正在加入…" : `下載所選 ${chosen.length} 集`}</button>
      </div>
      {message ? <p role="status">{message}</p> : null}
      {actionError ? <p role="alert">{actionError}</p> : null}
    </dialog>
    {message ? <p role="status">{message}</p> : null}
    {actionError || error ? <p role="alert">{actionError || error}</p> : null}
    {items.map(item => <DownloadRow key={item.id} item={item} refresh={refresh} eta={eta(item)} />)}
  </details>;
}

type Deletion = { deleted: string[]; pending?: string[]; errors: { id: string; error: string }[] };

export function OfflineLibrary() {
  const { items, cleanup, setCleanup, freeBytes, loaded, error, eta, refresh } = useDownloads();
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchor, setAnchor] = useState("");
  const [confirm, setConfirm] = useState<"" | "selected" | "unfinished">("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function toggleCleanup(enabled: boolean) {
    if (!cleanup || cleanupBusy) return;
    const previous = cleanup;
    setCleanup({ ...cleanup, enabled });
    setCleanupBusy(true); setMessage("");
    try {
      setCleanup(await request("/api/offline/cleanup", { enabled }, "POST") as Cleanup);
      setMessage(enabled ? "已開啟自動清理；超過 14 天未觀看的完整影片會在背景清除。" : "已關閉自動清理，兩站均會保留離線影片。");
      refresh();
    } catch (err) { setCleanup(previous); setMessage(err instanceof Error ? err.message : "無法更新清理設定"); }
    finally { setCleanupBusy(false); }
  }
  const needle = query.trim().toLocaleLowerCase();
  const visible = items.filter(item => `${item.title} ${item.episode_title}`.toLocaleLowerCase().includes(needle));
  const order = (item: Download) => ACTIVE.indexOf(item.phase);
  // Running first, then the queue in the order it will be downloaded.
  const running = visible.filter(active).sort((a, b) => order(a) - order(b) || (a.created || 0) - (b.created || 0));
  const done = visible.filter(item => item.phase === "complete");
  const failed = visible.filter(unfinished);
  const groups = new Map<string, Download[]>();
  for (const item of done) {
    const key = `${item.source}:${item.video_id}`;
    groups.set(key, [...(groups.get(key) || []), item]);
  }
  const ordered = [...groups.values()].flat();
  const chosen = items.filter(item => selected.has(item.id) && item.phase === "complete");
  const allDone = items.filter(item => item.phase === "complete");
  const allRunning = items.filter(active).length, allFailed = items.filter(unfinished).length;
  function select(ids: string[], checked: boolean) {
    setConfirm("");
    setSelected(prev => { const next = new Set(prev); for (const id of ids) { if (checked) next.add(id); else next.delete(id); } return next; });
  }
  function pick(item: Download, checked: boolean, shift: boolean) {
    const a = ordered.findIndex(i => i.id === anchor), b = ordered.findIndex(i => i.id === item.id);
    select(shift && a >= 0 ? ordered.slice(Math.min(a, b), Math.max(a, b) + 1).map(i => i.id) : [item.id], checked);
    setAnchor(item.id);
  }
  async function remove(targets: Download[], keepSelection: boolean) {
    if (!targets.length || busy) return;
    setBusy(true); setMessage("");
    const ids = targets.map(item => item.id), pending = new Set(ids);
    const errors: { id: string; error: string }[] = [];
    let deleted = 0, deleting = 0, failure = "";
    try {
      for (let offset = 0; offset < ids.length; offset += 200) {
        setMessage(`正在刪除：${offset} / ${ids.length} 集`);
        const result = await request("/api/offline/delete", { ids: ids.slice(offset, offset + 200) }, "POST") as Deletion;
        for (const id of result.deleted) { pending.delete(id); deleted++; }
        for (const id of result.pending || []) { pending.delete(id); deleting++; }
        errors.push(...result.errors);
      }
    } catch (err) { failure = err instanceof Error ? err.message : "刪除失敗"; }
    finally {
      if (keepSelection) setSelected(pending);
      setConfirm("");
      const details = errors.map(item => `${items.find(i => i.id === item.id)?.episode_title || "影片"}：${item.error}`).join("；");
      setMessage(`已刪除 ${deleted} 集。${deleting ? `${deleting} 集正在停止下載並刪除，完成後會自動移除。` : ""}${pending.size ? `尚有 ${pending.size} 集${keepSelection ? "保留勾選，" : "未刪除，"}可重試。` : ""}${[failure, details].filter(Boolean).join("；")}`);
      refresh(); setBusy(false);
    }
  }
  async function resumeAll() {
    const targets = failed.filter(item => item.phase !== "delete_error");
    if (!targets.length || busy) return;
    setBusy(true); setMessage("");
    let added = 0;
    const errors: string[] = [];
    for (const item of targets) {
      setMessage(`正在重新加入下載佇列：${added + errors.length + 1} / ${targets.length}`);
      try { await resume(item); added++; }
      catch (err) { errors.push(`${item.episode_title}：${err instanceof Error ? err.message : "無法加入"}`); }
    }
    setMessage(`已重新加入 ${added} 集，會依序下載。${errors.join("；")}`);
    refresh(); setBusy(false);
  }
  return <div className="dl-page"><h1>離線影片</h1>
    {loaded ? <p className="dl-summary">
      已下載 {allDone.length} 集 · {size(allDone.reduce((sum, item) => sum + (item.size || 0), 0))}
      {allRunning ? ` · 下載中 ${allRunning} 集` : ""}{allFailed ? ` · 未完成 ${allFailed} 集` : ""}
      {freeBytes !== null ? ` · 硬碟剩餘 ${size(freeBytes)}` : ""}
    </p> : <p className="dl-summary muted">讀取離線影片…</p>}
    <p className="muted">儲存在 D:\AI工作區\離線影片。網頁與投放優先播放完整的本地檔案；電腦及背景服務需要保持開啟。自動預載是有期限的暫存，此處管理手動下載的整集影片。</p>
    <details className="dl-cleanup">
      <summary>自動清理：{cleanup ? cleanup.enabled ? "已開啟（超過 14 天未觀看會清除）" : "已關閉" : "讀取中…"}</summary>
      <label className="offline-selection"><input type="checkbox" checked={cleanup?.enabled ?? false} disabled={!cleanup || cleanupBusy} onChange={e => void toggleCleanup(e.target.checked)} /> 自動清除超過 14 天未觀看的影片</label>
      <p className="muted">兩站共用此設定。從最後觀看時間計算；從未觀看則從下載完成日計算。下載中與未完成的檔案不會清除。舊影片沒有觀看紀錄時，從功能啟用時保留 14 天。背景每小時檢查一次，清除後需重新下載。</p>
      {cleanup?.last_run ? <p className="muted">上次檢查：{new Date(cleanup.last_run * 1000).toLocaleString()} · 已清除 {cleanup.deleted} 集{cleanup.errors ? ` · ${cleanup.errors} 項未完成清理，檔案仍保留` : ""}</p> : null}
    </details>
    <div className="dl-toolbar">
      <input className="field" aria-label="搜尋離線影片" disabled={busy} placeholder="搜尋劇名、集數" value={query} onChange={e => { setQuery(e.target.value); setConfirm(""); }} />
      {done.length ? <label className="offline-selection"><input type="checkbox" aria-label="全選已下載的搜尋結果" disabled={busy} checked={done.every(item => selected.has(item.id))} onChange={e => select(done.map(item => item.id), e.target.checked)} /> 全選已下載</label> : null}
      <span>{chosen.length} 集已選 · {size(chosen.reduce((sum, item) => sum + (item.size || 0), 0))}</span>
      <button className="btn alt" disabled={!chosen.length || busy} onClick={() => setConfirm("selected")}>刪除選取（{chosen.length}）</button>
      {chosen.length ? <button className="btn alt" disabled={busy} onClick={() => { setSelected(new Set()); setConfirm(""); }}>取消選取</button> : null}
    </div>
    {confirm === "selected" ? <div role="alertdialog" aria-label="確認刪除離線影片" className="dl-confirm">
      <p>永久刪除所選 {chosen.length} 集？正在播放這些本地影片時可能中斷；若檔案被其他程式占用，會顯示原因並保留重試操作。</p>
      <p>{[...new Set(chosen.map(item => item.title))].join("、")}</p>
      <button className="btn" disabled={busy} onClick={() => void remove(chosen, true)}>確認刪除 {chosen.length} 集</button>{" "}<button className="btn alt" disabled={busy} onClick={() => setConfirm("")}>保留影片</button>
    </div> : null}
    {message ? <p role="status">{message}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {loaded && !items.length && !error ? <p>尚未下載影片。請到播放頁展開「離線下載」，點「選擇集數下載」，勾選想下載的集數。</p> : null}
    {items.length && !visible.length ? <p>沒有符合的離線影片。</p> : null}
    {running.length ? <section className="dl-section" aria-label="下載中">
      <h2>下載中（{running.length}）</h2>
      {running.map(item => <DownloadRow key={item.id} item={item} refresh={refresh} eta={eta(item)} />)}
    </section> : null}
    {groups.size ? <section className="dl-section" aria-label="已下載">
      <h2>已下載（{done.length}）</h2>
      {[...groups].map(([key, group]) => <div key={key} className="dl-group">
        <h3><label className="offline-selection"><input type="checkbox" aria-label={`全選劇集：${group[0].title}`} disabled={busy} checked={group.every(item => selected.has(item.id))} onChange={e => select(group.map(item => item.id), e.target.checked)} /> {group[0].title}（{group.length} 集 · {size(group.reduce((sum, item) => sum + (item.size || 0), 0))}）</label></h3>
        {group.map(item => <div key={item.id} className="dl-pick">
          <label className="offline-selection offline-item-selection" onClick={e => {
            if (e.target !== e.currentTarget) return;
            e.preventDefault();
            if (!busy) { pick(item, !selected.has(item.id), e.shiftKey); e.currentTarget.querySelector("input")?.focus(); }
          }}>
            <input type="checkbox" aria-label={`選取 ${item.episode_title}`} disabled={busy} checked={selected.has(item.id)} onChange={e => pick(item, e.target.checked, (e.nativeEvent as MouseEvent).shiftKey)} />
          </label>
          <DownloadRow item={item} refresh={refresh} eta="" />
        </div>)}
      </div>)}
    </section> : null}
    {failed.length ? <section className="dl-section" aria-label="未完成">
      <h2>未完成（{failed.length}）</h2>
      <div className="dl-actions">
        <button className="btn alt" disabled={busy || !failed.some(item => item.phase !== "delete_error")} onClick={() => void resumeAll()}>全部繼續下載</button>
        <button className="btn alt" disabled={busy} onClick={() => setConfirm("unfinished")}>清除這些項目</button>
      </div>
      {confirm === "unfinished" ? <div role="alertdialog" aria-label="確認清除未完成項目" className="dl-confirm">
        <p>清除這 {failed.length} 個未完成的下載？已下載的部分會一併刪除，之後需要從頭下載。</p>
        <button className="btn" disabled={busy} onClick={() => void remove(failed, false)}>確認清除 {failed.length} 項</button>{" "}<button className="btn alt" disabled={busy} onClick={() => setConfirm("")}>保留</button>
      </div> : null}
      {failed.map(item => <DownloadRow key={item.id} item={item} refresh={refresh} eta="" />)}
    </section> : null}
  </div>;
}
