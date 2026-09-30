import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useSource } from "../source";
import type { Episode } from "../types";
import "./Offline.css";

type Download = { id: string; source: string; video_id: string; episode: string; title: string; episode_title: string; height: number; phase: string; progress: number; error: string; url?: string; size?: number; retry_attempt?: number; retry_at?: number };
type Cleanup = { enabled: boolean; days: number; last_run: number; deleted: number; errors: number };
const active = (item: Download) => ["queued", "downloading", "retrying", "preparing", "cancelling", "deleting"].includes(item.phase);
async function request(url: string, body?: object, method = "GET") {
  const response = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "下載操作失敗，請重試");
  return result;
}
export function notifyOfflinePlayback(source: string, video_id: string, episode: string) {
  return request("/api/offline/playback", { source, video_id, episode }, "POST");
}
function useDownloads(source?: string, id?: string) {
  const [items, setItems] = useState<Download[]>([]);
  const [cleanup, setCleanup] = useState<Cleanup | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true, busy = false;
    const refresh = async () => {
      if (busy) return;
      busy = true;
      try {
        const result = await request(`/api/offline?${new URLSearchParams({ ...(source ? { source } : {}), ...(id ? { video_id: id } : {}) })}`);
        if (alive) { setItems(result.items); setCleanup(result.cleanup || null); setError(""); }
      } catch (err) { if (alive) setError(err instanceof Error ? err.message : "無法取得下載狀態"); }
      finally { busy = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [source, id, revision]);
  return { items, cleanup, setCleanup, error, refresh: () => setRevision(n => n + 1) };
}
function label(item: Download) {
  if (item.phase === "deleting") return "正在停止下載並刪除，完成後會自動移除";
  if (item.phase === "delete_error") return `刪除未完成：${item.error}`;
  if (item.phase === "cancelling") return `正在取消下載 · 已保留 ${item.progress}% · 等待目前連線結束`;
  if (item.phase === "retrying") return `自動續傳中 · 已保留 ${item.progress}% · 第 ${item.retry_attempt || 1} 次重試 · 約 ${Math.max(0, Math.ceil((item.retry_at || 0) - Date.now() / 1000))} 秒後重連`;
  return item.phase === "complete" ? "已下載 · 優先本地播放" : item.phase === "queued" ? "等待下載" : item.phase === "preparing" ? "正在整理 MP4，尚未完成" : item.phase === "downloading" ? `下載中 ${item.progress}%` : item.error || "下載中斷";
}
function DownloadRow({ item, refresh }: { item: Download; refresh: () => void }) {
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
      else await request("/api/offline", { source: item.source, video_id: item.video_id, episode: item.episode, height: item.height, restart }, "POST");
      refresh();
    } catch (err) { setError(err instanceof Error ? err.message : "操作失敗"); }
    finally { setBusy(false); }
  }
  return <div style={{ borderTop: "1px solid #333", padding: "10px 0", display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 8 }}>
    <div>{item.title} · {item.episode_title} <span className="muted">{sources.find(s => s.id === item.source)?.label} · {item.height ? "720p" : "來源畫質"}</span></div>
    <div role="status">{label(item)}</div>
    {item.phase === "complete" ? <Link className="btn alt" to={`${prefix}/watch/${encodeURIComponent(item.source)}/${encodeURIComponent(item.video_id)}?ep=${encodeURIComponent(item.episode)}`}>播放本地影片</Link> : <button className="btn alt" disabled={busy || item.phase === "cancelling" || item.phase === "deleting"} onClick={() => void act()}>{item.phase === "deleting" ? "刪除中…" : item.phase === "delete_error" ? "重試刪除" : item.phase === "cancelling" || busy && active(item) ? "取消中…" : active(item) ? "取消下載" : "繼續下載"}</button>}
    {!active(item) && item.phase !== "complete" && item.phase !== "delete_error" ? <button className="btn alt" disabled={busy} onClick={() => void act(true)}>重新下載</button> : null}
    {error ? <p role="alert">{error}</p> : null}
  </div>;
}

