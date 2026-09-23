export type Card = {
  id: string;
  title: string;
  cover: string;
  duration?: string | null;
  progress?: number | null;
  source?: string;
  original_title?: string | null;
};

export type Tag = { name: string; slug: string; kind: string };

export type Episode = { id: string; title: string; playlist: string };

export type Listing = {
  items: Card[];
  page: number;
  has_next: boolean;
  title: string;
  pages?: number | null;
};

export type PickChip = { name: string; kind: string; slug: string };
export type PickGroup = { title: string; items: PickChip[] };

export type HomePayload = {
  continue_watching: Card[];
  rows: { kind: string; title: string; items: Card[] }[];
  mirror: string;
  error?: string | null;
  picks?: PickGroup[];
};

export type VideoDetail = {
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

export type Settings = {
  source?: string;
  theme?: string;
  lan_tv?: boolean;
  lan_ips?: string[];
  tv_code?: string;
  tv_url?: string;
  sources?: { id: string; label: string }[];
};
