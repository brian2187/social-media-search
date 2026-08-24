"""Canonical post row. One shape for every platform."""

from __future__ import annotations

FIELDS = (
    "id",
    "platform",
    "author_id",
    "author_handle",
    "created_at",
    "text",
    "lang",
    "post_kind",
    "reply_to_id",
    "quote_of_id",
    "like_count",
    "repost_count",
    "reply_count",
    "views_count",
    "media",
    "urls",
    "mentions",
    "hashtags",
    "source",
    "categories",
    "topics",
    "reply_to_handle",
    "reply_to_name",
)


def empty_post(**kwargs) -> dict:
    row = {k: None for k in FIELDS}
    row["media"] = []
    row["urls"] = []
    row["mentions"] = []
    row["hashtags"] = []
    row["categories"] = []
    row["topics"] = []
    row["like_count"] = 0
    row["repost_count"] = 0
    row["reply_count"] = 0
    row["views_count"] = 0
    row["post_kind"] = "original"
    row["platform"] = ""
    row["text"] = ""
    row["reply_to_handle"] = ""
    row["reply_to_name"] = ""
    row.update(kwargs)
    return row
