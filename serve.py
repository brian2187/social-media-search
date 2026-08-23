#!/usr/bin/env python3
"""Post Ledger local UI. http://127.0.0.1:8768/"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ingest import ingest_inbox, ingest_path, live_lookup
from store import query, stats

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8768


def page(body: str, title: str = "Post Ledger") -> bytes:
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
body {{ font-family: Segoe UI, sans-serif; background:#111; color:#ddd; margin:0; padding:24px; }}
a {{ color:#8cf; }}
input, select, button {{ font-size:16px; padding:8px; margin:4px 0; }}
button {{ cursor:pointer; }}
table {{ border-collapse: collapse; width:100%; margin-top:16px; }}
th, td {{ border-bottom:1px solid #333; padding:8px; text-align:left; vertical-align:top; }}
.tag {{ display:inline-block; background:#243; color:#9c8; padding:2px 6px; margin:1px; font-size:12px; }}
.warn {{ color:#c94; max-width:52rem; }}
.meta {{ color:#888; font-size:13px; }}
</style></head><body>
<h1>Post Ledger</h1>
<p class="warn">We paginate public official APIs until the platform returns nothing.
No Hitch 200-post cap. If X refuses unauthenticated bulk, that is X, not this app.
ZIP ingest is still the complete attic for an account you own.</p>
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/health":
            self._send(200, b"ok", "text/plain")
            return
        if u.path not in ("/", "/index.html"):
            self._send(404, page("<p>Not found.</p>"))
            return
        handle = (q.get("handle") or [""])[0]
        platform = (q.get("platform") or [""])[0]
        category = (q.get("category") or [""])[0]
        msg = (q.get("msg") or [""])[0]
        st = stats()
        rows = query(handle=handle, platform=platform, category=category, limit=0)
        form = f"""
<form method="post" action="/lookup">
  <label>Platform
    <select name="platform">
      <option value="x">X / x.com</option>
      <option value="reddit">Reddit</option>
      <option value="bluesky">Bluesky</option>
      <option value="youtube">YouTube</option>
      <option value="instagram">Instagram</option>
      <option value="tiktok">TikTok</option>
    </select>
  </label>
  <label>Handle or id <input name="ident" placeholder="happiestocamper" value="{_esc(handle)}"/></label>
  <button type="submit">Pull all public pages</button>
</form>
<form method="post" action="/inbox">
  <button type="submit">Ingest inbox ZIPs</button>
</form>
<form method="get" action="/">
  <input name="handle" placeholder="filter handle" value="{_esc(handle)}"/>
  <input name="category" placeholder="category" value="{_esc(category)}"/>
  <button type="submit">Filter</button>
</form>
<p>Stored posts: <b>{st.get("total", 0)}</b> { _esc(st.get("by_platform")) }</p>
<p class="warn">{_esc(msg)}</p>
<table>
<tr><th>When</th><th>Who</th><th>Kind</th><th>Text</th><th>Tags</th></tr>
"""
        bits = [form]
        for r in rows:
            tags = "".join(f'<span class="tag">{_esc(t)}</span>' for t in (r.get("categories") or []))
            bits.append(
                "<tr><td>%s</td><td>@%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    _esc(r.get("created_at")),
                    _esc(r.get("author_handle")),
                    _esc(r.get("post_kind")),
                    _esc((r.get("text") or "")[:280]),
                    tags,
                )
            )
        bits.append("</table>")
        self._send(200, page("".join(bits)))

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        form = parse_qs(raw)
        if u.path == "/lookup":
            platform = (form.get("platform") or ["x"])[0]
            ident = (form.get("ident") or [""])[0]
            info = live_lookup(platform, ident)
            if info.get("ok"):
                msg = "pulled %s posts. %s" % (info.get("count"), info.get("note") or "")
            else:
                msg = info.get("error") or json.dumps(info)
            loc = "/?handle=%s&msg=%s" % (quote(ident), quote(msg[:300]))
            self.send_response(303)
            self.send_header("Location", loc)
            self.end_headers()
            return
        if u.path == "/inbox":
            results = ingest_inbox()
            n = sum(int(r.get("count") or 0) for r in results)
            msg = "ingested %s posts from %s file(s)" % (n, len(results)) if results else "inbox empty — drop an X archive ZIP into inbox\\"
            self.send_response(303)
            self.send_header("Location", "/?msg=" + quote(msg))
            self.end_headers()
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
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Post Ledger http://%s:%s/  inbox=%s" % (HOST, PORT, ROOT / "inbox"))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
