import { useEffect, useRef, useState } from "react";

type Resume = { position: number; paused: boolean };
type Failure = Error & { status?: number; retryAfterSeconds?: number };

export function usePlaybackRecovery(identity: string, enabled: boolean,
  restore: (resume: Resume, signal: AbortSignal) => Promise<void>) {
  const [state, setState] = useState({ pending: false, message: "" });
  const operation = useRef({ version: 0, timer: 0, controller: null as AbortController | null, pending: false,
    attempts: 0, healthySince: 0, position: 0 });
  const restoreRef = useRef(restore);
  restoreRef.current = restore;

  function cancel() {
    const current = operation.current;
    current.version++;
    window.clearTimeout(current.timer);
    current.controller?.abort();
    current.controller = null;
    current.pending = false;
  }

  useEffect(() => {
    cancel();
    operation.current.attempts = 0;
    setState({ pending: false, message: "" });
    return cancel;
  }, [identity, enabled]);

  function schedule(resume: Resume, error?: Failure, immediate = false) {
    cancel();
    if (!enabled) return;
    if (error && [401, 403, 404, 410].includes(error.status || 0)) {
      setState({ pending: false, message: `${error.message}。已保留播放進度，可稍後重試。` });
      return;
    }
    const current = operation.current;
    const attempt = current.attempts++;
    const backoff = [2, 5, 10, 20, 30][Math.min(attempt, 4)];
    const seconds = immediate ? 0 : Math.max(backoff, error?.retryAfterSeconds || 0);
    const version = current.version;
    current.pending = true;
    current.position = resume.position;
    current.healthySince = 0;
    setState({ pending: true, message: `${error?.message || "播放連線中斷"}；${seconds} 秒後從原進度自動重連（第 ${attempt + 1} 次）。` });
    current.timer = window.setTimeout(async () => {
      if (current.version !== version) return;
      const controller = new AbortController();
      current.controller = controller;
      setState({ pending: true, message: "正在重新連接這一集，保留原播放進度…" });
      try {
        await restoreRef.current(resume, controller.signal);
        if (current.version !== version) return;
        current.pending = false;
        current.healthySince = Date.now();
        setState({ pending: false, message: "" });
      } catch (error) {
        if (current.version === version && !controller.signal.aborted) schedule(resume, error as Failure);
      }
    }, seconds * 1000);
  }

  return {
    ...state,
    start(position: number, paused: boolean) {
      if (!operation.current.pending) schedule({ position: Number.isFinite(position) ? Math.max(0, position) : 0, paused });
    },
    retryNow(position: number, paused: boolean) {
      operation.current.attempts = 0;
      schedule({ position: Number.isFinite(position) ? Math.max(0, position) : 0, paused }, undefined, true);
    },
    stop() { cancel(); setState({ pending: false, message: "已停止自動重試，播放進度已保留。" }); },
    progress(position: number) {
      const current = operation.current;
      // A successful manifest or play event alone does not prove recovery.
      if (!current.pending && current.healthySince && Date.now() - current.healthySince >= 15000
          && position >= current.position + 10) current.attempts = 0;
    },
  };
}
