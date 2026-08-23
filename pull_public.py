"""Pull public posts until the platform returns no more.

Hitch mixed ToS with criminal law earlier. This module does not invent a
200-post cap. It paginates official/public HTTP APIs to exhaustion.

It does not forge guest tokens, solve CAPTCHAs, or rotate IPs to bypass
access controls. If the platform stops, we stop.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from categorize import categorize
from schema import empty_post

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.local.json"
UA = "PostLedger/0.2 (local archive; contact=local-only)"
# Loop guard only — not a history product cap.
MAX_ROWS = 100_000


def _cfg() -> dict:
    if not CONFIG.is_file():
        return {}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _get_json(url: str, headers: dict | None = None, timeout: int = 30) -> dict:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def pull_reddit(ident: str) -> tuple[list[dict], str]:
    ident = ident.lstrip("u/").lstrip("/")
    after = None
    rows: list[dict] = []
    note = ""
    while len(rows) < MAX_ROWS:
        q = {"limit": "100", "raw_json": "1"}
        if after:
            q["after"] = after
        url = "https://www.reddit.com/user/%s/submitted.json?%s" % (
            urllib.parse.quote(ident),
            urllib.parse.urlencode(q),
        )
        try:
            data = _get_json(url)
        except urllib.error.HTTPError as e:
            note = "reddit HTTP %s" % e.code
            break
        except urllib.error.URLError as e:
            note = "reddit network %s" % e.reason
            break
        children = ((data.get("data") or {}).get("children")) or []
        if not children:
            break
        for ch in children:
            d = ch.get("data") or {}
            pid = str(d.get("id") or "")
            if not pid:
                continue
            text = d.get("selftext") or d.get("title") or ""
            post = empty_post(
                id=pid,
                platform="reddit",
                author_id=str(d.get("author_fullname") or ""),
                author_handle=str(d.get("author") or ident),
                created_at=str(d.get("created_utc") or ""),
                text=text,
                lang="",
                post_kind="original",
                like_count=int(d.get("ups") or 0),
                repost_count=0,
                reply_count=int(d.get("num_comments") or 0),
                urls=[d.get("url")] if d.get("url") else [],
                source="reddit-public-json",
            )
            post["categories"] = categorize(post)
            rows.append(post)
        after = (data.get("data") or {}).get("after")
        if not after:
            break
        time.sleep(0.4)
    return rows, note or ("reddit pages until empty, n=%s" % len(rows))


def pull_bluesky(ident: str) -> tuple[list[dict], str]:
    ident = ident.lstrip("@")
    cursor = None
    rows: list[dict] = []
    note = ""
    while len(rows) < MAX_ROWS:
        q = {"actor": ident, "limit": "100"}
        if cursor:
            q["cursor"] = cursor
        url = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?" + urllib.parse.urlencode(q)
        try:
            data = _get_json(url)
        except urllib.error.HTTPError as e:
            note = "bluesky HTTP %s" % e.code
            break
        except urllib.error.URLError as e:
            note = "bluesky network %s" % e.reason
            break
        feed = data.get("feed") or []
        if not feed:
            break
        for item in feed:
            post = (item.get("post") or {})
            rec = post.get("record") or {}
            uri = str(post.get("uri") or "")
            if not uri:
                continue
            author = post.get("author") or {}
            text = rec.get("text") or ""
            reply = rec.get("reply") or {}
            kind = "reply" if reply else "original"
            row = empty_post(
                id=uri,
                platform="bluesky",
                author_id=str(author.get("did") or ""),
                author_handle=str(author.get("handle") or ident),
                created_at=str(rec.get("createdAt") or post.get("indexedAt") or ""),
                text=text,
                lang="",
                post_kind=kind,
                like_count=int((post.get("likeCount") or 0)),
                repost_count=int((post.get("repostCount") or 0)),
                reply_count=int((post.get("replyCount") or 0)),
                source="bsky-public-api",
            )
            row["categories"] = categorize(row)
            rows.append(row)
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.2)
    return rows, note or ("bluesky pages until empty, n=%s" % len(rows))


def pull_x_api(ident: str, bearer: str) -> tuple[list[dict], str]:
    ident = ident.lstrip("@")
    headers = {"Authorization": "Bearer " + bearer}
    try:
        user = _get_json(
            "https://api.x.com/2/users/by/username/%s" % urllib.parse.quote(ident),
            headers=headers,
        )
    except urllib.error.HTTPError as e:
        try:
            user = _get_json(
                "https://api.twitter.com/2/users/by/username/%s" % urllib.parse.quote(ident),
                headers=headers,
            )
        except urllib.error.HTTPError as e2:
            return [], "x API HTTP %s (username lookup)" % e2.code
    uid = str((user.get("data") or {}).get("id") or "")
    handle = str((user.get("data") or {}).get("username") or ident)
    if not uid:
        return [], "x API: no user id for %s" % ident
    token = None
    rows: list[dict] = []
    note = ""
    host = "https://api.x.com"
    while len(rows) < MAX_ROWS:
        q = {
            "max_results": "100",
            "tweet.fields": "created_at,lang,public_metrics,entities,referenced_tweets,source,text",
        }
        if token:
            q["pagination_token"] = token
        url = host + "/2/users/%s/tweets?%s" % (uid, urllib.parse.urlencode(q))
        try:
            data = _get_json(url, headers=headers)
        except urllib.error.HTTPError as e:
            if host.endswith("x.com") and e.code in (404, 530):
                host = "https://api.twitter.com"
                continue
            note = "x API HTTP %s after %s posts" % (e.code, len(rows))
            break
        except urllib.error.URLError as e:
            note = "x API network %s" % e.reason
            break
        tweets = data.get("data") or []
        if not tweets:
            break
        for t in tweets:
            refs = t.get("referenced_tweets") or []
            kind = "original"
            reply_to = None
            quote_of = None
            for ref in refs:
                rt = ref.get("type")
                rid = str(ref.get("id") or "")
                if rt == "replied_to":
                    kind = "reply"
                    reply_to = rid
                elif rt == "retweeted":
                    kind = "repost"
                elif rt == "quoted":
                    kind = "quote"
                    quote_of = rid
            ents = t.get("entities") or {}
            metrics = t.get("public_metrics") or {}
            post = empty_post(
                id=str(t.get("id") or ""),
                platform="x",
                author_id=uid,
                author_handle=handle,
                created_at=str(t.get("created_at") or ""),
                text=t.get("text") or "",
                lang=t.get("lang") or "",
                post_kind=kind,
                reply_to_id=reply_to,
                quote_of_id=quote_of,
                like_count=int(metrics.get("like_count") or 0),
                repost_count=int(metrics.get("retweet_count") or 0),
                reply_count=int(metrics.get("reply_count") or 0),
                views_count=int(metrics.get("impression_count") or 0),
                urls=[u.get("expanded_url") or u.get("url") for u in ents.get("urls") or []],
                mentions=[m.get("username") for m in ents.get("mentions") or []],
                hashtags=[h.get("tag") for h in ents.get("hashtags") or []],
                source="x-api-v2",
            )
            post["categories"] = categorize(post)
            rows.append(post)
        token = (data.get("meta") or {}).get("next_token")
        if not token:
            break
        time.sleep(0.3)
    return rows, note or ("x API pages until empty, n=%s" % len(rows))


def pull_all(platform: str, ident: str) -> dict:
    platform = (platform or "").lower().strip()
    ident = (ident or "").strip().lstrip("@")
    if not ident:
        return {"ok": False, "error": "need a handle or id", "count": 0}
    cfg = _cfg()
    rows: list[dict] = []
    note = ""
    if platform in ("reddit",):
        rows, note = pull_reddit(ident)
    elif platform in ("bluesky", "bsky"):
        rows, note = pull_bluesky(ident)
    elif platform in ("x", "twitter", "x.com"):
        bearer = str(cfg.get("x_bearer") or "").strip()
        if bearer:
            rows, note = pull_x_api(ident, bearer)
        else:
            return {
                "ok": False,
                "count": 0,
                "id": ident,
                "platform": "x",
                "error": "X closed unauthenticated bulk history. Put x_bearer in config.local.json and I paginate until the API returns no more. I will not forge guest tokens. Own-account ZIP is still the complete attic.",
            }
    else:
        return {
            "ok": False,
            "count": 0,
            "id": ident,
            "platform": platform,
            "error": "no public paginated API wired for this platform yet. ZIP ingest still works.",
        }
    from store import upsert_many

    n = upsert_many(rows)
    return {"ok": True, "count": n, "id": ident, "platform": platform, "note": note}
