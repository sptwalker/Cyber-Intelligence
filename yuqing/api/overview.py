# -*- coding: utf-8 -*-
"""Overview read model built entirely from existing domain calculations."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .. import analytics
from ..report import aggregate
from .responses import APIError

RANGES = {"7d": 7, "30d": 30, "90d": 90}
ACTIVE_INCIDENT_STATUSES = {"pending_confirmation", "confirmed", "escalated"}


def configured_entities(watch: dict) -> list[dict[str, str]]:
    """Serialize selectable entities without exposing raw configuration details."""
    result = []
    for entity in watch.get("entities") or []:
        entity_id = str(entity.get("id") or "").strip()
        if not entity_id:
            continue
        aliases = entity.get("aliases") or []
        result.append({
            "id": entity_id,
            "name": str(aliases[0] if aliases else entity_id),
            "type": str(entity.get("type", "self")),
        })
    return result


def resolve_entity(watch: dict, requested: str | None) -> tuple[str, str]:
    """Resolve an explicit entity or the first configured self entity."""
    entities = watch.get("entities") or []
    by_id = {str(entity.get("id")): entity for entity in entities if entity.get("id")}
    if requested:
        entity = by_id.get(requested)
        if entity is None:
            raise APIError("INVALID_ENTITY", "监控对象不存在")
    else:
        entity = next((item for item in entities if item.get("type", "self") == "self"), None)
        entity = entity or (entities[0] if entities else None)
        if entity is None:
            raise APIError("NO_ENTITY_CONFIGURED", "尚未配置监控对象", 409)
    entity_id = str(entity["id"])
    aliases = entity.get("aliases") or []
    return entity_id, str(aliases[0] if aliases else entity_id)


def filter_range(rows: list[dict], days: int) -> list[dict]:
    cutoff = _dt.date.fromisoformat(cutoff_day(days))
    selected = []
    for row in rows:
        try:
            day = _dt.date.fromisoformat(str(row.get("day") or ""))
        except ValueError:
            continue
        if day >= cutoff:
            selected.append(row)
    return selected


def cutoff_day(days: int, *, today: _dt.date | None = None) -> str:
    """Return the inclusive first day for a rolling range."""
    current = today or _dt.date.today()
    return (current - _dt.timedelta(days=days - 1)).isoformat()


def _active_incidents(store, entity_id: str) -> list[dict]:
    incidents = [
        item for item in store.list_incidents(limit=1000)
        if item.get("entity_id") == entity_id and item.get("status") in ACTIVE_INCIDENT_STATUSES
    ]
    level_order = {"P0": 0, "P1": 1}
    status_order = {"pending_confirmation": 0, "confirmed": 1, "escalated": 2}
    return sorted(
        incidents,
        key=lambda item: (
            level_order.get(item.get("level"), 99),
            status_order.get(item.get("status"), 99),
        ),
    )


def build_overview(store, watch: dict, *, entity_id: str | None = None,
                   range_name: str = "7d") -> tuple[dict[str, Any], str, list[str]]:
    """Build the overview response data plus explicit quality metadata."""
    if range_name not in RANGES:
        raise APIError("INVALID_PARAMETER", "参数 range 仅支持：7d、30d、90d")
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    since_day = cutoff_day(RANGES[range_name])

    metrics = aggregate(store, resolved_id, since_day=since_day)
    brand_health = analytics.brand_health(store, resolved_id, since_day=since_day)
    sentiment_trend = filter_range(analytics.daily_series(store, resolved_id), RANGES[range_name])
    bhi_trend = filter_range(analytics.bhi_trend(store, resolved_id), RANGES[range_name])
    incidents = _active_incidents(store, resolved_id)
    platforms = [str(item) for item in (watch.get("platforms") or [])]
    from .collection import latest_platform_runs
    collection_health, data_quality, quality_notes = latest_platform_runs(
        store, resolved_id, platforms,
    )

    latest_report = store.conn.execute(
        "SELECT run_id,created_at FROM reports ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    top_negative = metrics["top_neg"][0] if metrics["top_neg"] else None
    highest_risk = top_negative.get("risk") if top_negative else None

    data = {
        "entity": {"id": resolved_id, "name": entity_name},
        "entities": configured_entities(watch),
        "range": range_name,
        "metrics_scope": range_name,
        "metrics": {
            "total_volume": metrics["n_total"],
            "bhi": brand_health.get("bhi"),
            "bhi_label": brand_health.get("label"),
            "negative_count": metrics["n_neg"],
            "negative_ratio": round(metrics["neg_ratio"], 4),
            "highest_risk": highest_risk,
            "active_incident_count": len(incidents),
        },
        "sentiment_trend": [
            {
                "day": item["day"], "positive": item["pos"], "negative": item["neg"],
                "neutral": item["neu"], "total": item["total"],
                "mention": item["mention"], "risk": item["risk"],
            }
            for item in sentiment_trend
        ],
        "bhi_trend": bhi_trend,
        "top_incident": incidents[0] if incidents else None,
        "pending_review_count": store.pending_review_count(entity_id=resolved_id),
        "collection_health": collection_health,
        "latest_report": dict(latest_report) if latest_report else None,
    }
    return data, data_quality, quality_notes
