# -*- coding: utf-8 -*-
"""Report and source-document routes."""

from __future__ import annotations

from urllib.parse import unquote

from ..dashboard_context import APIResponse, RequestContext


def list_reports(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from ..api.reports import build_report_list

    requested_entity = ctx.query_value("entity_id")
    with ctx.store() as store:
        data, quality, notes = build_report_list(
            store, watch, entity_id=requested_entity,
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def detail(ctx: RequestContext, watch: dict, match) -> APIResponse:
    from ..api.reports import build_report_detail

    requested_entity = ctx.query_value("entity_id")
    with ctx.store() as store:
        data, quality, notes = build_report_detail(
            store, watch, unquote(match.group(1)), entity_id=requested_entity,
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def source_document(ctx: RequestContext, watch: dict, match) -> APIResponse:
    from ..api.reports import build_source_document

    requested_entity = ctx.query_value("entity_id")
    with ctx.store() as store:
        data, quality, notes = build_source_document(
            store, watch, match.group(1), entity_id=requested_entity,
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def generate(ctx: RequestContext, watch: dict, _entity_id: str, _match=None) -> APIResponse:
    from ..api.reports import generate_report

    body = ctx.body()
    requested_entity = str(body.get("entity_id") or "").strip() or None
    with ctx.store() as store:
        data, quality, notes = generate_report(
            store, watch, entity_id=requested_entity,
        )
    return APIResponse(
        data, data["entity"]["id"], quality, tuple(notes), status=201,
    )
