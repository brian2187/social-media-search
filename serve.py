#!/usr/bin/env python3
"""Post Ledger local UI. http://127.0.0.1:8768/"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ingest import ingest_inbox, ingest_path
from jobs import is_running, request_stop, start_pull
from store import accounts, count_posts, last_ident, last_pull, query, stats
from summary import cleanup_stale, close_session, create_session, session_dir

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8768
PAGE_SIZE = 50


def page(body: str, title: str = "Post Ledger", extra_head: str = "") -> bytes:
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
{extra_head}
<title>{title}</title>
<style>
body {{ font-family: Segoe UI, sans-serif; background:#111; color:#ddd; margin:0; padding:24px; }}
a {{ color:#8cf; }}
input, select, button {{ font-size:16px; padding:8px; margin:4px 0; }}
button {{ cursor:pointer; }}
table {{ border-collapse: collapse; width:100%; margin-top:16px; }}
th, td {{ border-bottom:1px solid #333; padding:8px; text-align:left; vertical-align:top; }}
.tag {{ display:inline-block; background:#243; color:#9c8; padding:2px 6px; margin:1px; font-size:12px; }}
.topic {{ background:#224; color:#9bd; }}
.warn {{ color:#c94; max-width:52rem; }}
.meta {{ color:#888; font-size:13px; }}
.cards {{ display:flex; flex-wrap:wrap; gap:12px; margin:16px 0; }}
.card {{ background:#1a1a1a; padding:14px 16px; border-radius:8px; min-width:16rem; }}
.rowform {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
.pager {{ display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:12px 0; }}
.pager button:disabled {{ opacity:0.4; cursor:not-allowed; }}
</style></head><body>
<h1><a href="/" style="color:inherit;text-decoration:none">Post Ledger</a></h1>
<p class="warn">Each pull is stored in the local database. The next pull for the same id only inserts posts that are not already there.
Home lists accounts you pulled — it does not mix other people's feeds into your view.</p>
<p class="meta">Local {HOST}:{PORT}. Inbox: {ROOT / "inbox"}</p>
{body}
</body></html>"""
    return html.encode("utf-8")


def _esc(s) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _prefill() -> str:
    return last_ident() or "happiestocamper"


