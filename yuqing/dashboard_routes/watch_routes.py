# -*- coding: utf-8 -*-
"""Watch configuration, keyword, and seed routes."""

from __future__ import annotations

from ..api.entities import resolve_entity
from ..dashboard_context import APIResponse, RequestContext


def watch_config(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from ..api.watch import build_watch_config

    requested_entity = ctx.query_value("entity_id")
    data = build_watch_config(watch, entity_id=requested_entity)
    return APIResponse(data, data["entity"]["id"])


def keywords(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from ..api.watch import build_keywords

    requested_entity = ctx.query_value("entity_id")
    with ctx.store() as store:
        data = build_keywords(store, watch, entity_id=requested_entity)
    return APIResponse(data, data["entity"]["id"])


def seeds(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from ..api.watch import build_seeds

    requested_entity = ctx.query_value("entity_id")
    with ctx.store() as store:
        data = build_seeds(store, watch, entity_id=requested_entity)
    return APIResponse(data, data["entity"]["id"])


def mutate_keyword(ctx: RequestContext, watch: dict, _entity_id: str,
                   _match=None) -> APIResponse:
    from ..api.watch import mutate_keyword as mutate

    body = ctx.body()
    requested_entity = str(body.get("entity_id") or "").strip() or None
    entity_id, _ = resolve_entity(watch, requested_entity)
    with ctx.store() as store:
        result = mutate(store, watch, body, entity_id=requested_entity)
    return APIResponse({"entity": {"id": entity_id}, "result": result}, entity_id)


def mutate_seed(ctx: RequestContext, watch: dict, _entity_id: str,
                _match=None) -> APIResponse:
    from ..api.watch import mutate_seed as mutate

    body = ctx.body()
    requested_entity = str(body.get("entity_id") or "").strip() or None
    entity_id, _ = resolve_entity(watch, requested_entity)
    with ctx.store() as store:
        result, _updated_watch = mutate(
            store, watch, body, entity_id=requested_entity,
        )
    return APIResponse({"entity": {"id": entity_id}, "result": result}, entity_id)


def update(ctx: RequestContext, _watch: dict | None = None, _match=None) -> APIResponse:
    from ..api.watch import build_watch_config, update_watch_config

    body = ctx.body()
    updated = update_watch_config(ctx.load_watch(), body)
    requested_entity = str(body.get("entity_id") or "").strip() or None
    entity_id, _ = resolve_entity(updated, requested_entity)
    data = build_watch_config(updated, entity_id=entity_id)
    return APIResponse(data, entity_id)
