import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { CastingController } from "../hooks/useCasting";

export function CastBar({ controller }: { controller: CastingController }) {
  const { devices, selected, setSelected, busy, active, status, msg, uncertain, canControl,
    scan, play, control, returnToLocal } = controller;
  const device = devices.find((d) => d.uuid === selected);
  const [mac, setMac] = useState("");
  useEffect(() => setMac(device?.mac || ""), [device?.uuid, device?.mac]);
  return (
    <div className="cast-bar" aria-label="電視投放控制">
      <div className="cast-actions">
        <button className="btn" type="button" disabled={busy || !controller.canPlay || !device || device.online === false} onClick={() => void play()}>
          {busy ? "處理中…" : active ? "重新投放" : "投放到電視"}
        </button>
        {device?.online === false ? <button className="btn" disabled={busy || !controller.canPlay || !device.can_wake} onClick={() => void play(true)}>喚醒並投放</button> : null}
        <select className="field cast-target" aria-label="投放電視" value={selected} disabled={busy || !!active} onChange={(e) => setSelected(e.target.value)}>
          {!devices.some((d) => d.uuid === selected) ? <option value={selected}>{selected ? "已選電視離線" : "選擇 Chromecast／LG"}</option> : null}
          {devices.map((d) => <option key={d.uuid} value={d.uuid}>{d.name}{d.online === false ? "（離線）" : ""} · {d.kind === "dlna" ? "LG／DLNA" : "Chromecast"}</option>)}
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
      </div>
      {device ? <details><summary>電視喚醒設定</summary><p>LG 需開啟「行動裝置開啟電視／透過 Wi-Fi 開啟電視」。首次請開機掃描；若無法自動記錄，填入電視目前使用的網路 MAC 位址。</p>
        <input className="field" aria-label="電視 MAC 位址" value={mac} maxLength={17} placeholder="10:20:30:40:50:60" onChange={(e) => setMac(e.target.value)} />
        <button className="btn alt" disabled={busy} onClick={() => void controller.saveMac(mac)}>儲存 MAC</button></details> : null}
      <p className="cast-status" role="status">{msg || "選擇電視後即可投放"}{active && status && !busy ? ` · ${status.buffering ? "緩衝中" : status.paused ? "已暫停" : status.playing ? "播放中" : "已停止"}` : ""}{!devices.length && !busy ? <> · <Link to="/settings">投放設定</Link></> : null}</p>
    </div>
  );
}
