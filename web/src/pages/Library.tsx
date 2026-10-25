import { useEffect, useRef, useState } from "react";
import { NavLink, useSearchParams } from "react-router-dom";
import { PosterGrid, SkeletonGrid } from "../components/PosterGrid";
import { api } from "../api";
import { useScrollRestore } from "../hooks/useScrollRestore";
import { sourceLabel, useSource } from "../source";
import type { Card, HistoryItem } from "../types";

type Tab = "favorites" | "history";
type Entry = { card: Card; history?: HistoryItem };

const STEP = 120;

function episodeLabel(id?: string | null) {
  const n = /^(?:ep)?(\d{1,4})$/i.exec(id || "")?.[1];
  return n ? `第 ${Number(n)} 集` : null;
}

function fromHistory(item: HistoryItem): Entry {
  const progress = item.duration_sec > 0 ? Math.min(1, item.position_sec / item.duration_sec) : 0;
  return {
    card: { id: item.video_id, title: item.title, cover: item.cover, source: item.source, progress, duration: episodeLabel(item.episode_id) },
    history: item,
  };
}

/** 收藏 and 觀看紀錄 across every source, with filters and an undoable remove. */
export function Library({ tab }: { tab: Tab }) {
  const { prefix, sources } = useSource();
  // Filters and how far the list is expanded live in the URL, so Back returns to the same view.
  const [params, setParams] = useSearchParams();
  const only = params.get("only") || "";
  const text = params.get("t") || "";
  const shownCount = Math.max(STEP, Math.floor(Number(params.get("n")) || STEP));
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [err, setErr] = useState("");
  const [undo, setUndo] = useState<{ entry: Entry; index: number } | null>(null);
  const undoTimer = useRef(0);

  useEffect(() => {
    setEntries(null);
    setErr("");
    setUndo(null);
    let live = true;
    const load = tab === "favorites"
      ? api.favorites().then((r) => r.items.map((card): Entry => ({ card })))
      : api.history(1000).then((r) => r.items.map(fromHistory));
    load
      .then((list) => { if (live) setEntries(list); })
      .catch((e: Error) => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [tab]);

  useEffect(() => () => window.clearTimeout(undoTimer.current), []);
  useScrollRestore(entries !== null);

  const counts: Record<string, number> = {};
  for (const e of entries || []) counts[e.card.source || ""] = (counts[e.card.source || ""] || 0) + 1;
  const present = [...sources.map((s) => s.id).filter((id) => counts[id]), ...Object.keys(counts).filter((id) => id && !sources.some((s) => s.id === id))];
  const needle = text.trim().toLowerCase();
  const shown = (entries || []).filter((e) => (!only || e.card.source === only) && (!needle || e.card.title.toLowerCase().includes(needle)));
  const removeWord = tab === "favorites" ? "取消收藏" : "移除紀錄";

  function setView(changes: Record<string, string>, keepScroll = false) {
    const next = new URLSearchParams(params);
    for (const [name, value] of Object.entries(changes)) {
      if (value) next.set(name, value);
      else next.delete(name);
    }
    setParams(next, { replace: true, state: keepScroll ? { keepScroll: true } : undefined });
  }

  async function remove(card: Card) {
    const index = (entries || []).findIndex((e) => e.card === card);
    if (!entries || index < 0) return;
    const entry = entries[index];
    try {
      if (tab === "favorites") await api.removeFavorite(card.id, card.source);
      else await api.removeHistory(card.id, card.source || "");
    } catch (e) {
      setErr((e as Error).message);
      return;
    }
    setEntries((list) => (list || []).filter((e) => e !== entry));
    window.clearTimeout(undoTimer.current);
    setUndo({ entry, index });
    undoTimer.current = window.setTimeout(() => setUndo(null), 6000);
  }

  async function restore() {
    if (!undo) return;
    const { entry, index } = undo;
    window.clearTimeout(undoTimer.current);
    setUndo(null);
    try {
      if (tab === "favorites") await api.addFavorite(entry.card);
      else if (entry.history) {
        const h = entry.history;
        await api.saveHistory({
          id: h.video_id, title: h.title, cover: h.cover, position_sec: h.position_sec,
          duration_sec: h.duration_sec, source: h.source, episode_id: h.episode_id || undefined,
        });
      }
    } catch (e) {
      setErr((e as Error).message);
      return;
    }
    setEntries((list) => {
      const next = [...(list || [])];
      next.splice(Math.min(index, next.length), 0, entry);
      return next;
    });
  }

  return (
    <div>
      <div className="lib-tabs" role="tablist" aria-label="收藏與紀錄">
        <NavLink to={`${prefix}/favorites`} role="tab" data-tv="1">收藏</NavLink>
        <NavLink to={`${prefix}/history`} role="tab" data-tv="1">觀看紀錄</NavLink>
      </div>
      {err ? <div className="err" role="alert">{err}</div> : null}
      {entries === null && !err ? <SkeletonGrid count={8} /> : null}
      {entries?.length ? (
        <div className="lib-filters">
          <input
            className="field"
            value={text}
            onChange={(e) => setView({ t: e.target.value.slice(0, 50), n: "" })}
            placeholder="篩選片名"
            aria-label="篩選片名"
          />
          {present.length > 1 ? (
            <div className="chips" role="group" aria-label="依來源篩選">
              <button type="button" className={only ? "chip" : "chip on"} data-tv="1" onClick={() => setView({ only: "", n: "" })}>
                全部（{entries.length}）
              </button>
              {present.map((id) => (
                <button key={id} type="button" className={only === id ? "chip on" : "chip"} data-tv="1" onClick={() => setView({ only: id, n: "" })}>
                  {sourceLabel(sources, id)}（{counts[id]}）
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {entries ? (
        <PosterGrid
          items={shown.slice(0, shownCount).map((e) => e.card)}
          onRemove={(card) => void remove(card)}
          removeLabel={removeWord}
          empty={entries.length ? "沒有符合篩選的影片。"
            : tab === "favorites" ? "還沒有收藏。看影片時按「收藏」，就會出現在這裡。" : "還沒有觀看紀錄。"}
        />
      ) : null}
      {shown.length > shownCount ? (
        <div className="pager">
          <button className="btn alt" type="button" data-tv="1" onClick={() => setView({ n: String(shownCount + STEP) }, true)}>
            顯示更多（還有 {shown.length - shownCount} 部）
          </button>
        </div>
      ) : null}
      {undo ? (
        <div className="undo-bar" role="status">
          <span>已{removeWord}「{undo.entry.card.title}」</span>
          <button className="btn alt" type="button" data-tv="1" onClick={() => void restore()}>
            復原
          </button>
        </div>
      ) : null}
    </div>
  );
}
