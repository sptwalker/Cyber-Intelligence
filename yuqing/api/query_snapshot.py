# -*- coding: utf-8 -*-
"""Request-local snapshots for repeated read-model queries.

API read models often reuse the same ``clean JOIN features`` rows across
several domain calculations.  This proxy keeps those rows stable for one
builder invocation while delegating every other operation to the real store.
It deliberately has no process-wide state, TTL, or invalidation policy.
"""

from __future__ import annotations

from typing import Any


class RequestQuerySnapshot:
    """Delegate to a store and memoize explicitly safe, read-only joins."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._joined: dict[str | None, tuple[Any, ...]] = {}
        self._joined_with_entities: tuple[Any, ...] | None = None

    def joined(self, entity_id: str | None = None) -> list[Any]:
        if entity_id not in self._joined:
            self._joined[entity_id] = tuple(self._store.joined(entity_id))
        # Match Store.joined's list contract and keep callers from mutating the
        # cached container. sqlite3.Row instances themselves are immutable.
        return list(self._joined[entity_id])

    def joined_with_entities(self) -> list[Any]:
        if self._joined_with_entities is None:
            self._joined_with_entities = tuple(self._store.joined_with_entities())
        return list(self._joined_with_entities)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


def request_query_snapshot(store: Any) -> RequestQuerySnapshot:
    """Create a fresh query snapshot for one API read-model request."""
    return RequestQuerySnapshot(store)
