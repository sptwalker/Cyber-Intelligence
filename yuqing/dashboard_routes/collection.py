# -*- coding: utf-8 -*-
"""Collection status, login, run, and stop routes."""

from __future__ import annotations

from ..api.entities import resolve_entity
from ..api.responses import APIError, enum_value
from ..dashboard_context import APIResponse, RequestContext


def status(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from .. import login
    from ..api.collection import build_collection_status

    requested_entity = ctx.query_value("entity_id")
    include_login = enum_value(
        ctx.query, "include_login", ("0", "1"), default="1",
    ) == "1"

    def login_provider(platforms):
        return login.bridge_ok(), login.status(platforms)

    with ctx.store() as store:
        data, quality, notes = build_collection_status(
            store, watch, dict(ctx.app._run_state), entity_id=requested_entity,
            login_provider=login_provider if include_login else None,
        )
    return APIResponse(data, data["entity"]["id"], quality, tuple(notes))


def login_status(ctx: RequestContext, watch: dict, _match=None) -> APIResponse:
    from .. import login
    from ..api.collection import execution_environment

    requested_entity = ctx.query_value("entity_id")
    entity_id, entity_name = resolve_entity(watch, requested_entity)
    platforms = [str(item) for item in (watch.get("platforms") or [])]
    bridge_ok, bridge_message = login.bridge_ok()
    quality = "ok" if bridge_ok else "degraded"
    notes = () if bridge_ok else (bridge_message,)
    return APIResponse({
        "entity": {"id": entity_id, "name": entity_name},
        "execution": execution_environment(),
        "bridge": {"ok": bridge_ok, "message": bridge_message},
        "platforms": login.status(platforms),
    }, entity_id, quality, notes)


def open_login(ctx: RequestContext, _watch: dict, entity_id: str, _match=None) -> APIResponse:
    from .. import login

    platform = str(ctx.body().get("platform") or "").strip()
    if platform not in login.LOGIN_URLS:
        raise APIError("INVALID_PARAMETER", "该平台不支持交互登录", 400)
    message = login.open_login(platform)
    return APIResponse(
        {"platform": platform, "message": message or "已打开登录页"}, entity_id,
    )


def run(ctx: RequestContext, _watch: dict, entity_id: str, _match=None) -> APIResponse:
    from ..api.collection import execution_environment

    execution = execution_environment()
    if not execution["can_run"]:
        raise APIError("COLLECTION_UNAVAILABLE", execution["message"], 409)
    return APIResponse(ctx.app._start_background_run(ctx.db), entity_id)


def stop(ctx: RequestContext, _watch: dict, entity_id: str, _match=None) -> APIResponse:
    result = ctx.app._request_run_stop()
    if not result["stop_requested"]:
        raise APIError("NOT_RUNNING", result["message"], 409)
    return APIResponse(result, entity_id)
