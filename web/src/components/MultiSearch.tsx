import { useEffect, useState } from "react";
import { api } from "../api";
import { useScrollRestore } from "../hooks/useScrollRestore";
import { searchPath, type Scope } from "../searchPrefs";
import { sourceGroup, useSource } from "../source";
import type { MultiResult } from "../types";
import { SkeletonRows } from "./PosterGrid";
import { PosterRow } from "./PosterRow";

type State = { key: string; results: Record<string, MultiResult>; err: string };

/** Searches several sources at once; each source's row appears as soon as that source answers. */
export function MultiSearch({ q, src, scope }: { q: string; src: string; scope: Exclude<Scope, "source"> }) {
  const { sources } = useSource();
  const group = sourceGroup(sources, src);
  const pool = scope === "all" || !group ? sources : sources.filter((s) => s.group === group);
  const ordered = [...pool.filter((s) => s.id === src), ...pool.filter((s) => s.id !== src)];
  const ids = ordered.map((s) => s.id).join(",");
  const key = `${scope}\n${ids}\n${q}`;
  const [state, setState] = useState<State>(() => ({ key, results: api.peekMulti(key) || {}, err: "" }));
  const [forced, setForced] = useState({ key: "", n: 0 });

  useEffect(() => {
    const kept = forced.key === key ? undefined : api.peekMulti(key);
    if (kept) {
      setState({ key, results: kept, err: "" });
      return;
    }
    setState({ key, results: {}, err: "" });
    const controller = new AbortController();
    const got: Record<string, MultiResult> = {};
    api
      .searchMulti(q, ids.split(","), (result) => {
        got[result.source] = result;
        if (!controller.signal.aborted) setState({ key, results: { ...got }, err: "" });
      }, controller.signal)
      .then(() => {
        if (!controller.signal.aborted) api.keepMulti(key, got);
      })
      .catch((e: Error) => {
        if (!controller.signal.aborted) setState({ key, results: { ...got }, err: e.message });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, forced]);

  const results = state.key === key ? state.results : {};
  const err = state.key === key ? state.err : "";
  const answered = ordered.filter((s) => results[s.id]);
  const pending = ordered.filter((s) => !results[s.id]);
  const finished = pending.length === 0 || !!err;
  const found = answered.filter((s) => results[s.id].ok && results[s.id].listing?.items.length);
  // An empty answer that carries a notice was not a real search (e.g. a refused site); show why.
  const limited = (s: { id: string }) => results[s.id].ok && !results[s.id].listing?.items.length && !!results[s.id].listing?.notice;
  const empty = answered.filter((s) => results[s.id].ok && !results[s.id].listing?.items.length && !limited(s));
  const failed = answered.filter((s) => !results[s.id].ok || limited(s));
  useScrollRestore(finished);

  return (
    <div>
      {!finished ? (
        <p className="muted" role="status">
          已完成 {answered.length}／{ordered.length} 個來源，還在搜尋：{pending.map((s) => s.label).join("、")}
        </p>
      ) : null}
      {err ? (
        <div className="err" role="alert">
          {err}。
          <button className="btn alt" type="button" onClick={() => setForced((prev) => ({ key, n: prev.n + 1 }))}>
            重新搜尋
          </button>
        </div>
      ) : null}
      {found.map((s) => {
        const listing = results[s.id].listing!;
        const more = listing.has_next || (listing.pages || 0) > 1;
        return (
          <PosterRow
            key={s.id}
            title={`${s.label}（${listing.items.length}${more ? "+" : ""}）`}
            to={searchPath("", q, s.id)}
            moreLabel="看全部"
            note={listing.notice}
            items={listing.items}
            lines={2}
          />
        );
      })}
      {!finished && !found.length ? <SkeletonRows rows={2} /> : null}
      {finished && !found.length && !err ? <div className="empty">這些來源都沒有找到「{q}」。</div> : null}
      {empty.length ? <p className="muted">沒有結果：{empty.map((s) => s.label).join("、")}</p> : null}
      {failed.length ? (
        <ul className="multi-failed">
          {failed.map((s) => (
            <li key={s.id}>
              <strong>{s.label}</strong>：{results[s.id].error || results[s.id].listing?.notice || "暫時無法搜尋"}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
