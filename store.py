"""SQLite ledger. Local only. Not git."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from schema import FIELDS

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "ledger.sqlite"

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
    PRIMARY KEY (platform, id)
);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);
CREATE INDEX IF NOT EXISTS idx_posts_handle ON posts(author_handle);
"""


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(CREATE)
    return con


def _pack(row: dict) -> tuple:
    out = []
    for k in FIELDS:
        v = row.get(k)
        if k in ("media", "urls", "mentions", "hashtags", "categories"):
            out.append(json.dumps(v or []))
        else:
            out.append(v)
    return tuple(out)


def upsert_many(rows: list[dict]) -> int:
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


def query(handle: str = "", platform: str = "", category: str = "", limit: int = 0) -> list[dict]:
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
        sql += " AND categories LIKE ?"
        args.append("%" + category + "%")
    sql += " ORDER BY created_at DESC"
    if int(limit) > 0:
        sql += " LIMIT ?"
        args.append(int(limit))
    rows = []
    for r in con.execute(sql, args):
        d = dict(r)
        for k in ("media", "urls", "mentions", "hashtags", "categories"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except json.JSONDecodeError:
                d[k] = []
        rows.append(d)
    con.close()
    return rows


def stats() -> dict:
    con = connect()
    total = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    by_p = {r[0]: r[1] for r in con.execute("SELECT platform, COUNT(*) FROM posts GROUP BY platform")}
    con.close()
    return {"total": total, "by_platform": by_p}
