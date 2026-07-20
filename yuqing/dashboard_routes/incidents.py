# -*- coding: utf-8 -*-
"""Incident query and transition routes."""

from __future__ import annotations

import datetime as _dt

from ..api.responses import APIError
from ..dashboard_context import APIResponse, RequestContext


def list_incidents(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from ..api.incidents import build_incident_list

    requested_entity = ctx.query_value("entity_id")
    with ctx.store() as store:
        data, quality, notes = build_incident_list(
            store, watch, entity_id=requested_entity,
            status=ctx.query_value("status"),
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def detail(ctx: RequestContext, watch: dict, match) -> APIResponse:
    from ..api.incidents import build_incident_detail

    with ctx.store() as store:
        data, quality, notes = build_incident_detail(store, watch, match.group(1))
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def transition(ctx: RequestContext, _watch: dict, entity_id: str, match) -> APIResponse:
    from .. import alerts
    from ..api.incidents import allowed_action_names, serialize_incident

    body = ctx.body()
    action = str(body.get("action") or "").strip()
    note = str(body.get("note") or "").strip()[:1000]
    with ctx.store() as store:
        incident = store.get_incident(match.group(1))
        if incident is None:
            raise APIError("NOT_FOUND", "事件不存在", 404)
        if action not in allowed_action_names(incident):
            raise APIError("INVALID_TRANSITION", "当前状态不能执行该操作", 409)
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        result = alerts.transition(
            store, incident["incident_id"], action, actor=ctx.actor(), now=now, note=note,
        )
        if not result.get("success"):
            code = "DELIVERY_FAILED" if action == "escalate" else "INVALID_TRANSITION"
            raise APIError(code, result.get("message") or "事件状态更新失败", 409)
        data = {
            "incident": serialize_incident(result["incident"]),
            "executive_pushed": bool(result.get("executive_pushed")),
        }
    return APIResponse(data, entity_id)
