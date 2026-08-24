"""SQLite ledger. Local only. Not git. Next pull inserts only new rows."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from schema import FIELDS

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_DB = DATA / "ledger.sqlite"
JSON_FIELDS = ("media", "urls", "mentions", "hashtags", "categories", "topics")

CREATE = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT NOT NULL,
    platform TEXT NOT NULL,
    author_id TEXT,
    author_handle TEXT,
    created_at TEXT,
    text TEXT,
    lang TEXT,
    post_kind TEXT,
    reply_to_id TEXT,
    quote_of_id TEXT,
    like_count INTEGER,
    repost_count INTEGER,
    reply_count INTEGER,
    views_count INTEGER,
    media TEXT,
    urls TEXT,
    mentions TEXT,
    hashtags TEXT,
    source TEXT,
    categories TEXT,
    topics TEXT,
    reply_to_handle TEXT,
    reply_to_name TEXT,
    PRIMARY KEY (platform, id)
);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_posts_handle ON posts(author_handle);
CREATE TABLE IF NOT EXISTS pulls (
    platform TEXT NOT NULL,
    ident TEXT NOT NULL,
    last_post_id TEXT,
    last_created_at TEXT,
    last_inserted INTEGER,
    last_skipped INTEGER,
    pulled_at TEXT,
    note TEXT,
    PRIMARY KEY (platform, ident)
);
CREATE TABLE IF NOT EXISTS profiles (
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    author_id TEXT,
    display_name TEXT,
    bio TEXT,
    avatar_url TEXT,
    followers INTEGER,
    following INTEGER,
    extra TEXT,
    updated_at TEXT,
    PRIMARY KEY (platform, handle)
);
"""

EXTRA_POST_COLS = {
    "topics": "TEXT",
    "reply_to_handle": "TEXT",
    "reply_to_name": "TEXT",
}


def db_path() -> Path:
    env = os.environ.get("POST_LEDGER_DB")
    return Path(env) if env else DEFAULT_DB


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(CREATE)
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    have = {r[1] for r in con.execute("PRAGMA table_info(posts)")}
    for col, typ in EXTRA_POST_COLS.items():
        if col not in have:
            con.execute("ALTER TABLE posts ADD COLUMN %s %s" % (col, typ))


def _pack(row: dict) -> tuple:
    out = []
    for k in FIELDS:
        v = row.get(k)
        if k in JSON_FIELDS:
            out.append(json.dumps(v or []))
        else:
            out.append(v)
    return tuple(out)


