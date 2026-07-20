# -*- coding: utf-8 -*-
"""Optional semantic-relevance helpers for the collection pipeline."""

from __future__ import annotations


DEFAULT_THRESHOLD = 0.55


def setup(require: bool, aliases, *, default_threshold: float = DEFAULT_THRESHOLD):
    """Return ``(enabled, threshold, entity_vector)`` with fail-open fallback."""
    from .. import config, embed

    if not (
        require
        and aliases
        and config.resolve("SEMANTIC_RELEVANCE") in ("1", "true", "True")
    ):
        return False, 0.0, None
    if not embed.available():
        return False, 0.0, None
    try:
        threshold = float(config.resolve("SEMANTIC_THRESHOLD") or default_threshold)
        vector = embed.embed_one("、".join(aliases[:3]))
        return bool(vector), threshold, vector
    except Exception:
        return False, 0.0, None


def similarity(enabled: bool, entity_vector, text: str):
    """Return one semantic similarity, or ``None`` when disabled/unavailable."""
    if not (enabled and entity_vector and text):
        return None
    from .. import embed

    try:
        vector = embed.embed_one(text)
        return embed.cosine(entity_vector, vector) if vector else None
    except Exception:
        return None
