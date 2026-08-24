"""Rule-based first-pass categories and topics. Ollama may refine later. Not Core."""

from __future__ import annotations

import re

TOPIC_RULES: list[tuple[str, re.Pattern]] = [
    ("ai", re.compile(r"\b(ai|a\.i\.|llm|grok|chatgpt|openai|claude|gemini|ollama|gpt-?\d*|machine learning)\b", re.I)),
    ("games", re.compile(r"\b(game|gaming|godot|steam|playtest|video game|copycats?)\b", re.I)),
    ("politics", re.compile(r"\b(trump|biden|government|congress|election|president|law enforcement|america[ns]?|globalism|traitors?|leaders?)\b", re.I)),
    ("finance", re.compile(r"\b(market|polymarket|accounting|offshore|stock|money|hr\b|cheap)\b", re.I)),
    ("media", re.compile(r"\b(movie|song|news|media|film|tv)\b", re.I)),
    ("culture", re.compile(r"\b(holiday|culture|science|religion)\b", re.I)),
    ("tech", re.compile(r"\b(software|code|dev(?:elop(?:ment|ers?)?)?|memory|subscription|protocol|atproto)\b", re.I)),
]


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
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def topics_for(post: dict) -> list[str]:
    text = str(post.get("text") or "")
    blob = text + " " + " ".join(str(h) for h in (post.get("hashtags") or []))
    found: list[str] = []
    seen = set()
    for name, pat in TOPIC_RULES:
        if pat.search(blob) and name not in seen:
            seen.add(name)
            found.append(name)
    if not found and text.strip():
        found.append("other")
    return found
