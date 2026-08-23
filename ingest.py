"""Ingest IDs and official export ZIPs into the ledger."""

from __future__ import annotations

import json
from pathlib import Path

from store import upsert_many
from x_archive import parse_folder, parse_zip

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "inbox"
CONFIG = ROOT / "config.local.json"


def _handle_from_config() -> str:
    if not CONFIG.is_file():
        return ""
    try:
        d = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(d.get("x_handle") or d.get("handle") or "")


def ingest_path(path: Path, handle: str = "") -> dict:
    handle = (handle or _handle_from_config()).lstrip("@")
    path = Path(path)
    if not path.exists():
        return {"ok": False, "error": "path not found", "count": 0}
    if path.suffix.lower() == ".zip":
        rows = parse_zip(path, handle=handle)
    elif path.is_dir():
        rows = parse_folder(path, handle=handle)
    else:
        return {"ok": False, "error": "drop a ZIP or an unpacked archive folder", "count": 0}
    n = upsert_many(rows)
    return {"ok": True, "count": n, "handle": handle, "source": str(path)}


def ingest_inbox(handle: str = "") -> list[dict]:
    INBOX.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(INBOX.iterdir()):
        if p.suffix.lower() == ".zip" or p.is_dir():
            out.append(ingest_path(p, handle=handle))
    return out


def live_lookup(platform: str, ident: str) -> dict:
    """Paginate public/official APIs until they stop. No Hitch-invented 200 cap."""
    from pull_public import pull_all

    return pull_all(platform, ident)
