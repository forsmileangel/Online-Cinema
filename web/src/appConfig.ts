import type { SourceInfo } from "./types";

/**
 * Everything that differs between 深夜電影院 and 線上電影院. Every other file under src/ is the
 * same in both sites (tools/check-shared.py keeps it that way), so a fix lands in both at once.
 */
export const APP = {
  name: "線上電影院",
  /** localStorage keys are `${storagePrefix}.${name}` (see storageKey). */
  storagePrefix: "cinema",
  /** The scheduled task and the port, named in the settings page's restart hints. */
  task: "Online Cinema Server",
  port: 6970,
  defaultSource: "hongguo",
  /** Shown until /api/settings answers; the server list is the source of truth. */
  fallbackSources: [
    { id: "mvffm", label: "MVFFM 電影線上看", group: "影劇" },
    { id: "chinaq", label: "中國人線上看", group: "影劇" },
    { id: "gimy", label: "Gimy 劇迷", group: "影劇" },
    { id: "mmov", label: "MMOV 電影線上看", group: "影劇" },
    { id: "dramaq", label: "DramaQ", group: "影劇" },
    { id: "hongguo", label: "紅果短劇", group: "短劇" },
    { id: "anigamer", label: "巴哈姆特動畫瘋", group: "動畫" },
  ] as SourceInfo[],
  /** Home pages that take long upstream get a longer wait; null means every source does. */
  slowHome: null as string[] | null,
  /** Listings and searches that take long upstream. */
  slowList: ["chinaq", "dramaq"] as string[],
  /** Sources whose home page has no 選片 section. */
  noPicks: [] as string[],
  /** Sources whose video ids are shown as given; the others are 番號, shown upper-cased. null: never upper-case. */
  lowercaseIds: null as string[] | null,
  /** A source whose English listings get a 顯示原文 toggle and batch translation ("" for none). */
  translateSource: "",
  searchEmpty: "輸入劇名或關鍵字開始搜尋。",
  /** The people listed under a video. */
  peopleLabel: "演員",
  cast: {
    pick: "選擇電視",
    dlna: "DLNA",
    intro: "播放頁會有「投放到電視」。支援 Chromecast／Google TV 與 Philips、LG 等 DLNA 電視，與電腦位於同一區網。",
  },
  /** The lock screen and media keys see only the brand name, never a title. */
  privateMediaSession: false,
  features: {
    /** An 18+ gate before anything is shown. */
    ageGate: false,
    /** The ininAV account section in settings and the login hint on its videos. */
    ininav: false,
    /** The 黃果短劇 account section in settings and the login hint on its videos. */
    huangguoAccount: false,
    /** Mirror choice and listing language in settings (MissAV). */
    mirrors: false,
    /** The 我在高雄 switch that reveals the Kaohsiung TV. */
    kaohsiung: true,
    /** The live phone-connection panel (needs /api/connection on the server). */
    phoneConnection: true,
    /** Move settings saved under the old missav.* keys. */
    legacyMigration: false,
  },
  /** Browse headings that differ from the shared list. */
  browseTitles: { new: "最新上架", all: "全部", latest: "最近更新", hot: "最近熱門" } as Record<string, string>,
};

export const storageKey = (name: string) => `${APP.storagePrefix}.${name}`;
