# -*- coding: utf-8 -*-
"""Shared entity selection contract for versioned API read models."""

from __future__ import annotations

from .responses import APIError


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
