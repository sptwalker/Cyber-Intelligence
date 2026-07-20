# -*- coding: utf-8 -*-
"""Context, overview, analysis, and backlog routes."""

from __future__ import annotations

from ..api.overview import RANGES, build_overview, configured_entities, resolve_entity
from ..api.responses import enum_value
from ..dashboard_context import APIResponse, CSVResponse, RequestContext


def context(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    requested_entity = ctx.query_value("entity_id")
    entity_id, entity_name = resolve_entity(watch, requested_entity)
    return APIResponse({
        "entity": {"id": entity_id, "name": entity_name},
        "entities": configured_entities(watch),
        "ranges": [
            {"value": "7d", "label": "近 7 天"},
            {"value": "30d", "label": "近 30 天"},
            {"value": "90d", "label": "近 90 天"},
        ],
        "user": ctx.principal(),
    }, entity_id)


def overview(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    requested_entity = ctx.query_value("entity_id")
    range_name = enum_value(ctx.query, "range", RANGES, default="7d")
    with ctx.store() as store:
        data, quality, notes = build_overview(
            store, watch, entity_id=requested_entity, range_name=range_name,
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def analysis(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from ..api.analysis import build_analysis

    requested_entity = ctx.query_value("entity_id")
    range_name = enum_value(ctx.query, "range", RANGES, default="7d")
    with ctx.store() as store:
        data, quality, notes = build_analysis(
            store, watch, entity_id=requested_entity, range_name=range_name,
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def backlog(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from ..api.backlog import build_backlog

    requested_entity = ctx.query_value("entity_id")
    range_name = enum_value(ctx.query, "range", RANGES, default="30d")
    with ctx.store() as store:
        data, quality, notes = build_backlog(
            store, watch, entity_id=requested_entity, range_name=range_name,
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def backlog_csv(ctx: RequestContext, watch: dict, _match=None) -> CSVResponse:
    from ..api.backlog import backlog_csv as build_backlog_csv

    requested_entity = ctx.query_value("entity_id")
    range_name = enum_value(ctx.query, "range", RANGES, default="30d")
    with ctx.store() as store:
        text, _entity_id = build_backlog_csv(
            store, watch, entity_id=requested_entity, range_name=range_name,
        )
    return CSVResponse(text)