def _pager(handle: str, platform: str, category: str, pg: int, pages: int, total: int) -> str:
    if total <= 0:
        return ""
    start = (pg - 1) * PAGE_SIZE + 1
    end = min(total, pg * PAGE_SIZE)
    prev_p = max(1, pg - 1)
    next_p = min(pages, pg + 1)

    def form(target: int, label: str, disabled: bool) -> str:
        return (
            '<form method="get" action="/" class="rowform" style="display:inline">'
            '<input type="hidden" name="handle" value="%s"/>'
            '<input type="hidden" name="platform" value="%s"/>'
            '<input type="hidden" name="category" value="%s"/>'
            '<input type="hidden" name="page" value="%s"/>'
            '<button type="submit"%s>%s</button></form>'
            % (
                _esc(handle),
                _esc(platform),
                _esc(category),
                target,
                " disabled" if disabled else "",
                _esc(label),
            )
        )

    return (
        '<div class="pager">'
        "%s"
        "<span>Showing <b>%s–%s</b> of %s · page %s / %s</span>"
        "%s"
        "</div>"
        % (
            form(prev_p, "Prev %s" % PAGE_SIZE, pg <= 1),
            start,
            end,
            total,
            pg,
            pages,
            form(next_p, "Next %s" % PAGE_SIZE, pg >= pages),
        )
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, loc: str):
        self.send_response(303)
        self.send_header("Location", loc)
        self.end_headers()

    def do_GET(self):
        cleanup_stale()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        parts = [p for p in u.path.split("/") if p]

        if u.path == "/health":
            self._send(200, b"ok", "text/plain")
            return

        if parts[:1] == ["s"] and len(parts) >= 2:
            token = parts[1]
            action = parts[2] if len(parts) > 2 else ""
            dest = session_dir(token)
            if not dest:
                self._send(404, page("<p>Breakdown closed or expired.</p><p><a href='/'>Home</a></p>"))
                return
            if action in ("", "index.html"):
                html = (dest / "index.html").read_bytes()
                self._send(200, html)
                return
            if action == "avatar":
                blob = None
                for name in ("avatar", "avatar.jpg", "avatar.png", "avatar.webp"):
                    p = dest / name
                    if p.is_file():
                        blob = p.read_bytes()
                        break
                if not blob:
                    self._send(404, b"no avatar", "text/plain")
                    return
                ctype = "image/jpeg"
                if blob[:8] == b"\x89PNG\r\n\x1a\n":
                    ctype = "image/png"
                elif blob[:4] == b"RIFF":
                    ctype = "image/webp"
                self._send(200, blob, ctype)
                return
            self._send(404, page("<p>Not found.</p>"))
            return

        if u.path not in ("/", "/index.html"):
            self._send(404, page("<p>Not found.</p>"))
            return

        handle = (q.get("handle") or [""])[0].lstrip("@")
        platform = (q.get("platform") or [""])[0]
        category = (q.get("category") or [""])[0]
        msg = (q.get("msg") or [""])[0]
        try:
            pg = max(1, int((q.get("page") or ["1"])[0]))
        except ValueError:
            pg = 1

        ident_val = handle or _prefill()
        form = f"""
<form method="post" action="/lookup" class="rowform">
  <label>Platform
    <select name="platform">
      <option value="x" {"selected" if platform in ("", "x", "twitter") else ""}>X / x.com</option>
      <option value="reddit" {"selected" if platform == "reddit" else ""}>Reddit</option>
      <option value="bluesky" {"selected" if platform == "bluesky" else ""}>Bluesky</option>
      <option value="youtube">YouTube</option>
      <option value="instagram">Instagram</option>
      <option value="tiktok">TikTok</option>
    </select>
  </label>
  <label>Handle or id <input name="ident" value="{_esc(ident_val)}"/></label>
  <button type="submit">Pull new posts</button>
</form>
<form method="post" action="/inbox"><button type="submit">Ingest inbox ZIPs</button></form>
"""
        bits = [form]
        pulling = bool(handle) and is_running(platform or "x", handle)
        extra_head = '<meta http-equiv="refresh" content="4"/>' if pulling and pg <= 1 else ""
        if pulling:
            bits.append(
                "<p class='warn'>Pulling @%s — %s stored so far. This page refreshes until the pull stops.</p>"
                % (_esc(handle), stats(handle=handle, platform=platform).get("total", 0))
            )
            bits.append(
                "<form method='post' action='/cancel'><input type='hidden' name='handle' value='%s'/>"
                "<input type='hidden' name='platform' value='%s'/>"
                "<button type='submit'>Stop pull</button></form>" % (_esc(handle), _esc(platform or "x"))
            )
        if msg:
            bits.append("<p class='warn'>%s</p>" % _esc(msg))

        if not handle:
            accts = accounts()
            st = stats()
            bits.append(
                "<p>Stored posts: <b>%s</b> across <b>%s</b> account(s). Open one to browse — other accounts stay out of that view.</p>"
                % (st.get("total", 0), len(accts))
            )
            bits.append('<div class="cards">')
            if not accts:
                bits.append("<p class='meta'>Nothing stored yet. Pull a handle or drop an X archive ZIP.</p>")
            for a in accts:
                bits.append(
                    "<div class='card'><b>@%s</b> · %s<br/><span class='meta'>%s posts · %s → %s</span><br/>"
                    "<a href='/?handle=%s&amp;platform=%s'>Open</a> · "
                    "<form method='post' action='/breakdown' style='display:inline'>"
                    "<input type='hidden' name='handle' value='%s'/>"
                    "<input type='hidden' name='platform' value='%s'/>"
                    "<button type='submit'>Detailed breakdown</button></form></div>"
                    % (
                        _esc(a["handle"]),
                        _esc(a["platform"]),
                        a["count"],
                        _esc(a["first_at"]),
                        _esc(a["last_at"]),
                        quote(a["handle"] or ""),
                        quote(a["platform"] or ""),
                        _esc(a["handle"]),
                        _esc(a["platform"]),
                    )
                )
            bits.append("</div>")
            self._send(200, page("".join(bits), extra_head=extra_head))
            return

        st = stats(handle=handle, platform=platform)
        total = count_posts(handle=handle, platform=platform, category=category)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
        if pg > pages:
            pg = pages
        offset = (pg - 1) * PAGE_SIZE
        rows = query(handle=handle, platform=platform, category=category, limit=PAGE_SIZE, offset=offset)
        bits.append(
            """
<form method="get" action="/" class="rowform">
  <input name="handle" value="%s"/>
  <input name="platform" placeholder="platform" value="%s"/>
  <input name="category" placeholder="category or topic" value="%s"/>
  <button type="submit">Filter</button>
</form>
<form method="post" action="/breakdown" class="rowform">
  <input type="hidden" name="handle" value="%s"/>
  <input type="hidden" name="platform" value="%s"/>
  <button type="submit">Detailed breakdown</button>
</form>
<p>@%s%s: <b>%s</b> stored posts (this account only).</p>
%s
<table>
<tr><th>When</th><th>Who</th><th>Kind</th><th>In reply to</th><th>Text</th><th>Tags</th></tr>
"""
            % (
                _esc(handle),
                _esc(platform),
                _esc(category),
                _esc(handle),
                _esc(platform),
                _esc(handle),
                (" · " + _esc(platform)) if platform else "",
                total,
                _pager(handle, platform, category, pg, pages, total),
            )
        )
        for r in rows:
            tags = "".join(f'<span class="tag">{_esc(t)}</span>' for t in (r.get("categories") or []))
            tags += "".join(f'<span class="tag topic">{_esc(t)}</span>' for t in (r.get("topics") or []))
            reply_who = r.get("reply_to_handle") or r.get("reply_to_name") or ""
            if not reply_who and r.get("reply_to_id"):
                reply_who = r.get("reply_to_id")
            bits.append(
                "<tr><td>%s</td><td>@%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    _esc(r.get("created_at")),
                    _esc(r.get("author_handle")),
                    _esc(r.get("post_kind")),
                    ("@" + _esc(reply_who)) if reply_who else "",
                    _esc((r.get("text") or "")[:280]),
                    tags,
                )
            )
        bits.append("</table>")
        bits.append(_pager(handle, platform, category, pg, pages, total))
        lp = last_pull(platform or "x", handle)
        if lp and lp.get("note") and not pulling:
            bits.append("<p class='meta'>Last pull: %s — %s new, %s skipped. %s</p>" % (
                _esc(lp.get("pulled_at")), lp.get("last_inserted"), lp.get("last_skipped"), _esc(lp.get("note"))
            ))
        self._send(200, page("".join(bits), extra_head=extra_head))

    def do_POST(self):
        cleanup_stale()
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        form = parse_qs(raw)

        if parts[:1] == ["s"] and len(parts) >= 3 and parts[2] == "close":
            close_session(parts[1])
            self._send(204, b"", "text/plain")
            return

        if u.path == "/lookup":
            platform = (form.get("platform") or ["x"])[0]
            ident = (form.get("ident") or [""])[0].strip()
            if not ident:
                self._redirect("/?msg=" + quote("need a handle or id"))
                return
            info = start_pull(platform, ident)
            if info.get("running"):
                msg = "pull started — page will refresh as posts land. Next pull only inserts new ids."
            elif info.get("ok"):
                msg = info.get("note") or "done"
            else:
                msg = info.get("error") or json.dumps(info)
            loc = "/?handle=%s&platform=%s&msg=%s" % (
                quote(ident.lstrip("@")),
                quote(info.get("platform") or platform),
                quote(str(msg)[:300]),
            )
            self._redirect(loc)
            return

        if u.path == "/cancel":
            handle = (form.get("handle") or [""])[0]
            platform = (form.get("platform") or ["x"])[0]
            request_stop(platform, handle)
            self._redirect(
                "/?handle=%s&platform=%s&msg=%s"
                % (quote(handle.lstrip("@")), quote(platform), quote("stop requested"))
            )
            return

        if u.path == "/breakdown":
            handle = (form.get("handle") or [""])[0]
            platform = (form.get("platform") or [""])[0]
            info = create_session(handle, platform)
            if not info.get("ok"):
                self._redirect("/?handle=%s&msg=%s" % (quote(handle), quote(info.get("error") or "breakdown failed")))
                return
            self._redirect("/s/" + info["token"])
            return

        if u.path == "/inbox":
            results = ingest_inbox()
            n = sum(int(r.get("count") or 0) for r in results)
            msg = "ingested %s posts from %s file(s)" % (n, len(results)) if results else "inbox empty — drop an X archive ZIP into inbox\\"
            handle = ""
            if results and results[0].get("handle"):
                handle = results[0]["handle"]
            loc = "/?msg=" + quote(msg)
            if handle:
                loc = "/?handle=%s&msg=%s" % (quote(handle), quote(msg))
            self._redirect(loc)
            return

        if u.path == "/ingest":
            p = (form.get("path") or [""])[0]
            handle = (form.get("handle") or [""])[0]
            info = ingest_path(Path(p), handle=handle)
            self._send(200, json.dumps(info).encode("utf-8"), "application/json")
            return
        self._send(404, page("<p>Not found.</p>"))


def main():
    (ROOT / "inbox").mkdir(parents=True, exist_ok=True)
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    cleanup_stale()
    from store import backfill_derived

    backfill_derived()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Post Ledger http://%s:%s/  inbox=%s" % (HOST, PORT, ROOT / "inbox"))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
