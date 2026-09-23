from __future__ import annotations

from pydantic import BaseModel, Field


class Card(BaseModel):
    id: str
    title: str
    cover: str
    duration: str | None = None
    progress: float | None = None
    source: str = "hongguo"
    original_title: str | None = None


class Tag(BaseModel):
    browsable: bool = True
    name: str
    slug: str
    kind: str


class Listing(BaseModel):
    notice: str | None = None
    items: list[Card]
    page: int
    has_next: bool
    title: str = ""
    pages: int | None = None


class HomeRow(BaseModel):
    kind: str
    title: str
    items: list[Card]


class PickChip(BaseModel):
    name: str
    kind: str
    slug: str = ""


class PickGroup(BaseModel):
    title: str
    items: list[PickChip]


class HomePayload(BaseModel):
    continue_watching: list[Card]
    rows: list[HomeRow]
    mirror: str
    source: str = "hongguo"
    error: str | None = None
    picks: list[PickGroup] = Field(default_factory=list)


class Episode(BaseModel):
    id: str
    title: str
    playlist: str = ""


class VideoDetail(BaseModel):
    resolved_episode_id: str | None = None
    id: str
    source: str = "hongguo"
    title: str
    cover: str
    duration_sec: int | None = None
    duration_label: str | None = None
    release_date: str | None = None
    description: str | None = None
    original_title: str | None = None
    needs_translate: bool = False
    actresses: list[Tag] = Field(default_factory=list)
    genres: list[Tag] = Field(default_factory=list)
    maker: Tag | None = None
    playlist: str
    qualities: list[dict] = Field(default_factory=list)
    episodes: list[Episode] = Field(default_factory=list)
    related: list[Card] = Field(default_factory=list)
    favorited: bool = False
    position_sec: float = 0
    episode_id: str | None = None


class HistoryIn(BaseModel):
    id: str
    title: str
    cover: str = ""
    position_sec: float = 0
    duration_sec: float = 0
    source: str = "hongguo"
    episode_id: str | None = Field(default=None, max_length=16)


class FavoriteIn(BaseModel):
    id: str
    title: str
    cover: str = ""
    source: str = "hongguo"


class TvPairIn(BaseModel):
    code: str = ""


class CastPlayIn(BaseModel):
    cover: str = ""
    managed: bool = False
    source: str = ""
    video_id: str = ""
    episode_id: str = Field(default="", max_length=16)
    autoplay_next: bool = False
    wake: bool = False
    url: str = ""
    title: str = ""
    position_sec: float = Field(default=0, ge=0, allow_inf_nan=False)
    uuid: str = ""


class CastControlIn(BaseModel):
    action: str
    position_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    uuid: str = ""
    content_id: str = ""


class CastSessionControlIn(BaseModel):
    uuid: str
    session_id: str
    action: str
    position_sec: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    episode_id: str = Field(default="", max_length=16)
    autoplay_next: bool = False


class CastDeviceConfigIn(BaseModel):
    uuid: str
    mac: str = ""


class CastSelectIn(BaseModel):
    uuid: str = ""


class TranslateIn(BaseModel):
    title: str = ""
    description: str = ""
    names: list[str] = Field(default_factory=list)


class TranslateOut(BaseModel):
    title: str = ""
    description: str = ""
    names: list[str] = Field(default_factory=list)


class SourceInfo(BaseModel):
    id: str
    label: str


class SettingsOut(BaseModel):
    source: str = "hongguo"
    theme: str = ""
    lan_tv: bool = False
    lan_ips: list[str] = []
    tv_code: str = ""
    tv_url: str = ""
    sources: list[SourceInfo] = []


class SettingsIn(BaseModel):
    source: str | None = None
    theme: str | None = None
    lan_tv: bool | None = None
