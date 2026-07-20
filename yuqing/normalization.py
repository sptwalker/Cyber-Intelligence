# -*- coding: utf-8 -*-
"""Normalize collector payloads into the stable clean-document contract."""

from __future__ import annotations

import hashlib

from .store import CleanDoc


_COMPLAINT_TRIGGERS = ["投诉", "维权", "退款", "退货", "赔偿", "曝光", "避雷", "翻车", "召回", "欺诈"]
_GENERIC_SEGMENTS = {"", "link", "index", "s", "detail", "view", "article", "search"}


def _pick(data: dict, *keys, default=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_int(value) -> int:
    """Convert platform counters such as ``1.2万`` and ``10w+`` to integers."""
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "0").strip().lower().replace("+", "").replace(",", "")
    try:
        if text.endswith("万") or text.endswith("w"):
            return int(float(text[:-1]) * 10000)
        return int(float(text))
    except ValueError:
        return 0


def _derive_id(item: dict) -> str:
    """Derive a stable platform-native identifier when a payload omits one."""
    native_id = _pick(item, "id", "note_id", "mid", "aweme_id", "rid", "tid", default="")
    if native_id:
        return str(native_id)
    url = _pick(item, "url", "link", "note_url", default="")
    segment = url.split("?")[0].rstrip("/").split("/")[-1] if url else ""
    if segment and segment.lower() not in _GENERIC_SEGMENTS:
        return segment
    basis = _pick(item, "title", "desc", "text", default="") + "|" + _pick(
        item, "publish_time", "time", "date", "published_at", default="",
    )
    if basis.strip("|"):
        return hashlib.md5(basis.encode("utf-8")).hexdigest()[:12]
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:12] if url else ""


def normalize(
    platform: str, entity_id: str, item: dict, backend: str, fetched_at: str,
) -> CleanDoc:
    """Convert a platform payload into the clean-layer document contract."""
    text = _pick(
        item, "text", "content", "desc", "snippet", "summary", "title", default="",
    )
    user = item.get("user") or item.get("author") or {}
    if isinstance(user, str):
        user = {"nickname": user}
    is_complaint = platform == "heimao" or any(trigger in text for trigger in _COMPLAINT_TRIGGERS)
    return CleanDoc.build(
        platform=platform,
        entity_id=entity_id,
        native_id=_derive_id(item),
        text=text,
        author=_pick(user, "nickname", "nick_name", "name", default=""),
        author_followers=_to_int(_pick(user, "followers", "fans", "fans_count", default=0)),
        likes=_to_int(_pick(
            item, "like_count", "liked_count", "digg_count", "votes", "likes",
            "lights", "zhi_count", default=0,
        )),
        comments=_to_int(_pick(
            item, "comment_count", "comments", "comment", "replies", default=0,
        )),
        reposts=_to_int(_pick(
            item, "repost_count", "share_count", "forward_count", "shares", default=0,
        )),
        plays=_to_int(_pick(item, "plays", "score", "play_count", "views", default=0)),
        publish_ts=str(_pick(
            item, "created_at", "time", "publish_time", "date", "published_at",
            "updated_at", default="",
        )),
        url=_pick(item, "url", "link", "note_url", default=""),
        tags=item.get("tags") or item.get("tag_list") or [],
        is_complaint=is_complaint,
        backend=backend,
        fetched_at=fetched_at,
    )
