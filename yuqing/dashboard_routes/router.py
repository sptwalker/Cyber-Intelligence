# -*- coding: utf-8 -*-
"""Small method/path dispatcher for the dashboard API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Pattern

from ..api.entities import resolve_entity
from ..api.responses import APIError
from ..dashboard_context import APIResponse, CSVResponse, RequestContext
from .collection import login_status, open_login, run, status, stop
from .core import analysis, backlog, backlog_csv, context, overview
from .incidents import detail as incident_detail
from .incidents import list_incidents, transition
from .reports import detail as report_detail
from .reports import generate, list_reports, source_document
from .reviews import list_reviews, save_batch, save_one
from .watch_routes import keywords, mutate_keyword, mutate_seed, seeds, update, watch_config

RouteHandler = Callable[..., APIResponse | CSVResponse]


@dataclass(frozen=True)
class FailurePolicy:
    code: str
    message: str
    status: int = 500
    expose_exception: bool = False


@dataclass(frozen=True)
class Route:
    handler: RouteHandler
    path: str | None = None
    pattern: Pattern[str] | None = None
    failure: FailurePolicy | None = None

    def match(self, path: str):
        if self.path == path:
            return True
        if self.pattern is not None:
            return self.pattern.fullmatch(path)
        return None


GET_ROUTES = (
    Route(context, path="/api/v1/context"),
    Route(overview, path="/api/v1/overview"),
    Route(analysis, path="/api/v1/analysis"),
    Route(backlog, path="/api/v1/backlog"),
    Route(backlog_csv, path="/api/v1/backlog.csv"),
    Route(status, path="/api/v1/collection/status"),
    Route(login_status, path="/api/v1/collection/login-status"),
    Route(list_reviews, path="/api/v1/reviews"),
    Route(list_incidents, path="/api/v1/incidents"),
    Route(incident_detail, pattern=re.compile(r"/api/v1/incidents/([0-9A-Za-z_-]+)")),
    Route(list_reports, path="/api/v1/reports"),
    Route(report_detail, pattern=re.compile(r"/api/v1/reports/([^/]+)")),
    Route(source_document, pattern=re.compile(r"/api/v1/docs/([0-9a-f]{6,16})")),
    Route(watch_config, path="/api/v1/watch"),
    Route(keywords, path="/api/v1/keywords"),
    Route(seeds, path="/api/v1/seeds"),
)

INTERNAL = FailurePolicy("INTERNAL_ERROR", "服务暂时不可用")
POST_ROUTES = (
    Route(run, path="/api/v1/collection/run"),
    Route(stop, path="/api/v1/collection/stop"),
    Route(
        open_login,
        path="/api/v1/collection/login/open",
        failure=FailurePolicy("COLLECTOR_UNAVAILABLE", "", 409, expose_exception=True),
    ),
    Route(save_batch, path="/api/v1/reviews/batch", failure=INTERNAL),
    Route(
        save_one,
        pattern=re.compile(r"/api/v1/reviews/([0-9A-Za-z_-]+)"),
        failure=INTERNAL,
    ),
    Route(
        transition,
        pattern=re.compile(r"/api/v1/incidents/([0-9A-Za-z_-]+)/transition"),
        failure=INTERNAL,
    ),
    Route(
        generate,
        path="/api/v1/reports/generate",
        failure=FailurePolicy("INTERNAL_ERROR", "报告生成失败"),
    ),
    Route(
        mutate_keyword,
        path="/api/v1/keywords",
        failure=FailurePolicy("INTERNAL_ERROR", "监控配置操作失败"),
    ),
    Route(
        mutate_seed,
        path="/api/v1/seeds",
        failure=FailurePolicy("INTERNAL_ERROR", "监控配置操作失败"),
    ),
)


def _find(routes: tuple[Route, ...], path: str) -> tuple[Route | None, Any]:
    for route in routes:
        match = route.match(path)
        if match:
            return route, None if match is True else match
    return None, None


def _send_api_error(ctx: RequestContext, exc: APIError) -> None:
    ctx.send_error(exc.code, exc.message, exc.status)


def _readiness(ctx: RequestContext) -> None:
    try:
        from .. import collector_client
        from ..api.overview import build_overview

        watch = ctx.load_watch()
        with ctx.store() as store:
            data, quality, notes = build_overview(store, watch, range_name="7d")
            schema_version = store.schema_version()
        if schema_version != 2 or not (ctx.app._WORKBENCH_DIR / "index.html").is_file():
            raise RuntimeError("delivery baseline unavailable")
        collector = collector_client.selfcheck(timeout=5) if collector_client.enabled() else None
    except Exception:
        ctx.send_error("NOT_READY", "服务尚未就绪", 503)
        return
    readiness = {"ready": True, "schema_version": schema_version}
    if collector is not None:
        readiness["collector"] = collector
    ctx.send_response(APIResponse(
        readiness, data["entity"]["id"], quality, tuple(notes),
    ))


def dispatch_get(ctx: RequestContext) -> None:
    if ctx.path == "/api/v1/readiness":
        _readiness(ctx)
        return
    if ctx.principal() is None:
        ctx.send_error("UNAUTHORIZED", "请先登录", 401)
        return
    route, match = _find(GET_ROUTES, ctx.path)
    if route is None:
        ctx.send_error("NOT_FOUND", "接口不存在", 404)
        return
    try:
        response = route.handler(ctx, ctx.load_watch(), match)
    except APIError as exc:
        _send_api_error(ctx, exc)
        return
    except (SystemExit, OSError, ValueError, TypeError):
        ctx.send_error("CONFIG_ERROR", "监控配置无法读取", 503)
        return
    except Exception:
        ctx.send_error("INTERNAL_ERROR", "服务暂时不可用", 500)
        return
    ctx.send_response(response)


def dispatch_post(ctx: RequestContext) -> None:
    if not ctx.handler._api_mutation_allowed():
        ctx.send_error("FORBIDDEN", "无权执行该操作", 403)
        return
    route, match = _find(POST_ROUTES, ctx.path)
    if route is None:
        ctx.send_error("NOT_FOUND", "接口不存在", 404)
        return
    try:
        watch = ctx.load_watch()
        entity_id, _ = resolve_entity(watch, None)
    except APIError as exc:
        _send_api_error(ctx, exc)
        return
    except (SystemExit, OSError, ValueError, TypeError):
        ctx.send_error("CONFIG_ERROR", "监控配置无法读取", 503)
        return
    try:
        response = route.handler(ctx, watch, entity_id, match)
    except APIError as exc:
        _send_api_error(ctx, exc)
        return
    except Exception as exc:
        if route.failure is None:
            raise
        message = str(exc)[:200] if route.failure.expose_exception else route.failure.message
        ctx.send_error(route.failure.code, message, route.failure.status)
        return
    ctx.send_response(response)


def dispatch_put(ctx: RequestContext) -> None:
    if ctx.path != "/api/v1/watch":
        ctx.send_error("NOT_FOUND", "接口不存在", 404)
        return
    if not ctx.handler._api_mutation_allowed():
        ctx.send_error("FORBIDDEN", "无权执行该操作", 403)
        return
    try:
        response = update(ctx)
    except APIError as exc:
        _send_api_error(ctx, exc)
        return
    except Exception:
        ctx.send_error("CONFIG_WRITE_FAILED", "监控配置保存失败", 500)
        return
    ctx.send_response(response)
