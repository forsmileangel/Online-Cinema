import { useEffect, useRef, useState } from "react";
import { api, type CastDevice, type CastSession } from "../api";

type Context = { cover: string; source: string; video_id: string; episode_id: string; autoplay_next: boolean };
export function useCasting({ identity, playlist, title, context, getPosition, onReturn, onEpisode }: {
  identity: string; playlist: string; title: string; context: Context;
  getPosition: () => number; onReturn: (position: number) => void;
  onEpisode: (episode: string, playlist: string, autoplay: boolean) => void;
}) {
  const [devices, setDevices] = useState<CastDevice[]>([]);
  const [selected, setSelected] = useState("");
  const [inKaohsiung, setInKaohsiung] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [active, setActive] = useState<{ uuid: string; session_id: string } | null>(null);
  const [status, setStatus] = useState<CastSession | null>(null);
  const [msg, setMsg] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const generation = useRef(0);
  const pending = useRef(false);
  const lastPosition = useRef(0);
  const seekTarget = useRef<{ position: number; until: number } | null>(null);
  const callbacks = useRef({ getPosition, onReturn, onEpisode, context });
  callbacks.current = { getPosition, onReturn, onEpisode, context };

  function trackPosition(next: CastSession) {
    // Buffering after a seek already reports the new spot; LG may report 0 while transitioning.
    if (next.idle || !(next.playing || next.paused || (next.buffering && next.current_time > 0))) return;
    const target = seekTarget.current;
    if (target) {
      // A queued seek is answered with the old position until the TV reaches the new one.
      if (Date.now() < target.until && Math.abs(next.current_time - target.position) > 5) return;
      seekTarget.current = null;
    }
    lastPosition.current = next.current_time;
  }

  function remember(next: CastSession) {
    setStatus(next);
    setUncertain(!!next.error || next.phase === "error" || next.phase === "replaced");
    trackPosition(next);
    callbacks.current.onEpisode(next.episode_id, next.playlist, next.autoplay_next);
    const labels: Record<string, string> = { reconnecting: "正在重新連接電視，確認原本的播放進度…", waking: "正在喚醒電視，最多等待 60 秒…", loading: "正在載入，等待電視確認播放…",
      playing: "電視已確認播放，本機保持暫停。關閉網頁後仍可自動連播。", paused: "電視已確認暫停", ended: "已播放完畢", stopped: "電視已停止" };
    const pendingLabel = next.pending_action === "stop" ? "正在停止投放，等待電視回應…"
      : next.pending_action === "episode" ? "正在換集，確認片源與電視狀態中…" : "";
    setMsg(next.error || pendingLabel || next.warning || labels[next.phase] || "等待電視回應…");
  }

  async function scan(inKaohsiung?: boolean) {
    if (pending.current) return;
    pending.current = true; setRequesting(true);
    const version = generation.current;
    try {
      if (inKaohsiung !== undefined) {
        const saved = await api.saveSettings({ in_kaohsiung: inKaohsiung });
        if (version !== generation.current) return;
        setInKaohsiung(!!saved.in_kaohsiung);
        setDevices([]); setSelected("");
      }
      const next = await api.castDevices();
      if (version !== generation.current) return;
      setDevices(next.devices);
      setInKaohsiung(next.in_kaohsiung);
      setSelected((old) => [old, next.selected].find((uuid) => next.devices.some((d) => d.uuid === uuid)) || next.devices[0]?.uuid || "");
      if (!next.devices.length) setMsg("沒找到電視，請先開機並確認位於同一 Wi-Fi。");
    } catch (e) { if (version === generation.current) setMsg(e instanceof Error ? e.message : "掃描失敗"); }
    finally { if (version === generation.current) { pending.current = false; setRequesting(false); } }
  }

  useEffect(() => {
    const version = ++generation.current;
    pending.current = false;
    setActive(null); setStatus(null); setRestoring(true); setUncertain(false);
    const controller = new AbortController();
    // Restore before allowing local playback; unmount never stops the server.
    void api.castSession("", controller.signal).then((next) => {
      if (version !== generation.current || !next) return;
      const ctx = callbacks.current.context;
      if (next.source === ctx.source && next.video_id === ctx.video_id && !["stopped", "ended", "replaced"].includes(next.phase)) {
        setActive({ uuid: next.uuid, session_id: next.session_id }); setSelected(next.uuid); remember(next);
      }
    }).catch((e) => { if (!controller.signal.aborted) setMsg(e instanceof Error ? e.message : "無法確認投放狀態"); })
      .finally(() => { if (version === generation.current) setRestoring(false); });
    void scan();
    return () => { ++generation.current; controller.abort(); pending.current = false; };
  }, [identity]);

  useEffect(() => {
    if (!active?.session_id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const next = await api.castSession(active!.uuid, controller.signal);
        if (cancelled) return;
        if (!next || next.session_id !== active!.session_id) {
          setStatus(null); setUncertain(true); setMsg("電視播放已變更，本機保持暫停。請重新投放或回本機。");
        } else if (!pending.current) remember(next);
      } catch (e) { if (!cancelled) { setUncertain(true); setMsg(e instanceof Error ? e.message : "無法確認電視狀態"); } }
      finally { if (!cancelled) timer = setTimeout(poll, 1500); }
    }
    void poll();
    return () => { cancelled = true; clearTimeout(timer); controller.abort(); };
  }, [active]);

  const busy = requesting || !!status?.pending_action || status?.phase === "waking" || status?.phase === "loading" || status?.phase === "reconnecting";
  const canControl = !!active && !!status && (status.playing || status.paused) && !busy && !uncertain;
  const canSeek = canControl && status!.duration > 0;
  const canSetVolume = canControl && !!status?.can_set_volume;
  const canMute = canControl && !!status?.can_mute;

  async function play(wake = false) {
    if (!selected || !playlist || pending.current) return;
    pending.current = true; setRequesting(true);
    const version = generation.current;
    const position = active ? lastPosition.current : callbacks.current.getPosition();
    lastPosition.current = position;
    seekTarget.current = null;
    setActive({ uuid: selected, session_id: "" }); setStatus(null); setUncertain(false);
    setMsg(wake ? "正在喚醒電視…" : "正在建立投放…");
    try {
      const next = await api.castSessionPlay({ url: playlist, title, position_sec: position, uuid: selected, ...context, wake });
      if (version !== generation.current) return;
      setActive({ uuid: next.uuid, session_id: next.session_id }); remember(next);
    } catch (e) { if (version === generation.current) { setUncertain(true); setMsg(e instanceof Error ? e.message : "投放失敗"); } }
    finally { if (version === generation.current) { pending.current = false; setRequesting(false); } }
  }

  async function command(action: string, values: { position_sec?: number; episode_id?: string; autoplay_next?: boolean; volume_level?: number; muted?: boolean } = {}) {
    if (!active?.session_id || pending.current) return;
    const version = generation.current;
    pending.current = true; setRequesting(true);
    try {
      const next = await api.castSessionControl(active.uuid, active.session_id, action, values);
      if (version === generation.current) remember(next);
    } catch (e) { if (version === generation.current) { setUncertain(true); setMsg(e instanceof Error ? e.message : "控制失敗"); } }
    finally { if (version === generation.current) { pending.current = false; setRequesting(false); } }
  }

  async function control(action: "pause" | "resume" | "seek" | "stop", position?: number) {
    if (action !== "stop" && !(action === "seek" ? canSeek : canControl)) return;
    if (action === "seek" && position != null && active?.session_id && !pending.current) {
      // Stopping right after a seek must return to where the user dragged to.
      seekTarget.current = { position, until: Date.now() + 20000 };
      lastPosition.current = position;
    }
    await command(action, { position_sec: position });
  }

  async function setVolume(volume_level: number) {
    if (canSetVolume) await command("volume", { volume_level });
  }

  async function setMuted(muted: boolean) {
    if (canMute) await command("mute", { muted });
  }

  async function returnToLocal() {
    if (active?.session_id) {
      try { await api.castSessionControl(active.uuid, active.session_id, "detach"); }
      catch (e) { setMsg(e instanceof Error ? e.message : "尚未停用連播，請重試"); return; }
    }
    setActive(null); setStatus(null); setUncertain(false);
    callbacks.current.onReturn(lastPosition.current); setMsg("已返回本機，可按播放繼續。");
  }

  async function saveMac(mac: string) {
    try { await api.castDeviceMac(selected, mac); await scan(); setMsg("已儲存電視 MAC 位址"); }
    catch (e) { setMsg(e instanceof Error ? e.message : "儲存失敗"); }
  }

  return { canPlay: !!playlist, devices, selected, setSelected, inKaohsiung, setInKaohsiung: (enabled: boolean) => scan(enabled), busy, restoring, active, status, msg, uncertain, canControl, canSeek, canSetVolume, canMute, setVolume, setMuted, scan, play, control, returnToLocal,
    episode: (episode_id: string) => command("episode", { episode_id }),
    autoplay: (autoplay_next: boolean) => command("autoplay", { autoplay_next }), retry: () => command("retry"), saveMac };
}
export type CastingController = ReturnType<typeof useCasting>;
