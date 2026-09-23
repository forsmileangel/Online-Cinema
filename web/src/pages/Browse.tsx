import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Pager } from "../components/Pager";
import { PosterGrid } from "../components/PosterGrid";
import { api } from "../api";
import { useSource } from "../source";
import type { Listing } from "../types";

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
  const { source } = useSource();
  const { kind = "", slug } = useParams();
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Listing | null>(null);
  const [err, setErr] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    setPage(1);
  }, [kind, slug, source]);

  useEffect(() => {
    setErr("");
    setData(null);
    const controller = new AbortController();
    api
      .browse(kind, slug, page, source, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setData(next); })
      .catch((e: Error) => { if (!controller.signal.aborted) setErr(e.message); });
    return () => controller.abort();
  }, [kind, slug, page, source, retry]);

  if (err) return <div className="err">{err}。<button className="btn alt" type="button" onClick={() => setRetry(retry + 1)}>重新載入</button></div>;
  if (!data) return <div className="empty">載入中…</div>;
  const heading = data.title || (slug ? decodeURIComponent(slug) : "") || TITLES[kind] || kind;

  return (
    <div>
      <h1 className="h1">{heading}</h1>
      <PosterGrid items={data.items} large />
      <Pager page={page} pages={data.pages} hasNext={data.has_next} onPage={setPage} />
    </div>
  );
}
