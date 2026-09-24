import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CastBar } from "../components/CastBar";
import { Player } from "../components/Player";
import { PosterRow } from "../components/PosterRow";
import { api } from "../api";
import { useCasting } from "../hooks/useCasting";
import { useSource } from "../source";
import type { Episode, VideoDetail } from "../types";

function looksJp(s: string) {
  return /[\u3040-\u30ff]/.test(s);
}

function mergeEpisodes(prev: VideoDetail, next: VideoDetail): VideoDetail {
  const incoming = new Map((next.episodes || []).filter((e) => e.playlist).map((e) => [e.id, e.playlist]));
  const episodes = (prev.episodes || []).map((e) =>
    incoming.has(e.id) ? { ...e, playlist: incoming.get(e.id) || e.playlist } : e,
  );
  const extras = (next.episodes || []).filter((e) => !episodes.some((x) => x.id === e.id));
  return {
    ...prev,
    episodes: [...episodes, ...extras],
    playlist: next.playlist || prev.playlist,
  };
}

function epKey(source: string, id: string) {
  return `cinema.ep.${source}.${id}`;
}

const AUTO_KEY = "cinema.autoplay.next";

function loadAutoplay() {
  try {
    const v = localStorage.getItem(AUTO_KEY);
    if (v === "0") return false;
    if (v === "1") return true;
  } catch {
    /* ignore */
  }
  return true;
}

