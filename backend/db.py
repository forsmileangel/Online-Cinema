from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from . import settings as S
from .catalog import SOURCES, normalize_source
from .security import safe_video_id


def store_id(source: str, video_id: str) -> str:
    return f"{normalize_source(source)}:{safe_video_id(video_id)}"


def parse_store_id(raw: str) -> tuple[str, str]:
    raw = raw or ""
    if ":" in raw:
        a, b = raw.split(":", 1)
        # Keep a foreign prefix intact so old rows stay hidden instead of being relabeled.
        if a in SOURCES or (a.isascii() and a.replace("-", "").isalnum() and a == a.lower() and b):
            return a, b
    return "hongguo", raw

_local = threading.local()


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        path: Path = S.db_path()
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _init(conn)
        _local.conn = conn
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS favorites (
            video_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            cover TEXT NOT NULL DEFAULT '',
            added_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS history (
            video_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            cover TEXT NOT NULL DEFAULT '',
            position_sec REAL NOT NULL DEFAULT 0,
            duration_sec REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS translations (
            src_hash TEXT PRIMARY KEY,
            src TEXT NOT NULL,
            dst TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    if "episode_id" not in {row[1] for row in conn.execute("PRAGMA table_info(history)")}:
        conn.execute("ALTER TABLE history ADD COLUMN episode_id TEXT")
    conn.commit()
    _migrate_keys(conn)


def _migrate_keys(conn: sqlite3.Connection) -> None:
    for table in ("favorites", "history"):
        rows = conn.execute(f"SELECT video_id FROM {table}").fetchall()
        for row in rows:
            vid = str(row["video_id"])
            if ":" not in vid:
                conn.execute(
                    f"UPDATE {table} SET video_id = ? WHERE video_id = ?",
                    (f"hongguo:{vid}", vid),
                )
    conn.commit()


def get_setting(key: str, default: str = "") -> str:
    row = connect().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    connect().execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    connect().commit()


def list_favorites() -> list[dict]:
    rows = connect().execute(
        "SELECT video_id, title, cover, added_at FROM favorites ORDER BY added_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        src, vid = parse_store_id(d["video_id"])
        if src not in SOURCES:
            continue
        d["source"] = src
        d["video_id"] = vid
        out.append(d)
    return out


def is_favorite(video_id: str, source: str = "hongguo") -> bool:
    key = store_id(source, video_id)
    row = connect().execute("SELECT 1 FROM favorites WHERE video_id = ?", (key,)).fetchone()
    return row is not None


def add_favorite(video_id: str, title: str, cover: str, source: str = "hongguo") -> None:
    key = store_id(source, video_id)
    connect().execute(
        "INSERT INTO favorites(video_id, title, cover, added_at) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(video_id) DO UPDATE SET title = excluded.title, cover = excluded.cover",
        (key, title[:300], cover[:500], time.time()),
    )
    connect().commit()


def remove_favorite(video_id: str, source: str = "hongguo") -> None:
    key = store_id(source, video_id)
    connect().execute("DELETE FROM favorites WHERE video_id = ?", (key,))
    connect().commit()


def upsert_history(
    video_id: str,
    title: str,
    cover: str,
    position_sec: float,
    duration_sec: float,
    source: str = "hongguo",
    episode_id: str | None = None,
) -> None:
    key = store_id(source, video_id)
    connect().execute(
        "INSERT INTO history(video_id, title, cover, position_sec, duration_sec, updated_at, episode_id) "
        "VALUES(?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(video_id) DO UPDATE SET "
        "title = excluded.title, cover = excluded.cover, "
        "position_sec = excluded.position_sec, duration_sec = excluded.duration_sec, episode_id = excluded.episode_id, "
        "updated_at = excluded.updated_at",
        (key, title[:300], cover[:500], float(position_sec), float(duration_sec), time.time(), episode_id),
    )
    connect().commit()


def get_history_item(video_id: str, source: str = "hongguo") -> dict | None:
    key = store_id(source, video_id)
    row = connect().execute(
        "SELECT video_id, title, cover, position_sec, duration_sec, episode_id FROM history WHERE video_id = ?",
        (key,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    src, vid = parse_store_id(d["video_id"])
    d["source"] = src
    d["video_id"] = vid
    return d


def list_history(limit: int = 40, source: str | None = None) -> list[dict]:
    limit = max(1, min(int(limit), 80))
    rows = connect().execute(
        "SELECT video_id, title, cover, position_sec, duration_sec, updated_at, episode_id "
        "FROM history ORDER BY updated_at DESC LIMIT ?",
        (limit * 2,),
    ).fetchall()
    out = []
    want = normalize_source(source) if source else None
    for r in rows:
        d = dict(r)
        src, vid = parse_store_id(d["video_id"])
        if src not in SOURCES or (want and src != want):
            continue
        d["source"] = src
        d["video_id"] = vid
        out.append(d)
        if len(out) >= limit:
            break
    return out


def clear_history() -> None:
    connect().execute("DELETE FROM history")
    connect().commit()


def _src_hash(src: str) -> str:
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def get_translation(src: str) -> str | None:
    row = connect().execute(
        "SELECT dst FROM translations WHERE src_hash = ?",
        (_src_hash(src),),
    ).fetchone()
    return str(row["dst"]) if row else None


def put_translation(src: str, dst: str) -> None:
    connect().execute(
        "INSERT INTO translations(src_hash, src, dst, updated_at) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(src_hash) DO UPDATE SET dst = excluded.dst, updated_at = excluded.updated_at",
        (_src_hash(src), src[:4000], dst[:4000], time.time()),
    )
    connect().commit()
