"""Ephemeral profile-breakdown pages. Created on open, deleted on close."""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from secrets import token_urlsafe

from categorize import categorize, topics_for
from pull_public import download_avatar, fetch_x_avatar_url
from store import DATA, get_profile, query, upsert_profile

TMP = DATA / "tmp" / "summaries"
MAX_AGE_SEC = 2 * 60 * 60


def _esc(s) -> str:
    return escape(str(s or ""))


def cleanup_stale(now: float | None = None) -> None:
    if not TMP.is_dir():
        return
    now = now or time.time()
    for p in TMP.iterdir():
        if not p.is_dir():
            continue
        age = now - p.stat().st_mtime
        if age > MAX_AGE_SEC:
            shutil.rmtree(p, ignore_errors=True)


def close_session(token: str) -> bool:
    token = _safe_token(token)
    if not token:
        return False
    path = TMP / token
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return True
    return False


def _safe_token(token: str) -> str:
    t = (token or "").strip()
    if not t or any(c in t for c in r"/\."):
        return ""
    if not all(c.isalnum() or c in "-_" for c in t):
        return ""
    return t


def session_dir(token: str) -> Path | None:
    t = _safe_token(token)
    if not t:
        return None
    path = TMP / t
    return path if path.is_dir() else None


def _backfill_row(r: dict) -> dict:
    if not r.get("categories"):
        r["categories"] = categorize(r)
    if not r.get("topics"):
        r["topics"] = topics_for(r)
    if r.get("post_kind") == "reply" and not r.get("reply_to_handle"):
        mentions = r.get("mentions") or []
        if mentions:
            r["reply_to_handle"] = mentions[0]
        else:
            text = str(r.get("text") or "")
            if text.startswith("@"):
                r["reply_to_handle"] = text[1:].split()[0].rstrip(":,")
    return r


