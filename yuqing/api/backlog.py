# -*- coding: utf-8 -*-
"""Read-only product-demand backlog derived from analyzed source documents."""

from __future__ import annotations

from typing import Any

from .. import insights
from .collection import latest_platform_runs
from .overview import RANGES, cutoff_day, resolve_entity
from .responses import APIError


def build_backlog(store, watch: dict, *, entity_id: str | None = None,
                  range_name: str = "30d") -> tuple[dict[str, Any], str, list[str]]:
    if range_name not in RANGES:
        raise APIError("INVALID_PARAMETER", "参数 range 仅支持：7d、30d、90d")
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    items = insights.backlog(
        store, {resolved_id}, since_day=cutoff_day(RANGES[range_name]),
    )
    platforms = [str(item) for item in (watch.get("platforms") or [])]
    _, quality, notes = latest_platform_runs(store, resolved_id, platforms)
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "range": range_name,
        "metrics_scope": range_name,
        "items": items,
        "count": len(items),
    }, quality, notes


def backlog_csv(
    store, watch: dict, *, entity_id: str | None = None, range_name: str = "30d",
) -> tuple[str, str]:
    if range_name not in RANGES:
        raise APIError("INVALID_PARAMETER", "参数 range 仅支持：7d、30d、90d")
    resolved_id, _ = resolve_entity(watch, entity_id)
    items = insights.backlog(
        store, {resolved_id}, since_day=cutoff_day(RANGES[range_name]),
    )
    return insights.backlog_csv(items), resolved_id