function saveAutoplay(on: boolean) {
  try {
    localStorage.setItem(AUTO_KEY, on ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function FavUnderFs({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <div className="fs-col fav-idle">
      <button type="button" disabled>
        全螢幕
      </button>
      <button
        type="button"
        className={on ? "on" : ""}
        data-tv="1"
        aria-pressed={on}
        aria-label={on ? "取消收藏" : "收藏"}
        onClick={onClick}
      >
        {on ? "已收藏" : "收藏"}
      </button>
    </div>
  );
}

export function Watch() {
  const { prefix, zhMap } = useSource();
  const { id = "", source: sourceParam } = useParams();
  const source = sourceParam && sourceParam !== "watch" ? sourceParam : "hongguo";
  const [data, setData] = useState<VideoDetail | null>(null);
  const [err, setErr] = useState("");
  const [zhTitle, setZhTitle] = useState("");
  const [zhDesc, setZhDesc] = useState("");
  const [zhNames, setZhNames] = useState<string[]>([]);
  const [showOrig, setShowOrig] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [returnPosition, setReturnPosition] = useState<number>();
  const [epId, setEpId] = useState("");
  const [epTouched, setEpTouched] = useState(false);
  const [resumeEp, setResumeEp] = useState("");
  const [episodeError, setEpisodeError] = useState("");
  const [resolveAttempt, setResolveAttempt] = useState(0);
  const failedRefresh = useRef("");
  const [autoplay, setAutoplay] = useState(loadAutoplay);
  const posRef = useRef(0);
  const lastSent = useRef(0);
  const epRailRef = useRef<HTMLDivElement>(null);
  const endLock = useRef("");
  const autoplayRef = useRef(autoplay);
  autoplayRef.current = autoplay;

  useEffect(() => {
    setData(null);
    setErr("");
    setEpisodeError("");
    failedRefresh.current = "";
    setZhTitle("");
    setZhDesc("");
    setZhNames([]);
    setShowOrig(false);
    setReturnPosition(undefined);
    setEpId("");
    setEpTouched(false);
    setResumeEp("");
    posRef.current = 0;
    lastSent.current = 0;
    endLock.current = "";
    const controller = new AbortController();
    api
      .video(id, source, controller.signal)
      .then((next) => { if (!controller.signal.aborted) { posRef.current = next.position_sec || 0; setData(next); } })
      .catch((e: Error) => { if (!controller.signal.aborted) setErr(e.message); });
    return () => controller.abort();
  }, [id, source]);

  useEffect(() => {
    if (!data) return;
    const blob = `${data.title} ${data.original_title || ""} ${data.description || ""}`;
    const auto = data.needs_translate || looksJp(blob);
    if (!auto) return;
    const names = [
      ...data.actresses.map((t) => t.name),
      ...data.genres.map((t) => t.name),
      ...(data.maker ? [data.maker.name] : []),
    ];
    setTranslating(true);
    const origTitle = data.original_title || data.title;
    api
      .translate({
        title: origTitle,
        description: data.description || "",
        names,
      })
      .then((t) => {
        setZhTitle(t.title && t.title !== origTitle ? t.title : "");
        setZhDesc(t.description && t.description !== data.description ? t.description : "");
        setZhNames(t.names || []);
      })
      .catch(() => {})
      .finally(() => setTranslating(false));
  }, [data?.id, source]);

  useEffect(() => {
    if (!data) return;
    const ids = (data.episodes || []).map((e) => e.id);
    if (!ids.length) {
      setEpId("");
      return;
    }
    let saved = "";
    try {
      saved = localStorage.getItem(epKey(source, data.id)) || "";
    } catch {
      /* ignore */
    }
    const pick = data.resolved_episode_id && ids.includes(data.resolved_episode_id) ? data.resolved_episode_id : data.episode_id && ids.includes(data.episode_id) ? data.episode_id : ids.includes(saved) ? saved : ids[0];
    setResumeEp((cur) => cur || pick);
    setEpId((cur) => (ids.includes(cur) ? cur : pick));
  }, [data, source]);

  useEffect(() => {
    if (!data || !epId) return;
    const ids = (data.episodes || []).map((e) => e.id);
    const want = [epId];
    if (autoplay) {
      const i = ids.indexOf(epId);
      if (i >= 0 && i + 1 < ids.length) want.push(ids[i + 1]);
    }
    const missing = want.filter((eid) => {
      const ep = (data.episodes || []).find((e) => e.id === eid);
      return ep && !ep.playlist;
    });
    if (!missing.length) return;
    const controller = new AbortController();
    for (const eid of missing) {
      void api.video(id, source, controller.signal, eid).then((next) => {
        if (controller.signal.aborted) return;
        if (!(next.episodes || []).some((e) => e.id === eid && e.playlist)) throw new Error("這一集尚無可播放的來源");
        setData((prev) => prev ? mergeEpisodes(prev, next) : prev);
        if (eid === epId) setEpisodeError("");
      }).catch((e) => {
        if (!controller.signal.aborted && eid === epId) setEpisodeError(e instanceof Error ? e.message : "解析失敗，請重試");
      });
    }
    return () => controller.abort();
  }, [epId, id, source, autoplay, resolveAttempt]);

  const onProgress = useCallback(
    (pos: number, dur: number) => {
      posRef.current = pos;
      if (dur > 1 && pos < dur - 1) endLock.current = "";
      if (!data || dur < 1) return;
      const now = Date.now();
      if (now - lastSent.current < 5000) return;
      lastSent.current = now;
      void api.saveHistory({
        id: data.id,
        title: data.title,
        cover: data.cover,
        position_sec: pos,
        duration_sec: dur,
        source: data.source || source,
        episode_id: epId || undefined,
      }).catch(() => {});
    },
    [data, source, epId],
  );

  const origMode = showOrig;
  const origTitle = data?.original_title || data?.title || "";
  const hasZh = Boolean(zhTitle || zhDesc || zhNames.some((n) => n) || zhMap[origTitle]);
  const title = origMode ? origTitle : zhTitle || zhMap[origTitle] || origTitle;
  const desc = origMode ? data?.description : zhDesc || data?.description;
  function zhName(i: number, orig: string) {
    const n = zhNames[i];
    return !origMode && n && n !== orig ? n : orig;
  }
  const episodes: Episode[] = data?.episodes || [];
  let savedEp = "";
  try {
    savedEp = data ? localStorage.getItem(epKey(source, data.id)) || "" : "";
  } catch {
    /* ignore */
  }
  const epIds = episodes.map((e) => e.id);
  const activeEp = epIds.includes(epId) ? epId : data?.resolved_episode_id && epIds.includes(data.resolved_episode_id) ? data.resolved_episode_id : data?.episode_id && epIds.includes(data.episode_id) ? data.episode_id : epIds.includes(savedEp) ? savedEp : epIds[0] || "";
  const currentEp = episodes.find((e) => e.id === activeEp);
  const playlist = currentEp?.playlist || (!episodes.length ? data?.playlist || "" : "");
  const resume = data?.episode_id || resumeEp;
  const startAt = episodes.length
    ? (!epTouched && data?.episode_id && activeEp === resume ? data?.position_sec || 0 : 0)
    : data?.position_sec || 0;
  const castTitle = activeEp && episodes.length > 1 ? `${title} ${currentEp?.title || activeEp}` : title;
  const cast = useCasting({
    identity: `${source}:${id}`,
    context: { cover: data?.cover || "", source, video_id: id, episode_id: activeEp, autoplay_next: autoplay },
    playlist: data?.id === id && data.source === source ? playlist : "",
    title: castTitle,
    getPosition: () => posRef.current,
    onEpisode: (episode, remotePlaylist, nextAutoplay) => {
      if (episode) setEpId(episode);
      setAutoplay(nextAutoplay);
      setData((prev) => {
        if (!prev || !episode || !remotePlaylist || prev.episodes?.find((e) => e.id === episode)?.playlist === remotePlaylist) return prev;
        return { ...prev, episodes: prev.episodes?.map((e) => e.id === episode ? { ...e, playlist: remotePlaylist } : e) };
      });
    },
    onReturn: setReturnPosition,
  });

  useEffect(() => {
    const root = epRailRef.current;
    const on = root?.querySelector<HTMLElement>(".chip.on");
    if (!root || !on) return;
    const r = on.getBoundingClientRect();
    const b = root.getBoundingClientRect();
    if (r.top < b.top) root.scrollTop -= b.top - r.top;
    else if (r.bottom > b.bottom) root.scrollTop += r.bottom - b.bottom;
  }, [activeEp]);

  function pickEpisode(nextId: string) {
    if (nextId === epId) return;
    if (cast.active) { void cast.episode(nextId); return; }
    setEpisodeError("");
    endLock.current = "";
    failedRefresh.current = "";
    setEpId(nextId);
    setEpTouched(true);
    setReturnPosition(0);
    posRef.current = 0;
    lastSent.current = 0;
    try {
      localStorage.setItem(epKey(source, id), nextId);
    } catch {
      /* ignore */
    }
  }

  const onEnded = useCallback(() => {
    if (!autoplayRef.current || cast.active) return;
    const ids = (data?.episodes || []).map((e) => e.id);
    const current = ids.includes(epId) ? epId : ids[0] || "";
    const i = ids.indexOf(current);
    if (i < 0 || i >= ids.length - 1) return;
    const token = `${id}:${current}`;
    if (endLock.current === token) return;
    endLock.current = token;
    pickEpisode(ids[i + 1]);
  }, [cast.active, data, epId, id]);

  function retryEpisode() {
    setEpisodeError("");
    setData((prev) => prev ? { ...prev, episodes: prev.episodes?.map((e) => e.id === activeEp ? { ...e, playlist: "" } : e) } : prev);
    setResolveAttempt((n) => n + 1);
    if (!episodes.length) {
      setData((prev) => prev ? { ...prev, playlist: "" } : prev);
      void api.video(id, source).then((next) => { setData(next); setEpisodeError(""); }).catch((e) => setEpisodeError(e.message));
    }
  }

  function playbackError() {
    const token = `${source}:${id}:${activeEp}`;
    if (failedRefresh.current !== token) { failedRefresh.current = token; retryEpisode(); }
    else setEpisodeError("播放來源無法載入，請重試或選擇其他集數。");
  }

  async function toggleFav() {
    if (!data) return;
    const src = data.source || source;
    if (data.favorited) await api.removeFavorite(data.id, src);
    else await api.addFavorite({ id: data.id, title: data.title, cover: data.cover, source: src });
    setData({ ...data, favorited: !data.favorited });
  }

  if (err) return <div className="err">無法播放：{err}</div>;
  if (!data) return <div className="empty">解析片源中…</div>;

  const manyEps = episodes.length > 1;

  return (
    <div className="watch">
      <div className={`watch-stage${manyEps ? " has-eps" : ""}`}>
        <div className="player-wrap">
          <div className="player-screen">
            {!cast.restoring && playlist && !episodeError ? (
              <Player key={`${activeEp}:${resolveAttempt}`} onError={playbackError} src={playlist} startAt={startAt} onProgress={onProgress} onEnded={onEnded} remote={cast} returnPosition={returnPosition} favorited={!!data.favorited} onToggleFav={() => void toggleFav()} />
            ) : (
              <div className="player">
                <div className="empty">{episodeError ? "這一集無法播放" : `載入第${activeEp || ""}集…`}</div>
                <FavUnderFs on={!!data.favorited} onClick={() => void toggleFav()} />
              </div>
            )}
            {episodeError ? <p className="banner err" role="alert">{episodeError} <button className="btn" onClick={retryEpisode}>重試這一集</button></p> : null}
          </div>
          <CastBar controller={cast} currentEpisode={currentEp?.title} />
        </div>
        {manyEps ? (
          <aside className="ep-rail" aria-label="選集">
            <div className="ep-label">選集</div>
            <button
              type="button"
              className={`ep-auto${autoplay ? " on" : ""}`}
              data-tv="1"
              aria-pressed={autoplay}
              disabled={cast.busy}
              onClick={() => {
                const next = !autoplay;
                if (cast.active) void cast.autoplay(next);
                else setAutoplay(next);
                saveAutoplay(next);
              }}
            >
              {autoplay ? "自動下一集：開" : "自動下一集：關"}
            </button>
            <div className="ep-rail-list" ref={epRailRef}>
              {episodes.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  className={`chip${e.id === activeEp ? " on" : ""}`}
                  data-tv="1"
                  disabled={cast.busy}
                  onClick={() => pickEpisode(e.id)}
                >
                  {e.title}
                </button>
              ))}
            </div>
          </aside>
        ) : null}
      </div>
      <div className="meta">
        <h1>{title}</h1>
        {!hasZh && data.original_title && data.original_title !== title ? (
          <p className="orig-title">{data.original_title}</p>
        ) : null}
        <p className="muted">
          {data.id}
          {activeEp && manyEps ? ` · ${currentEp?.title || activeEp}` : ""}
          {data.duration_label ? ` · ${data.duration_label}` : ""}
          {data.release_date ? ` · ${data.release_date}` : ""}
          {translating ? " · 翻譯中…" : ""}
        </p>
        <div className="actions">
          {hasZh ? (
            <button className="btn alt" type="button" onClick={() => setShowOrig(!showOrig)}>
              {showOrig ? "顯示譯文" : "顯示原文"}
            </button>
          ) : looksJp(`${data.title} ${data.description || ""}`) ? (
            <button
              className="btn alt"
              type="button"
              disabled={translating}
              onClick={() => {
                setTranslating(true);
                api
                  .translate({ title: data.title, description: data.description || "" })
                  .then((t) => {
                    setZhTitle(t.title);
                    setZhDesc(t.description);
                    setShowOrig(false);
                  })
                  .finally(() => setTranslating(false));
              }}
            >
              翻譯成繁中
            </button>
          ) : null}
        </div>
        {data.actresses.length ? (
          <div className="cast-block">
            <div className="cast-label">演員</div>
            <div className="chips">
              {data.actresses.map((t, i) => (
                <Link
                  key={t.slug}
                  className="chip actor"
                  to={`${prefix}/c/${t.kind || "actresses"}/${encodeURIComponent(t.slug)}`}
                >
                  {zhName(i, t.name)}
                </Link>
              ))}
            </div>
          </div>
        ) : null}
        {desc ? <p className="synopsis">{desc}</p> : null}
        <div className="chips">
          {data.genres.map((t, i) => t.browsable === false ? (
            <span key={t.slug} className="chip">{zhName(data.actresses.length + i, t.name)}</span>
          ) : (
            <Link key={t.slug} className="chip" to={`${prefix}/c/${t.kind || "genres"}/${encodeURIComponent(t.slug)}`}>
              {zhName(data.actresses.length + i, t.name)}
            </Link>
          ))}
          {data.maker ? (
            <Link className="chip" to={`${prefix}/c/makers/${encodeURIComponent(data.maker.slug)}`}>
              {zhName(data.actresses.length + data.genres.length, data.maker.name)}
            </Link>
          ) : null}
        </div>
      </div>
      <PosterRow title="相關作品" items={data.related.filter((x) => x.id !== data.id)} />
    </div>
  );
}
