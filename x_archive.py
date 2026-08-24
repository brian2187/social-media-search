"""Parse an official X data-export ZIP or unpacked data/ folder.

Complete history is only in the archive the account owner requested.
https://help.x.com/en/managing-your-account/how-to-download-your-x-archive
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from categorize import categorize, topics_for
from schema import empty_post

YTD_ASSIGN = re.compile(
    r"^window\.YTD\.[A-Za-z0-9_]+\.part\d+\s*=\s*",
    re.MULTILINE,
)


def _strip_js(text: str) -> str:
    text = text.lstrip("\ufeff")
    text = YTD_ASSIGN.sub("", text, count=1)
    text = text.strip()
    if text.endswith(";"):
        text = text[:-1].strip()
    return text


def _read_ytd(raw: bytes) -> list:
    text = raw.decode("utf-8", errors="replace")
    payload = _strip_js(text)
    if not payload:
        return []
    data = json.loads(payload)
    if isinstance(data, list):
        return data
    return []


def _from_tweet(obj: dict, handle: str = "") -> dict:
    t = obj.get("tweet") if "tweet" in obj else obj
    if not isinstance(t, dict):
        return empty_post()
    text = t.get("full_text") or t.get("text") or ""
    ents = t.get("entities") or {}
    media_e = (t.get("extended_entities") or {}).get("media") or ents.get("media") or []
    media = []
    for m in media_e:
        media.append(
            {
                "type": m.get("type") or "photo",
                "url": m.get("media_url_https") or m.get("media_url") or "",
            }
        )
    urls = [u.get("expanded_url") or u.get("url") for u in ents.get("urls") or [] if u]
    mentions = [m.get("screen_name") for m in ents.get("user_mentions") or [] if m.get("screen_name")]
    hashtags = [h.get("text") for h in ents.get("hashtags") or [] if h.get("text")]
    reply = t.get("in_reply_to_status_id_str") or t.get("in_reply_to_status_id")
    reply_handle = str(t.get("in_reply_to_screen_name") or "").lstrip("@")
    quote = None
    if t.get("is_quote_status"):
        quote = str(t.get("quoted_status_id_str") or t.get("quoted_status_id") or "") or None
    rt = t.get("retweeted_status") or t.get("retweeted_status_id_str")
    if str(text).startswith("RT @") or rt:
        kind = "repost"
    elif reply:
        kind = "reply"
    elif quote:
        kind = "quote"
    else:
        kind = "original"
    user_id = ""
    user = t.get("user") if isinstance(t.get("user"), dict) else {}
    if user:
        user_id = str(user.get("id_str") or user.get("id") or "")
        handle = handle or str(user.get("screen_name") or "")
    post = empty_post(
        id=str(t.get("id_str") or t.get("id") or ""),
        platform="x",
        author_id=user_id,
        author_handle=handle.lstrip("@"),
        created_at=t.get("created_at") or "",
        text=text,
        lang=t.get("lang") or "",
        post_kind=kind,
        reply_to_id=str(reply) if reply else None,
        quote_of_id=quote,
        like_count=int(t.get("favorite_count") or 0),
        repost_count=int(t.get("retweet_count") or 0),
        reply_count=int(t.get("reply_count") or 0),
        views_count=int((t.get("ext_views") or {}).get("count") or t.get("view_count") or 0),
        media=media,
        urls=[u for u in urls if u],
        mentions=mentions,
        hashtags=hashtags,
        reply_to_handle=reply_handle or (mentions[0] if reply and mentions else ""),
        source="x-archive",
    )
    post["categories"] = categorize(post)
    post["topics"] = topics_for(post)
    return post


def _iter_archive_files(root: Path):
    names = ("tweets.js", "tweet.js")
    seen = set()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        n = p.name.lower()
        if n in names or n.startswith("tweets-part") or n.startswith("tweet-part"):
            if p.suffix.lower() in (".js", ".json"):
                key = str(p.resolve())
                if key not in seen:
                    seen.add(key)
                    yield p


def parse_folder(folder: Path, handle: str = "") -> list[dict]:
    rows = []
    for p in _iter_archive_files(folder):
        items = _read_ytd(p.read_bytes())
        for obj in items:
            row = _from_tweet(obj, handle=handle)
            if row.get("id"):
                rows.append(row)
    return rows


def parse_zip(zip_path: Path, handle: str = "") -> list[dict]:
    rows = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            low = name.replace("\\", "/").lower()
            base = low.rsplit("/", 1)[-1]
            if not (base.endswith(".js") or base.endswith(".json")):
                continue
            if not (
                base in ("tweets.js", "tweet.js")
                or base.startswith("tweets-part")
                or base.startswith("tweet-part")
            ):
                continue
            items = _read_ytd(zf.read(name))
            for obj in items:
                row = _from_tweet(obj, handle=handle)
                if row.get("id"):
                    rows.append(row)
    return rows
