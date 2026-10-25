import { useParams, useSearchParams } from "react-router-dom";
import { Pager } from "../components/Pager";
import { PosterGrid, SkeletonGrid } from "../components/PosterGrid";
import { api } from "../api";
import { useListing } from "../hooks/useListing";
import { useScrollRestore } from "../hooks/useScrollRestore";
import { usePageSource } from "../source";

const TITLES: Record<string, string> = {
  new: "最新上架",
  featured: "熱門短劇",
  recent: "最近上新",
  "ranks-hot": "排行榜",
  tag: "標籤",
  all: "全部",
  gudian: "古裝",
  dushi: "都市",
  jiating: "家庭",
  xiju: "喜劇",
  juqing: "劇情",
  qihuan: "奇幻",
  qingchun: "青春偶像",
  dongzuo: "動作",
  lishi: "歷史",
  wangju: "網劇",
  tags: "標籤",
  genres: "類型",
  zhanzheng: "戰爭",
  fanzui: "犯罪",
  jingdian: "經典",
  xiangcun: "鄉村",
  qingjing: "情景",
  shangzhan: "商戰",
  qita: "其他",
  update: "最近更新",
  latest: "最近更新",
  hot: "最近熱門",
  cn: "陸劇",
  kr: "韓劇",
  jp: "日劇",
  hk: "港劇",
  th: "泰劇",
  tv: "電視劇",
  movie: "電影",
  anime: "動漫",
  show: "綜藝",
  us: "美劇",
  short: "短劇",
  ai: "AI漫劇",
  overseas: "海外劇",
  doc: "紀錄片",
  top: "排行榜",
};

export function Browse() {
  const source = usePageSource();
  const { kind = "", slug } = useParams();
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, Math.floor(Number(params.get("page")) || 1));
  const list = `${source}\n${kind}\n${slug || ""}`;
  const { data, err, busy, ready, retry } = useListing(
    `${list}\n${page}`,
    list,
    () => api.peekBrowse(kind, slug, page, source),
    (signal) => api.browse(kind, slug, page, source, signal),
  );
  useScrollRestore(ready);

  function setPage(n: number) {
    const next = new URLSearchParams(params);
    if (n > 1) next.set("page", String(n));
    else next.delete("page");
    setParams(next);
  }

  if (err) return <div className="err">{err}。<button className="btn alt" type="button" onClick={retry}>重新載入</button></div>;
  const heading = data?.title || (slug ? decodeURIComponent(slug) : "") || TITLES[kind] || kind;
  if (!data) {
    return (
      <div>
        <h1 className="h1">{heading}</h1>
        <SkeletonGrid />
      </div>
    );
  }

  return (
    <div>
      <h1 className="h1">{heading}</h1>
      {data.notice ? <p className="muted" role="status">{data.notice}</p> : null}
      {busy ? <p className="muted" role="status">載入第 {page} 頁…</p> : null}
      <PosterGrid items={data.items} busy={busy} />
      <Pager page={page} pages={data.pages} hasNext={data.has_next} onPage={setPage} />
    </div>
  );
}
