"""Background pulls so the UI does not sit on one HTTP request."""

from __future__ import annotations

import threading
import traceback

_lock = threading.Lock()
_running: dict[tuple[str, str], bool] = {}
_stop: set[tuple[str, str]] = set()


def _key(platform: str, ident: str) -> tuple[str, str]:
    return ((platform or "").lower(), (ident or "").lstrip("@").lower())


def is_running(platform: str, ident: str) -> bool:
    return bool(_running.get(_key(platform, ident)))


def should_stop(platform: str, ident: str) -> bool:
    return _key(platform, ident) in _stop


def request_stop(platform: str, ident: str) -> None:
    _stop.add(_key(platform, ident))


def start_pull(platform: str, ident: str) -> dict:
    ident = (ident or "").strip().lstrip("@")
    platform = (platform or "").lower().strip()
    if not ident:
        return {"ok": False, "error": "need a handle or id", "id": ident, "platform": platform}
    key = _key(platform, ident)
    with _lock:
        if _running.get(key):
            return {
                "ok": True,
                "running": True,
                "started": False,
                "id": ident,
                "platform": platform,
                "note": "already pulling",
            }
        _running[key] = True
        _stop.discard(key)

    from store import record_pull

    record_pull(platform, ident, 0, 0, "", "", "running")

    def run():
        try:
            from pull_public import pull_all

            pull_all(platform, ident, should_stop=lambda: should_stop(platform, ident))
        except Exception:
            traceback.print_exc()
            try:
                record_pull(platform, ident, 0, 0, "", "", "pull failed")
            except Exception:
                pass
        finally:
            with _lock:
                _running.pop(key, None)
                _stop.discard(key)

    threading.Thread(target=run, daemon=True, name="pull-%s-%s" % (platform, ident)).start()
    return {
        "ok": True,
        "running": True,
        "started": True,
        "id": ident,
        "platform": platform,
        "note": "pull started",
    }