def _ollama_blurb(facts: str) -> str:
    if os.environ.get("POST_LEDGER_NO_OLLAMA"):
        return ""
    payload = json.dumps(
        {
            "model": "hermes:latest",
            "prompt": (
                "Write 4 short factual sentences summarizing this social-media account from the stats. "
                "No flattery. No advice. If a number is given, keep it.\n\n" + facts
            ),
            "stream": False,
            "options": {"num_predict": 180, "temperature": 0.2},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        return str(body.get("response") or "").strip()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return ""


def build_facts(handle: str, platform: str, rows: list[dict], profile: dict | None) -> dict:
    kinds = Counter(str(r.get("post_kind") or "original") for r in rows)
    topics = Counter()
    cats = Counter()
    replied = Counter()
    for r in rows:
        for t in r.get("topics") or []:
            topics[t] += 1
        for c in r.get("categories") or []:
            cats[c] += 1
        if str(r.get("post_kind") or "") == "reply":
            who = r.get("reply_to_handle") or r.get("reply_to_name") or r.get("reply_to_id") or "(unknown parent)"
            replied[str(who).lstrip("@")] += 1
    dates = [str(r.get("created_at") or "") for r in rows if r.get("created_at")]
    dates_sorted = sorted(dates)
    likes = sum(int(r.get("like_count") or 0) for r in rows)
    reposts = sum(int(r.get("repost_count") or 0) for r in rows)
    replies_got = sum(int(r.get("reply_count") or 0) for r in rows)
    views = sum(int(r.get("views_count") or 0) for r in rows)
    return {
        "handle": handle.lstrip("@"),
        "platform": platform or (rows[0].get("platform") if rows else ""),
        "display_name": (profile or {}).get("display_name") or handle.lstrip("@"),
        "bio": (profile or {}).get("bio") or "",
        "followers": (profile or {}).get("followers"),
        "n": len(rows),
        "first": dates_sorted[0] if dates_sorted else "",
        "last": dates_sorted[-1] if dates_sorted else "",
        "kinds": dict(kinds.most_common()),
        "topics": dict(topics.most_common()),
        "categories": dict(cats.most_common()),
        "replied_to": replied.most_common(25),
        "likes": likes,
        "reposts": reposts,
        "replies_got": replies_got,
        "views": views,
    }


def _template_blurb(f: dict) -> str:
    kinds = f["kinds"]
    replies = int(kinds.get("reply") or 0)
    originals = int(kinds.get("original") or 0)
    quotes = int(kinds.get("quote") or 0)
    top_topics = ", ".join("%s (%s)" % (k, v) for k, v in list(f["topics"].items())[:5]) or "uncategorized"
    top_people = ", ".join("@%s ×%s" % (k, v) for k, v in f["replied_to"][:5]) or "no resolved parent handles"
    return (
        "@%s on %s has %s stored posts from %s to %s. "
        "%s original, %s replies, %s quotes. "
        "Topics: %s. "
        "Reply targets: %s. "
        "Engagement on stored posts: %s likes, %s reposts, %s replies, %s views."
        % (
            f["handle"],
            f["platform"],
            f["n"],
            f["first"] or "?",
            f["last"] or "?",
            originals,
            replies,
            quotes,
            top_topics,
            top_people,
            f["likes"],
            f["reposts"],
            f["replies_got"],
            f["views"],
        )
    )


def _bars(items: list[tuple[str, int]], total: int) -> str:
    bits = []
    total = max(total, 1)
    for name, n in items:
        pct = int(round(100.0 * n / total))
        bits.append(
            '<div class="barrow"><span class="blab">%s</span>'
            '<span class="bar"><i style="width:%s%%"></i></span>'
            '<span class="bn">%s (%s%%)</span></div>'
            % (_esc(name), pct, n, pct)
        )
    return "".join(bits) or "<p class='meta'>None.</p>"


def render_html(token: str, f: dict, blurb: str, has_avatar: bool, handle: str, platform: str) -> str:
    avatar = (
        '<img class="av" src="/s/%s/avatar" alt="profile"/>' % token if has_avatar else '<div class="av ph">@</div>'
    )
    replied_rows = "".join(
        "<tr><td>@%s</td><td>%s</td></tr>" % (_esc(who), n) for who, n in f["replied_to"]
    ) or "<tr><td colspan='2'>No reply-parent handles stored. Drop an X archive ZIP or use x_bearer so reply-to users resolve.</td></tr>"
    bio = ("<p class='bio'>%s</p>" % _esc(f["bio"])) if f.get("bio") else ""
    followers = ""
    if f.get("followers") not in (None, ""):
        followers = "<p class='meta'>%s followers</p>" % _esc(f["followers"])
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Breakdown @%s</title>
<style>
body {{ font-family: Segoe UI, sans-serif; background:#111; color:#ddd; margin:0; padding:24px; }}
a, button.linkish {{ color:#8cf; }}
button {{ font-size:16px; padding:8px 14px; cursor:pointer; }}
.hero {{ display:flex; gap:20px; align-items:flex-start; margin-bottom:24px; }}
.av {{ width:96px; height:96px; border-radius:50%%; object-fit:cover; background:#222; }}
.av.ph {{ display:flex; align-items:center; justify-content:center; font-size:32px; color:#666; }}
.bio {{ max-width:40rem; color:#bbb; }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
@media (max-width:800px) {{ .grid {{ grid-template-columns:1fr; }} }}
.card {{ background:#1a1a1a; padding:16px; border-radius:8px; }}
.barrow {{ display:grid; grid-template-columns:8rem 1fr 6rem; gap:8px; align-items:center; margin:6px 0; }}
.bar {{ background:#333; height:10px; border-radius:6px; overflow:hidden; }}
.bar i {{ display:block; height:100%%; background:#6a8; }}
.bn, .meta {{ color:#888; font-size:13px; }}
table {{ border-collapse:collapse; width:100%%; }}
td, th {{ border-bottom:1px solid #333; padding:6px 8px; text-align:left; }}
.warn {{ color:#c94; }}
</style>
</head><body>
<div class="hero">
  {avatar}
  <div>
    <h1>%s</h1>
    <p>@%s · %s · %s posts</p>
    {bio}
    {followers}
    <p class="meta">%s → %s</p>
  </div>
</div>
<div class="card" style="margin-bottom:24px">
  <h2>Overall profile summary</h2>
  <p>%s</p>
</div>
<div class="grid">
  <div class="card">
    <h2>By kind</h2>
    %s
  </div>
  <div class="card">
    <h2>By topic</h2>
    %s
  </div>
  <div class="card">
    <h2>By category tag</h2>
    %s
  </div>
  <div class="card">
    <h2>In response to</h2>
    <table><tr><th>Who</th><th>Replies</th></tr>{replied_rows}</table>
  </div>
</div>
<p style="margin-top:24px">
  <button type="button" id="closebtn">Close breakdown</button>
  <a href="/?handle=%s&amp;platform=%s">Back to posts</a>
</p>
<p class="warn meta">This page is a temp file. Close deletes it.</p>
<script>
const TOKEN = %s;
function boom() {{
  try {{ navigator.sendBeacon("/s/" + TOKEN + "/close"); }} catch (e) {{}}
}}
document.getElementById("closebtn").onclick = function() {{
  boom();
  location.href = "/?handle=%s&platform=%s";
}};
window.addEventListener("pagehide", boom);
window.addEventListener("beforeunload", boom);
</script>
</body></html>""" % (
        _esc(f["handle"]),
        _esc(f["display_name"] or f["handle"]),
        _esc(f["handle"]),
        _esc(f["platform"]),
        f["n"],
        _esc(f["first"]),
        _esc(f["last"]),
        _esc(blurb),
        _bars(list(f["kinds"].items()), f["n"]),
        _bars(list(f["topics"].items()), f["n"]),
        _bars(list(f["categories"].items()), f["n"]),
        _esc(handle),
        _esc(platform),
        json.dumps(token),
        _esc(handle),
        _esc(platform),
    )


def create_session(handle: str, platform: str = "") -> dict:
    cleanup_stale()
    handle = (handle or "").lstrip("@")
    if not handle:
        return {"ok": False, "error": "need a handle"}
    rows = [_backfill_row(r) for r in query(handle=handle, platform=platform, limit=0)]
    if not rows:
        return {"ok": False, "error": "no stored posts for @%s" % handle}
    platform = platform or str(rows[0].get("platform") or "")
    profile = get_profile(platform, handle) or {}
    avatar_url = str(profile.get("avatar_url") or "")
    offline = bool(os.environ.get("POST_LEDGER_NO_OLLAMA"))
    if platform in ("x", "twitter") and not avatar_url and not offline:
        avatar_url = fetch_x_avatar_url(handle)
        if avatar_url:
            upsert_profile({"platform": "x", "handle": handle, "avatar_url": avatar_url})
            profile["avatar_url"] = avatar_url
    token = token_urlsafe(12)
    dest = TMP / token
    dest.mkdir(parents=True, exist_ok=True)
    has_avatar = False
    if avatar_url:
        raw, ext = download_avatar(avatar_url)
        if raw:
            (dest / ("avatar." + ext)).write_bytes(raw)
            (dest / "avatar").write_bytes(raw)
            has_avatar = True
    facts = build_facts(handle, platform, rows, profile)
    blurb = _ollama_blurb(
        "handle=@%s platform=%s posts=%s range=%s..%s kinds=%s topics=%s replied_to=%s"
        % (handle, platform, facts["n"], facts["first"], facts["last"], facts["kinds"], facts["topics"], facts["replied_to"][:8])
    ) or _template_blurb(facts)
    html = render_html(token, facts, blurb, has_avatar, handle, platform)
    (dest / "index.html").write_text(html, encoding="utf-8")
    meta = {
        "handle": handle,
        "platform": platform,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "has_avatar": has_avatar,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"ok": True, "token": token, "n": len(rows)}