export function OfflinePanel({ source, id, episodes, current, local, onReady }: {
  source: string; id: string; episodes: Episode[]; current: string; local: boolean;
  onReady: (items: { episode: string; url: string }[]) => void;
}) {
  const { items, error, refresh } = useDownloads(source, id);
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
        await request("/api/offline", { source, video_id: id, episode: ep.id, height }, "POST");
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
  return <details style={{ padding: "8px 16px" }} onKeyDown={e => e.stopPropagation()}>
    <summary>離線下載{local ? " · 目前使用本地影片" : ""}{items.some(active) ? " · 下載進行中" : ""}</summary>
    <p className="muted">點擊才下載整集；完成後保留在 D:\AI工作區\離線影片。下載 720p 較適合平板與 Nest Hub；中斷後保留已下載部分，可按「繼續下載」。整理期間會使用電腦運算資源。開始播放同一集時會取消整集下載並保留進度；已驗證的相同 HLS 分段可重用，未下載部分仍需連線。</p>
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
            {existing ? <small className="muted">{label(existing)}</small> : null}
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
    {items.map(item => <DownloadRow key={item.id} item={item} refresh={refresh} />)}
  </details>;
}

export function OfflineLibrary() {
  const { items, cleanup, setCleanup, error, refresh } = useDownloads();
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchor, setAnchor] = useState("");
  const [confirm, setConfirm] = useState(false);
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
  const visible = items.filter(item => `${item.title} ${item.episode_title}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const groups = new Map<string, Download[]>();
  for (const item of visible) {
    const key = `${item.source}:${item.video_id}`;
    groups.set(key, [...(groups.get(key) || []), item]);
  }
  const ordered = [...groups.values()].flat();
  const chosen = items.filter(item => selected.has(item.id));
  const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  function select(ids: string[], checked: boolean) {
    setConfirm(false);
    setSelected(prev => { const next = new Set(prev); for (const id of ids) { if (checked) next.add(id); else next.delete(id); } return next; });
  }
  function pick(item: Download, checked: boolean, shift: boolean) {
    const a = ordered.findIndex(i => i.id === anchor), b = ordered.findIndex(i => i.id === item.id);
    select(shift && a >= 0 ? ordered.slice(Math.min(a, b), Math.max(a, b) + 1).map(i => i.id) : [item.id], checked);
    setAnchor(item.id);
  }
  async function remove() {
    if (!chosen.length || busy) return;
    setBusy(true); setMessage("");
    const ids = chosen.map(item => item.id), pending = new Set(ids);
    const errors: { id: string; error: string }[] = [];
    let deleted = 0, deleting = 0, failure = "";
    try {
      for (let offset = 0; offset < ids.length; offset += 200) {
        setMessage(`正在刪除：${offset} / ${ids.length} 集`);
        const result = await request("/api/offline/delete", { ids: ids.slice(offset, offset + 200) }, "POST") as { deleted: string[]; pending?: string[]; errors: { id: string; error: string }[] };
        for (const id of result.deleted) { pending.delete(id); deleted++; }
        for (const id of result.pending || []) { pending.delete(id); deleting++; }
        errors.push(...result.errors);
      }
    } catch (err) { failure = err instanceof Error ? err.message : "刪除失敗"; }
    finally {
      setSelected(pending); setConfirm(false);
      const details = errors.map(item => `${items.find(i => i.id === item.id)?.episode_title || "影片"}：${item.error}`).join("；");
      setMessage(`已刪除 ${deleted} 集。${deleting ? `${deleting} 集正在停止下載並刪除，完成後會自動移除。` : ""}${pending.size ? `尚有 ${pending.size} 集保留勾選，可重試。` : ""}${[failure, details].filter(Boolean).join("；")}`);
      refresh(); setBusy(false);
    }
  }
  return <div><h1>離線影片</h1><p className="muted">儲存在 D:\AI工作區\離線影片。網頁與投放優先播放完整的本地檔案；電腦及背景服務需要保持開啟。</p>
    <p className="muted">自動預載是有期限的暫存；此處管理手動下載的整集影片。支援依劇名全選，以及按住 Shift 連續勾選。</p>
    <section aria-label="離線影片自動清理" style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12, marginBottom: 16 }}>
      <label className="offline-selection"><input type="checkbox" checked={cleanup?.enabled ?? false} disabled={!cleanup || cleanupBusy} onChange={e => void toggleCleanup(e.target.checked)} /> 自動清除超過 14 天未觀看的影片</label>
      <p className="muted">兩站共用此設定。從最後觀看時間計算；從未觀看則從下載完成日計算。下載中與未完成的檔案不會清除。</p>
      <p className="muted">舊影片沒有觀看紀錄時，從功能啟用時保留 14 天。背景每小時檢查一次，清除後需重新下載。</p>
      {cleanup?.last_run ? <p className="muted">上次檢查：{new Date(cleanup.last_run * 1000).toLocaleString()} · 已清除 {cleanup.deleted} 集{cleanup.errors ? ` · ${cleanup.errors} 項未完成清理，檔案仍保留` : ""}</p> : null}
    </section>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", position: "sticky", top: 0, background: "#17171d", padding: 12, zIndex: 2 }}>
      <input className="field" aria-label="搜尋離線影片" disabled={busy} placeholder="搜尋劇名、集數" value={query} onChange={e => { setQuery(e.target.value); setConfirm(false); }} />
      <label className="offline-selection"><input type="checkbox" aria-label="全選搜尋結果" disabled={busy} checked={visible.length > 0 && visible.every(item => selected.has(item.id))} onChange={e => select(visible.map(item => item.id), e.target.checked)} /> 全選搜尋結果</label>
      <span>{chosen.length} 集已選 · {size(chosen.reduce((sum, item) => sum + (item.size || 0), 0))}</span>
      <button className="btn alt" disabled={!chosen.length || busy} onClick={() => setConfirm(true)}>刪除選取（{chosen.length}）</button>
      {chosen.length ? <button className="btn alt" disabled={busy} onClick={() => { setSelected(new Set()); setConfirm(false); }}>取消選取</button> : null}
    </div>
    {confirm ? <div role="alertdialog" aria-label="確認刪除離線影片" style={{ border: "1px solid #ec604f", padding: 16, margin: "12px 0" }}>
      <p>永久刪除所選 {chosen.length} 集？正在播放這些本地影片時可能中斷；仍在下載的項目會先自動取消，再刪除檔案；若檔案被其他程式占用，會顯示原因並保留重試操作。</p>
      <p>{[...new Set(chosen.map(item => item.title))].join("、")}</p>
      <button className="btn" disabled={busy} onClick={() => void remove()}>確認刪除 {chosen.length} 集</button>{" "}<button className="btn alt" disabled={busy} onClick={() => setConfirm(false)}>保留影片</button>
    </div> : null}
    {message ? <p role="status">{message}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {!items.length && !error ? <p>尚未下載影片。請到播放頁展開「離線下載」，點「選擇集數下載」，勾選想下載的集數。</p> : null}
    {items.length && !visible.length ? <p>沒有符合的離線影片。</p> : null}
    {[...groups].map(([key, group]) => <section key={key} style={{ marginTop: 24 }}>
      <h2><label className="offline-selection"><input type="checkbox" aria-label={`全選劇集：${group[0].title}`} disabled={busy} checked={group.every(item => selected.has(item.id))} onChange={e => select(group.map(item => item.id), e.target.checked)} /> {group[0].title}（{group.length} 集）</label></h2>
      {group.map(item => <div key={item.id} style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <label className="offline-selection offline-item-selection" onClick={e => {
          if (e.target !== e.currentTarget) return;
          e.preventDefault();
          if (!busy) { pick(item, !selected.has(item.id), e.shiftKey); e.currentTarget.querySelector("input")?.focus(); }
        }}>
          <input type="checkbox" aria-label={`選取 ${item.episode_title}`} disabled={busy} checked={selected.has(item.id)} onChange={e => pick(item, e.target.checked, (e.nativeEvent as MouseEvent).shiftKey)} />
        </label>
        <div style={{ flex: 1 }}><DownloadRow item={item} refresh={refresh} /></div>
      </div>)}
    </section>)}
  </div>;
}
