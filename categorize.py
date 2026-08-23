"""Rule-based first-pass categories. Ollama may refine later. Not Core."""

from __future__ import annotations


def categorize(post: dict) -> list[str]:
    tags: list[str] = []
    kind = str(post.get("post_kind") or "original")
    tags.append(kind)
    text = str(post.get("text") or "")
    if post.get("media"):
        tags.append("has_media")
    if post.get("urls"):
        tags.append("has_link")
    if post.get("mentions"):
        tags.append("has_mention")
    if "?" in text:
        tags.append("question")
    n = len(text.strip())
    if n and n <= 80:
        tags.append("short")
    elif n >= 280:
        tags.append("long")
    # unique, stable order
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
