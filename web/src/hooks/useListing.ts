import { useEffect, useState } from "react";

type State<T> = { key: string; group: string; data?: T; err: string };

/**
 * Loads the list named by `key`, showing a kept copy at once when there is one.
 * While another page of the same list (same `group`) loads, the previous page stays on screen.
 */
export function useListing<T>(key: string, group: string, peek: () => T | undefined, load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<State<T>>(() => ({ key, group, data: peek(), err: "" }));
  const [forced, setForced] = useState({ key: "", n: 0 });

  useEffect(() => {
    // peek/load/group are derived from key, so key alone decides when to reload.
    const cached = forced.key === key ? undefined : peek();
    if (cached !== undefined) {
      setState({ key, group, data: cached, err: "" });
      return;
    }
    setState((prev) => ({ ...prev, data: prev.group === group ? prev.data : undefined, err: "" }));
    const controller = new AbortController();
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ key, group, data, err: "" });
      })
      .catch((e: Error) => {
        if (!controller.signal.aborted) setState({ key, group, data: undefined, err: e.message });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, forced]);

  const current = state.key === key;
  return {
    data: state.data,
    err: current ? state.err : "",
    /** A different page is loading while the previous one is still shown. */
    busy: !current,
    ready: current && state.data !== undefined,
    retry: () => setForced((prev) => ({ key, n: prev.n + 1 })),
  };
}
