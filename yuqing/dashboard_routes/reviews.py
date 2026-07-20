# -*- coding: utf-8 -*-
"""Review query and mutation routes."""

from __future__ import annotations

from ..api.entities import resolve_entity
from ..api.responses import enum_value
from ..dashboard_context import APIResponse, RequestContext


def list_reviews(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from ..api.reviews import CONFIDENCE_BUCKETS, REVIEW_STATUSES, build_reviews

    requested_entity = ctx.query_value("entity_id")
    status = enum_value(ctx.query, "status", REVIEW_STATUSES, default="pending")
    confidence = enum_value(
        ctx.query, "confidence", CONFIDENCE_BUCKETS, default="all",
    )
    with ctx.store() as store:
        data, quality, notes = build_reviews(
            store, watch,
            entity_id=requested_entity,
            status=status,
            platform=ctx.query_value("platform"),
            confidence=confidence,
            limit=ctx.query_value("limit"),
            cursor=ctx.query_value("cursor"),
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def save_one(ctx: RequestContext, watch: dict, _entity_id: str, match) -> APIResponse:
    from ..api.reviews import save_review

    body = ctx.body()
    requested_entity = str(body.get("entity_id") or "").strip() or None
    entity_id, _ = resolve_entity(watch, requested_entity)
    with ctx.store() as store:
        review = save_review(
            store, watch, match.group(1),
            verdict=str(body.get("verdict") or "").strip(),
            entity_id=requested_entity,
            note=str(body.get("note") or ""),
            actor=ctx.actor(),
        )
    return APIResponse({"review": review}, entity_id)


def save_batch(ctx: RequestContext, watch: dict, _entity_id: str, _match=None) -> APIResponse:
    from ..api.reviews import save_review_batch

    body = ctx.body()
    requested_entity = str(body.get("entity_id") or "").strip() or None
    entity_id, _ = resolve_entity(watch, requested_entity)
    with ctx.store() as store:
        data = save_review_batch(
            store, watch, body.get("items"),
            entity_id=requested_entity, actor=ctx.actor(),
        )
    return APIResponse(data, entity_id)
