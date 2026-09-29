import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { CastingController } from "../hooks/useCasting";

export function CastBar({ controller, currentEpisode, currentTitle }: { controller: CastingController; currentEpisode?: string; currentTitle?: string }) {
  const { devices, selected, setSelected, busy, active, status, msg, uncertain, canControl,
    scan, play, control, returnToLocal } = controller;
  const device = devices.find((d) => d.uuid === selected);
  const nestHub = /nest hub|google home hub/i.test(device?.model || "");
  const [mac, setMac] = useState("");
  useEffect(() => setMac(device?.mac || ""), [device?.uuid, device?.mac]);
  return (
    <div className="cast-bar" aria-label="電視投放控制">
      <p><label><input type="checkbox" checked={controller.inKaohsiung} disabled={busy || !!active}
        onChange={(e) => void controller.setInKaohsiung(e.target.checked)} /> 我在高雄</label>
        <span className="muted"> · 勾選後顯示高雄 Philips 電視</span></p>
      <div className="cast-actions">
        <button className="btn" type="button" disabled={busy || !controller.canPlay || !device || (device.online === false && device.kind !== "chromecast")} onClick={() => void play()}>
          {busy ? "處理中…" : active ? "重新投放" : "投放到電視"}
        </button>
        {device?.online === false && device.kind === "dlna" && !device.manual_power_on ? <button className="btn" disabled={busy || !controller.canPlay || !device.can_wake} onClick={() => void play(true)}>嘗試喚醒並投放</button> : null}
        <select className="field cast-target" aria-label="投放電視" value={selected} disabled={busy || !!active} onChange={(e) => setSelected(e.target.value)}>
          {!devices.some((d) => d.uuid === selected) ? <option value={selected}>{selected ? "已選電視離線" : "選擇電視"}</option> : null}
          {devices.map((d) => <option key={d.uuid} value={d.uuid}>{d.location === "kaohsiung" ? "高雄 · " : ""}{d.name}{d.online === false ? "（離線）" : ""} · {d.kind === "dlna" ? "DLNA" : "Chromecast"}</option>)}
        </select>
        <button className="btn alt" type="button" disabled={busy} onClick={() => void scan()}>掃描電視</button>
        {active ? <>
          <button className="btn alt" type="button" disabled={!canControl} onClick={() => void control(status?.paused ? "resume" : "pause")}>
            {status?.paused ? "電視續播" : "電視暫停"}
          </button>
          <button className="btn alt" type="button" disabled={!active.session_id || status?.pending_action === "stop"} onClick={() => void control("stop")}>停止投放</button>
          {status?.phase === "error" ? <button className="btn alt" disabled={busy} onClick={() => void controller.retry()}>重試這一集</button> : null}
          {uncertain || status?.idle || status?.phase === "stopped" || status?.phase === "ended" ? <button className="btn alt" type="button" disabled={busy} onClick={() => void returnToLocal()}>我已用遙控器停止，回本機</button> : null}
        </> : null}
        {currentTitle ? <span className="cast-current-title" title={currentTitle} aria-live="polite" style={{ flex: "1 1 180px", minWidth: 0, overflowWrap: "anywhere", fontWeight: 600, lineHeight: 1.4 }}>{currentTitle}</span> : null}
        {currentEpisode ? <span className="cast-current-episode" aria-live="polite">目前播放：{currentEpisode}</span> : null}
      </div>
      {device?.manual_power_on ? <p className="muted">這台電視需手動開機。請先用遙控器開機，再按「掃描電視」後投放。</p> : null}
      {device?.kind === "chromecast" ? <p className="muted">投放時會自動確認連線並啟動 Chromecast／Nest Hub；裝置需接通電源並連上同一個 Wi-Fi。電視自動開機需啟用 HDMI-CEC。</p> : null}
      {nestHub ? <p className="muted">Nest Hub 優先使用 720p 串流；高畫質 HLS 會自動轉為 720p 相容模式。</p> : null}
      {device?.kind === "dlna" && !device.manual_power_on ? <details><summary>電視喚醒設定</summary><p>已記住 MAC 不代表電視支援待機喚醒。若嘗試後未回應，請先用遙控器開機，再按「掃描電視」。電視需支援並開啟網路待機；LG 需開啟「行動裝置開啟電視／透過 Wi-Fi 開啟電視」。首次請開機掃描；若無法自動記錄，填入電視目前使用的網路 MAC 位址。</p>
        <input className="field" aria-label="電視 MAC 位址" value={mac} maxLength={17} placeholder="10:20:30:40:50:60" onChange={(e) => setMac(e.target.value)} />
        <button className="btn alt" disabled={busy} onClick={() => void controller.saveMac(mac)}>儲存 MAC</button></details> : null}
      <p className="cast-status" role="status">{msg || "選擇電視後即可投放"}{active && status && !busy ? ` · ${status.buffering ? "緩衝中" : status.paused ? "已暫停" : status.playing ? "播放中" : "已停止"}` : ""}{!devices.length && !busy ? <> · <Link to="/settings">投放設定</Link></> : null}</p>
    </div>
  );
}
