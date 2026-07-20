# -*- coding: utf-8 -*-
"""Incident list/detail serialization and server-authoritative actions."""

from __future__ import annotations

from typing import Any

from .collection import latest_platform_runs
from .entities import resolve_entity
from .responses import APIError

TRANSITIONS = {
    "pending_confirmation": [
        {"action": "confirm", "target": "confirmed", "label": "确认事件"},
        {"action": "suppress", "target": "suppressed", "label": "抑制误报"},
    ],
    "confirmed": [
        {"action": "escalate", "target": "escalated", "label": "升级高层"},
        {"action": "resolve", "target": "resolved", "label": "标记解决"},
    ],
    "escalated": [
        {"action": "resolve", "target": "resolved", "label": "标记解决"},
    ],
}
STATUSES = {"pending_confirmation", "confirmed", "escalated", "resolved", "suppressed"}


def serialize_incident(item: dict) -> dict[str, Any]:
    return {**item, "allowed_actions": TRANSITIONS.get(item.get("status"), [])}


def allowed_action_names(item: dict) -> set[str]:
    return {action["action"] for action in TRANSITIONS.get(item.get("status"), [])}


def build_incident_list(store, watch: dict, *, entity_id: str | None = None,
                        status: str | None = None) -> tuple[dict[str, Any], str, list[str]]:
    if status and status not in STATUSES:
        raise APIError("INVALID_PARAMETER", "事件状态非法")
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    incidents = [
        serialize_incident(item) for item in store.list_incidents(status=status, limit=1000)
        if item.get("entity_id") == resolved_id
    ]
    platforms = [str(item) for item in (watch.get("platforms") or [])]
    _, quality, notes = latest_platform_runs(store, resolved_id, platforms)
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "items": incidents,
        "count": len(incidents),
    }, quality, notes


def build_incident_detail(store, watch: dict, incident_id: str) -> tuple[dict[str, Any], str, list[str]]:
    item = store.get_incident(incident_id)
    if item is None:
        raise APIError("NOT_FOUND", "事件不存在", 404)
    resolved_id, entity_name = resolve_entity(watch, item.get("entity_id"))
    platforms = [str(value) for value in (watch.get("platforms") or [])]
    _, quality, notes = latest_platform_runs(store, resolved_id, platforms)
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "incident": serialize_incident(item),
    }, quality, notes