def _unpack(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_FIELDS:
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except json.JSONDecodeError:
            d[k] = []
    return d


def upsert_many(rows: list[dict]) -> int:
    """Replace existing rows (ZIP attic)."""
    if not rows:
        return 0
    con = connect()
    placeholders = ",".join("?" for _ in FIELDS)
    cols = ",".join(FIELDS)
    n = 0
    with con:
        for row in rows:
            con.execute(
                f"INSERT OR REPLACE INTO posts ({cols}) VALUES ({placeholders})",
                _pack(row),
            )
            n += 1
    con.close()
    return n


def insert_new(rows: list[dict]) -> tuple[int, int]:
    """Insert only unseen (platform, id). Next pull does not rewrite old posts."""
    if not rows:
        return 0, 0
    con = connect()
    placeholders = ",".join("?" for _ in FIELDS)
    cols = ",".join(FIELDS)
    inserted = 0
    skipped = 0
    with con:
        for row in rows:
            cur = con.execute(
                f"INSERT OR IGNORE INTO posts ({cols}) VALUES ({placeholders})",
                _pack(row),
            )
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
    con.close()
    return inserted, skipped


def known_ids(platform: str, handle: str = "") -> set[str]:
    con = connect()
    sql = "SELECT id FROM posts WHERE platform = ?"
    args: list = [platform]
    if handle:
        sql += " AND lower(author_handle) = lower(?)"
        args.append(handle.lstrip("@"))
    ids = {r[0] for r in con.execute(sql, args)}
    con.close()
    return ids


def newest_id(platform: str, handle: str) -> str:
    con = connect()
    row = con.execute(
        "SELECT id FROM posts WHERE platform = ? AND lower(author_handle) = lower(?) "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (platform, handle.lstrip("@")),
    ).fetchone()
    con.close()
    return str(row[0]) if row else ""


def query(
    handle: str = "",
    platform: str = "",
    category: str = "",
    limit: int = 0,
    offset: int = 0,
) -> list[dict]:
    con = connect()
    sql = "SELECT * FROM posts WHERE 1=1"
    args: list = []
    if handle:
        sql += " AND lower(author_handle) = lower(?)"
        args.append(handle.lstrip("@"))
    if platform:
        sql += " AND platform = ?"
        args.append(platform)
    if category:
        sql += " AND (categories LIKE ? OR topics LIKE ?)"
        args.extend(["%" + category + "%", "%" + category + "%"])
    sql += " ORDER BY created_at DESC"
    if int(limit) > 0:
        sql += " LIMIT ?"
        args.append(int(limit))
        if int(offset) > 0:
            sql += " OFFSET ?"
            args.append(int(offset))
    rows = [_unpack(r) for r in con.execute(sql, args)]
    con.close()
    return rows


def count_posts(handle: str = "", platform: str = "", category: str = "") -> int:
    con = connect()
    sql = "SELECT COUNT(*) FROM posts WHERE 1=1"
    args: list = []
    if handle:
        sql += " AND lower(author_handle) = lower(?)"
        args.append(handle.lstrip("@"))
    if platform:
        sql += " AND platform = ?"
        args.append(platform)
    if category:
        sql += " AND (categories LIKE ? OR topics LIKE ?)"
        args.extend(["%" + category + "%", "%" + category + "%"])
    n = con.execute(sql, args).fetchone()[0]
    con.close()
    return int(n)


def accounts() -> list[dict]:
    con = connect()
    rows = []
    for r in con.execute(
        "SELECT platform, author_handle, COUNT(*) AS n, "
        "MIN(created_at) AS first_at, MAX(created_at) AS last_at "
        "FROM posts GROUP BY platform, lower(author_handle) "
        "ORDER BY n DESC"
    ):
        rows.append(
            {
                "platform": r["platform"],
                "handle": r["author_handle"],
                "count": r["n"],
                "first_at": r["first_at"],
                "last_at": r["last_at"],
            }
        )
    con.close()
    return rows


def stats(handle: str = "", platform: str = "") -> dict:
    con = connect()
    sql = "SELECT COUNT(*) FROM posts WHERE 1=1"
    args: list = []
    if handle:
        sql += " AND lower(author_handle) = lower(?)"
        args.append(handle.lstrip("@"))
    if platform:
        sql += " AND platform = ?"
        args.append(platform)
    total = con.execute(sql, args).fetchone()[0]
    by_sql = "SELECT platform, COUNT(*) FROM posts WHERE 1=1"
    by_args: list = []
    if handle:
        by_sql += " AND lower(author_handle) = lower(?)"
        by_args.append(handle.lstrip("@"))
    by_sql += " GROUP BY platform"
    by_p = {r[0]: r[1] for r in con.execute(by_sql, by_args)}
    con.close()
    return {"total": total, "by_platform": by_p}


def record_pull(platform: str, ident: str, inserted: int, skipped: int, last_id: str, last_at: str, note: str) -> None:
    con = connect()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with con:
        con.execute(
            "INSERT INTO pulls(platform, ident, last_post_id, last_created_at, last_inserted, last_skipped, pulled_at, note) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(platform, ident) DO UPDATE SET "
            "last_post_id=excluded.last_post_id, last_created_at=excluded.last_created_at, "
            "last_inserted=excluded.last_inserted, last_skipped=excluded.last_skipped, "
            "pulled_at=excluded.pulled_at, note=excluded.note",
            (platform, ident.lstrip("@"), last_id, last_at, inserted, skipped, now, note),
        )
    con.close()


def last_pull(platform: str, ident: str) -> dict | None:
    con = connect()
    r = con.execute(
        "SELECT * FROM pulls WHERE platform = ? AND lower(ident) = lower(?)",
        (platform, ident.lstrip("@")),
    ).fetchone()
    con.close()
    return dict(r) if r else None


def last_ident() -> str:
    con = connect()
    r = con.execute("SELECT ident FROM pulls ORDER BY pulled_at DESC LIMIT 1").fetchone()
    if not r:
        r = con.execute(
            "SELECT author_handle FROM posts GROUP BY lower(author_handle) ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
    con.close()
    return str(r[0]) if r else ""


def upsert_profile(row: dict) -> None:
    handle = str(row.get("handle") or "").lstrip("@")
    platform = str(row.get("platform") or "")
    if not handle or not platform:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = connect()
    extra = row.get("extra")
    if not isinstance(extra, str):
        extra = json.dumps(extra or {})
    with con:
        con.execute(
            "INSERT INTO profiles(platform, handle, author_id, display_name, bio, avatar_url, followers, following, extra, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(platform, handle) DO UPDATE SET "
            "author_id=COALESCE(excluded.author_id, profiles.author_id), "
            "display_name=COALESCE(excluded.display_name, profiles.display_name), "
            "bio=COALESCE(excluded.bio, profiles.bio), "
            "avatar_url=COALESCE(excluded.avatar_url, profiles.avatar_url), "
            "followers=COALESCE(excluded.followers, profiles.followers), "
            "following=COALESCE(excluded.following, profiles.following), "
            "extra=excluded.extra, updated_at=excluded.updated_at",
            (
                platform,
                handle,
                row.get("author_id"),
                row.get("display_name"),
                row.get("bio"),
                row.get("avatar_url"),
                row.get("followers"),
                row.get("following"),
                extra,
                now,
            ),
        )
    con.close()


def get_profile(platform: str, handle: str) -> dict | None:
    con = connect()
    r = con.execute(
        "SELECT * FROM profiles WHERE platform = ? AND lower(handle) = lower(?)",
        (platform, handle.lstrip("@")),
    ).fetchone()
    con.close()
    return dict(r) if r else None


def backfill_derived() -> int:
    from categorize import categorize, topics_for

    con = connect()
    n = 0
    with con:
        for r in list(con.execute("SELECT * FROM posts")):
            d = _unpack(r)
            cats = categorize(d)
            tops = topics_for(d)
            rh = d.get("reply_to_handle") or ""
            if d.get("post_kind") == "reply" and not rh:
                mentions = d.get("mentions") or []
                if mentions:
                    rh = str(mentions[0]).lstrip("@")
            if (
                cats != (d.get("categories") or [])
                or tops != (d.get("topics") or [])
                or rh != (d.get("reply_to_handle") or "")
            ):
                con.execute(
                    "UPDATE posts SET categories=?, topics=?, reply_to_handle=? WHERE platform=? AND id=?",
                    (json.dumps(cats), json.dumps(tops), rh, d["platform"], d["id"]),
                )
                n += 1
    con.close()
    return n


def delete_platform(platform: str) -> int:
    con = connect()
    with con:
        cur = con.execute("DELETE FROM posts WHERE platform = ?", (platform,))
        n = cur.rowcount
    con.close()
    return int(n or 0)
