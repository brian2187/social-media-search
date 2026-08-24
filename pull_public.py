"""Pull public posts until the platform returns no more.

Next pull stops at posts already in the ledger. No Hitch 200-post cap.
Does not forge guest tokens, solve CAPTCHAs, or rotate IPs.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from categorize import categorize, topics_for
from schema import empty_post

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.local.json"
UA = "PostLedger/0.3 (local archive; contact=local-only)"
MAX_ROWS = 100_000
MENTION_RE = re.compile(r"@([A-Za-z0-9_]{1,15})")
HASHTAG_RE = re.compile(r"#(\w+)")


def _cfg() -> dict:
    if not CONFIG.is_file():
        return {}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _get_json(url: str, headers: dict | None = None, timeout: int = 30):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if not raw:
            return {}
        if "json" not in ctype and raw[:1] not in (b"{", b"["):
            return raw, ctype
        text = raw.decode("utf-8", errors="replace")
        return json.loads(text) if text else {}


def _get_bytes(url: str, timeout: int = 20) -> tuple[bytes, str]:
    h = {"User-Agent": UA, "Accept": "image/*,application/json"}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), (resp.headers.get("Content-Type") or "application/octet-stream")


def _finish(post: dict) -> dict:
    post["categories"] = categorize(post)
    post["topics"] = topics_for(post)
    return post


def _reply_handle(mentions, explicit: str = "") -> str:
    if explicit:
        return str(explicit).lstrip("@")
    if mentions:
        return str(mentions[0]).lstrip("@")
    return ""


def pull_reddit(ident: str, known: set[str] | None = None) -> tuple[list[dict], str]:
    ident = ident.lstrip("u/").lstrip("/")
    known = known or set()
    after = None
    rows: list[dict] = []
    note = ""
    hit_old = False
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
        if not isinstance(data, dict):
            note = "reddit unexpected body"
            break
        children = ((data.get("data") or {}).get("children")) or []
        if not children:
            break
        for ch in children:
            d = ch.get("data") or {}
            pid = str(d.get("id") or "")
            if not pid:
                continue
            if pid in known:
                hit_old = True
                break
            author = str(d.get("author") or ident)
            if author.lower() not in (ident.lower(), "[deleted]"):
                continue
            text = d.get("selftext") or d.get("title") or ""
            post = empty_post(
                id=pid,
                platform="reddit",
                author_id=str(d.get("author_fullname") or ""),
                author_handle=author,
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
            rows.append(_finish(post))
        if hit_old:
            note = note or ("reddit incremental, new=%s" % len(rows))
            break
        after = (data.get("data") or {}).get("after")
        if not after:
            break
        time.sleep(0.4)
    return rows, note or ("reddit pages until empty, n=%s" % len(rows))


def pull_bluesky(ident: str, known: set[str] | None = None) -> tuple[list[dict], str, dict]:
    ident = ident.lstrip("@")
    known = known or set()
    cursor = None
    rows: list[dict] = []
    note = ""
    profile: dict = {}
    hit_old = False
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
        if not isinstance(data, dict):
            note = "bluesky unexpected body"
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
            handle = str(author.get("handle") or "")
            ident_l = ident.lower()
            if handle.lower() != ident_l and ident_l not in handle.lower():
                # feed includes other people's posts (reposts / context). skip.
                continue
            if uri in known:
                hit_old = True
                break
            if not profile:
                profile = {
                    "platform": "bluesky",
                    "handle": handle or ident,
                    "author_id": str(author.get("did") or ""),
                    "display_name": author.get("displayName") or handle,
                    "avatar_url": author.get("avatar") or "",
                }
            text = rec.get("text") or ""
            reply = rec.get("reply") or {}
            kind = "reply" if reply else "original"
            parent = (reply.get("parent") or {}) if reply else {}
            reply_to = str(parent.get("uri") or "") or None
            row = empty_post(
                id=uri,
                platform="bluesky",
                author_id=str(author.get("did") or ""),
                author_handle=handle or ident,
                created_at=str(rec.get("createdAt") or post.get("indexedAt") or ""),
                text=text,
                lang="",
                post_kind=kind,
                reply_to_id=reply_to,
                like_count=int((post.get("likeCount") or 0)),
                repost_count=int((post.get("repostCount") or 0)),
                reply_count=int((post.get("replyCount") or 0)),
                source="bsky-public-api",
            )
            rows.append(_finish(row))
        if hit_old:
            note = note or ("bluesky incremental, new=%s" % len(rows))
            break
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.2)
    return rows, note or ("bluesky pages until empty, n=%s" % len(rows)), profile


def pull_x_api(ident: str, bearer: str, known: set[str] | None = None, since_id: str = "") -> tuple[list[dict], str, dict]:
    ident = ident.lstrip("@")
    known = known or set()
    headers = {"Authorization": "Bearer " + bearer}
    profile: dict = {}
    try:
        user = _get_json(
            "https://api.x.com/2/users/by/username/%s?%s"
            % (
                urllib.parse.quote(ident),
                urllib.parse.urlencode(
                    {"user.fields": "created_at,description,profile_image_url,public_metrics,name"}
                ),
            ),
            headers=headers,
        )
    except urllib.error.HTTPError:
        try:
            user = _get_json(
                "https://api.twitter.com/2/users/by/username/%s?%s"
                % (
                    urllib.parse.quote(ident),
                    urllib.parse.urlencode(
                        {"user.fields": "created_at,description,profile_image_url,public_metrics,name"}
                    ),
                ),
                headers=headers,
            )
        except urllib.error.HTTPError as e2:
            return [], "x API HTTP %s (username lookup)" % e2.code, {}
    if not isinstance(user, dict):
        return [], "x API unexpected user body", {}
    ud = user.get("data") or {}
    uid = str(ud.get("id") or "")
    handle = str(ud.get("username") or ident)
    if not uid:
        return [], "x API: no user id for %s" % ident, {}
    metrics = ud.get("public_metrics") or {}
    profile = {
        "platform": "x",
        "handle": handle,
        "author_id": uid,
        "display_name": ud.get("name") or handle,
        "bio": ud.get("description") or "",
        "avatar_url": (ud.get("profile_image_url") or "").replace("_normal", ""),
        "followers": int(metrics.get("followers_count") or 0),
        "following": int(metrics.get("following_count") or 0),
    }
    token = None
    rows: list[dict] = []
    note = ""
    host = "https://api.x.com"
    hit_old = False
    while len(rows) < MAX_ROWS:
        q = {
            "max_results": "100",
            "tweet.fields": "created_at,lang,public_metrics,entities,referenced_tweets,source,text,in_reply_to_user_id",
            "expansions": "in_reply_to_user_id,referenced_tweets.id.author_id",
            "user.fields": "username,name",
        }
        if token:
            q["pagination_token"] = token
        elif since_id:
            q["since_id"] = since_id
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
        if not isinstance(data, dict):
            note = "x API unexpected body"
            break
        tweets = data.get("data") or []
        users_inc = {str(u.get("id")): u for u in ((data.get("includes") or {}).get("users") or [])}
        if not tweets:
            break
        for t in tweets:
            tid = str(t.get("id") or "")
            if not tid:
                continue
            if tid in known:
                hit_old = True
                break
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
            tmetrics = t.get("public_metrics") or {}
            mentions = [m.get("username") for m in ents.get("mentions") or []]
            reply_user = users_inc.get(str(t.get("in_reply_to_user_id") or ""))
            reply_handle = ""
            reply_name = ""
            if reply_user:
                reply_handle = str(reply_user.get("username") or "")
                reply_name = str(reply_user.get("name") or "")
            elif kind == "reply":
                reply_handle = _reply_handle(mentions)
            post = empty_post(
                id=tid,
                platform="x",
                author_id=uid,
                author_handle=handle,
                created_at=str(t.get("created_at") or ""),
                text=t.get("text") or "",
                lang=t.get("lang") or "",
                post_kind=kind,
                reply_to_id=reply_to,
                quote_of_id=quote_of,
                like_count=int(tmetrics.get("like_count") or 0),
                repost_count=int(tmetrics.get("retweet_count") or 0),
                reply_count=int(tmetrics.get("reply_count") or 0),
                views_count=int(tmetrics.get("impression_count") or 0),
                urls=[u.get("expanded_url") or u.get("url") for u in ents.get("urls") or []],
                mentions=mentions,
                hashtags=[h.get("tag") for h in ents.get("hashtags") or []],
                reply_to_handle=reply_handle,
                reply_to_name=reply_name,
                source="x-api-v2",
            )
            rows.append(_finish(post))
        if hit_old:
            note = note or ("x incremental, new=%s" % len(rows))
            break
        token = (data.get("meta") or {}).get("next_token")
        if not token:
            break
        time.sleep(0.3)
    return rows, note or ("x API pages until empty, n=%s" % len(rows)), profile


def _iso_time(created_at: str, ts) -> str:
    if ts not in (None, ""):
        try:
            return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OSError, OverflowError, ValueError, TypeError):
            pass
    if created_at:
        try:
            return parsedate_to_datetime(created_at).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError, OverflowError):
            return str(created_at)
    return ""


def _fx_status_to_post(item: dict, ident: str) -> dict | None:
    if not isinstance(item, dict) or item.get("type") not in (None, "status"):
        return None
    author = item.get("author") or {}
    handle = str(author.get("screen_name") or ident).lstrip("@")
    tid = str(item.get("id") or "")
    if not tid:
        return None
    reply = item.get("replying_to") if isinstance(item.get("replying_to"), dict) else {}
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    kind = "original"
    if item.get("reposted_by"):
        kind = "repost"
    elif reply.get("status") or reply.get("screen_name"):
        kind = "reply"
    elif quote.get("id"):
        kind = "quote"
    facets = (item.get("raw_text") or {}).get("facets") or []
    mentions: list[str] = []
    hashtags: list[str] = []
    urls: list[str] = []
    for f in facets:
        if not isinstance(f, dict):
            continue
        t = str(f.get("type") or "")
        if t == "mention":
            mentions.append(str(f.get("id") or f.get("original") or f.get("display") or "").lstrip("@"))
        elif t == "hashtag":
            hashtags.append(str(f.get("original") or f.get("display") or "").lstrip("#"))
        elif t == "url":
            u = f.get("replacement") or f.get("original") or ""
            if u:
                urls.append(str(u))
    text = item.get("text") or ""
    if not mentions:
        mentions = [m for m in MENTION_RE.findall(text) if m.lower() != handle.lower()]
    if not hashtags:
        hashtags = HASHTAG_RE.findall(text)
    media = []
    mobj = item.get("media") if isinstance(item.get("media"), dict) else {}
    for p in mobj.get("photos") or []:
        media.append({"type": p.get("type") or "photo", "url": p.get("url") or ""})
    for v in mobj.get("videos") or []:
        media.append({"type": v.get("type") or "video", "url": v.get("url") or v.get("thumbnail_url") or ""})
    reply_handle = str(reply.get("screen_name") or "")
    post = empty_post(
        id=tid,
        platform="x",
        author_id=str(author.get("id") or ""),
        author_handle=handle,
        created_at=_iso_time(str(item.get("created_at") or ""), item.get("created_timestamp")),
        text=text,
        lang=item.get("lang") or "",
        post_kind=kind,
        reply_to_id=str(reply.get("status") or "") or None,
        quote_of_id=str(quote.get("id") or "") or None,
        like_count=int(item.get("likes") or 0),
        repost_count=int(item.get("reposts") or 0),
        reply_count=int(item.get("replies") or 0),
        views_count=int(item.get("views") or 0),
        media=[x for x in media if x.get("url")],
        urls=urls,
        mentions=[m for m in mentions if m],
        hashtags=[h for h in hashtags if h],
        reply_to_handle=reply_handle,
        source="x-public-timeline",
    )
    return _finish(post)


def fetch_x_profile(ident: str) -> dict:
    ident = ident.lstrip("@")
    try:
        data = _get_json("https://api.fxtwitter.com/%s" % urllib.parse.quote(ident))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    user = data.get("user") if isinstance(data.get("user"), dict) else data
    handle = str(user.get("screen_name") or ident).lstrip("@")
    if not handle:
        return {}
    avatar = str(user.get("avatar_url") or user.get("avatar") or "")
    return {
        "platform": "x",
        "handle": handle,
        "author_id": str(user.get("id") or ""),
        "display_name": user.get("name") or handle,
        "bio": user.get("description") or "",
        "avatar_url": avatar,
        "followers": user.get("followers") or user.get("followers_count"),
        "following": user.get("following") or user.get("following_count"),
    }


def pull_x_public(
    ident: str,
    known: set[str] | None = None,
    on_page=None,
    should_stop=None,
) -> tuple[int, str, dict]:
    """Paginate the public profile timeline. Own-author posts only. Stop at ledger hits."""
    ident = ident.lstrip("@")
    known = set(known or [])
    seen: set[str] = set()
    profile = fetch_x_profile(ident)
    note = ""
    inserted_like = 0
    cursor = ""
    empty_own = 0
    pages = 0
    while inserted_like < MAX_ROWS:
        if should_stop and should_stop():
            note = "stopped by user after %s own posts this pull" % inserted_like
            break
        q = {"count": "100", "with_replies": "1"}
        if cursor:
            q["cursor"] = cursor
        url = "https://api.fxtwitter.com/2/profile/%s/statuses?%s" % (
            urllib.parse.quote(ident),
            urllib.parse.urlencode(q),
        )
        try:
            data = _get_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(4)
                continue
            note = "x public HTTP %s after %s own posts" % (e.code, inserted_like)
            break
        except urllib.error.URLError as e:
            note = "x public network %s" % e.reason
            break
        if not isinstance(data, dict):
            note = "x public unexpected body"
            break
        results = data.get("results") or []
        if not results:
            break
        pages += 1
        own_rows: list[dict] = []
        hit_old = False
        for item in results:
            author = (item.get("author") or {}) if isinstance(item, dict) else {}
            handle = str(author.get("screen_name") or "")
            if handle.lower() != ident.lower():
                continue
            tid = str(item.get("id") or "")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            if tid in known:
                hit_old = True
                continue
            post = _fx_status_to_post(item, ident)
            if not post:
                continue
            own_rows.append(post)
            if not profile.get("avatar_url") and author.get("avatar_url"):
                profile = {
                    "platform": "x",
                    "handle": handle,
                    "author_id": str(author.get("id") or ""),
                    "display_name": author.get("name") or handle,
                    "bio": author.get("description") or "",
                    "avatar_url": author.get("avatar_url") or "",
                    "followers": author.get("followers"),
                    "following": author.get("following"),
                }
        if own_rows:
            empty_own = 0
            inserted_like += len(own_rows)
            if on_page:
                on_page(own_rows, profile)
        else:
            empty_own += 1
        if hit_old:
            note = note or ("x public incremental, new own posts=%s pages=%s" % (inserted_like, pages))
            break
        if empty_own >= 3:
            note = note or ("x public no more own posts, n=%s pages=%s" % (inserted_like, pages))
            break
        nxt = ((data.get("cursor") or {}) if isinstance(data.get("cursor"), dict) else {}).get("bottom") or ""
        if not nxt or nxt == cursor:
            break
        cursor = nxt
        time.sleep(0.25)
    return inserted_like, note or ("x public pages until empty, own=%s pages=%s" % (inserted_like, pages)), profile


def fetch_x_avatar_url(ident: str) -> str:
    ident = ident.lstrip("@")
    try:
        data = _get_json("https://unavatar.io/twitter/%s?json" % urllib.parse.quote(ident))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict):
        return str(data.get("url") or "")
    return ""


def download_avatar(url: str) -> tuple[bytes, str]:
    if not url:
        return b"", ""
    try:
        raw, ctype = _get_bytes(url)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return b"", ""
    if not raw or raw[:1] in (b"{", b"<"):
        return b"", ""
    if "png" in ctype:
        ext = "png"
    elif "webp" in ctype:
        ext = "webp"
    elif "gif" in ctype:
        ext = "gif"
    else:
        ext = "jpg"
    return raw, ext


def pull_all(platform: str, ident: str, should_stop=None) -> dict:
    platform = (platform or "").lower().strip()
    ident = (ident or "").strip().lstrip("@")
    if not ident:
        return {"ok": False, "error": "need a handle or id", "count": 0}
    from store import insert_new, known_ids, newest_id, record_pull, upsert_profile

    cfg = _cfg()
    rows: list[dict] = []
    note = ""
    profile: dict = {}
    known: set[str] = set()
    since = ""
    inserted = 0
    skipped = 0
    already = False
    if platform in ("reddit",):
        known = known_ids("reddit", ident)
        rows, note = pull_reddit(ident, known=known)
        platform = "reddit"
    elif platform in ("bluesky", "bsky"):
        known = known_ids("bluesky", ident)
        rows, note, profile = pull_bluesky(ident, known=known)
        platform = "bluesky"
    elif platform in ("x", "twitter", "x.com"):
        platform = "x"
        bearer = str(cfg.get("x_bearer") or "").strip()
        known = known_ids("x", ident)
        if bearer:
            since = newest_id("x", ident)
            rows, note, profile = pull_x_api(ident, bearer, known=known, since_id=since)
        else:
            totals = {"ins": 0, "skip": 0, "id": "", "at": ""}

            def on_page(page_rows, prof):
                ins, sk = insert_new(page_rows)
                totals["ins"] += ins
                totals["skip"] += sk
                if page_rows:
                    totals["id"] = page_rows[0]["id"]
                    totals["at"] = page_rows[0].get("created_at") or ""
                if prof:
                    upsert_profile(prof)
                record_pull(
                    "x",
                    ident,
                    totals["ins"],
                    totals["skip"],
                    totals["id"],
                    totals["at"],
                    "running stored %s" % totals["ins"],
                )

            n_own, note, profile = pull_x_public(
                ident, known=known, on_page=on_page, should_stop=should_stop
            )
            already = True
            inserted, skipped = totals["ins"], totals["skip"]
            since = totals["id"]
            if profile:
                upsert_profile(profile)
            record_pull("x", ident, inserted, skipped, totals["id"], totals["at"], note)
            ok = True
            if note.startswith("x public HTTP") and inserted == 0:
                ok = False
            return {
                "ok": ok,
                "count": inserted,
                "inserted": inserted,
                "skipped": skipped,
                "id": ident,
                "platform": "x",
                "note": note,
                "error": None if ok else note,
            }
    else:
        return {
            "ok": False,
            "count": 0,
            "inserted": 0,
            "skipped": 0,
            "id": ident,
            "platform": platform,
            "error": "no public paginated API wired for this platform yet. ZIP ingest still works.",
        }

    if profile:
        if platform == "x" and not profile.get("avatar_url"):
            profile["avatar_url"] = fetch_x_avatar_url(ident) or (fetch_x_profile(ident).get("avatar_url") or "")
        upsert_profile(profile)
    elif platform == "x":
        prof = fetch_x_profile(ident)
        if prof:
            upsert_profile(prof)

    if not already:
        inserted, skipped = insert_new(rows)
    last_id = rows[0]["id"] if rows else since
    last_at = rows[0].get("created_at") or "" if rows else ""
    record_pull(platform, ident, inserted, skipped, str(last_id or ""), str(last_at or ""), note)
    ok = True
    if note.startswith(("reddit HTTP", "bluesky HTTP", "x API HTTP", "x public HTTP")) and inserted == 0:
        ok = False
    return {
        "ok": ok,
        "count": inserted,
        "inserted": inserted,
        "skipped": skipped,
        "id": ident,
        "platform": platform,
        "note": note,
        "error": None if ok else note,
    }
