export type Card = {
  id: string;
  title: string;
  cover: string;
  duration?: string | null;
  progress?: number | null;
  source?: string;
  original_title?: string | null;
};

export type Tag = { name: string; slug: string; kind: string; browsable?: boolean };

export type Episode = { id: string; title: string; playlist: string };

export type Listing = {
  notice?: string | null;
  items: Card[];
  page: number;
  has_next: boolean;
  title: string;
  pages?: number | null;
  sorts?: SortOption[];
  /** The option in effect (an id from sorts). */
  sort?: string;
};

/** kind set: another list of the source (/c/kind/slug); otherwise ?sort=id on this list. */
export type SortOption = { id: string; label: string; kind?: string; slug?: string };

export type PickChip = { name: string; kind: string; slug: string };
export type PickGroup = { title: string; items: PickChip[] };

export type HomePayload = {
  continue_watching: Card[];
  /** more: false when the source has nothing behind the row's 更多. */
  rows: { kind: string; slug?: string; title: string; items: Card[]; more?: boolean }[];
  mirror: string;
  error?: string | null;
  picks?: PickGroup[];
};

export type VideoDetail = {
  resolved_episode_id?: string | null;
  id: string;
  source?: string;
  title: string;
  cover: string;
  duration_sec?: number | null;
  duration_label?: string | null;
  release_date?: string | null;
  description?: string | null;
  original_title?: string | null;
  needs_translate?: boolean;
  actresses: Tag[];
  genres: Tag[];
  maker?: Tag | null;
  playlist: string;
  episodes?: Episode[];
  related: Card[];
  favorited: boolean;
  position_sec: number;
  episode_id?: string;
};

export type SourceInfo = { id: string; label: string; group?: string };

/** A new mechanism that runs beside the earlier one until it is switched on. */
export type Trial = { id: string; label: string; note: string; on: boolean; used: number; fallbacks: number };

export type HistoryItem = {
  video_id: string;
  source: string;
  title: string;
  cover: string;
  position_sec: number;
  duration_sec: number;
  updated_at: number;
  episode_id?: string | null;
};

export type MultiResult = { source: string; ok: boolean; listing?: Listing; error?: string };
/** The same show on another source: its episode numbers paired with episode ids. */
export type Alternative = { source: string; label: string; id: string; title: string; count: number; episodes: [number, string][] };

export type Settings = {
  /** MissAV mirrors and listing language (深夜電影院 only). */
  mirror?: string;
  forced_mirror?: string;
  locale?: string;
  mirrors?: string[];
  in_kaohsiung?: boolean;
  source?: string;
  theme?: string;
  lan_tv?: boolean;
  lan_ips?: string[];
  tv_code?: string;
  tv_url?: string;
  sources?: SourceInfo[];
  trials?: Trial[];
};
