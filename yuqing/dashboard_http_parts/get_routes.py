# -*- coding: utf-8 -*-
"""GET routes retained for legacy dashboard pages and APIs."""

from __future__ import annotations

from urllib.parse import parse_qs, quote


def _handle_config(handler, parsed_url) -> bool:
    app = handler._dashboard_app
    if parsed_url.path == "/config":
        if not app._write_allowed(handler):
            handler.send_error(403)
            return True
        handler._send(app.render_config())
        return True
    if parsed_url.path == "/config/test":
        if not app._write_allowed(handler):
            handler.send_error(403)
            return True
        provider = parse_qs(parsed_url.query).get("p", [""])[0]
        handler._send(app.render_config(test_msg=app._run_test(provider)))
        return True
    return False


def _handle_workbench(handler, parsed_url) -> bool:
    app = handler._dashboard_app
    if parsed_url.path in ("/", "/v2", "/v2/"):
        if not app._write_allowed(handler) and app._require_auth(handler) is None:
            handler._redirect(f"/auth/login?next={quote(handler.path, safe='')}")
            return True
        handler._send_workbench_asset("index.html")
        return True

    asset_prefix = next(
        (
            prefix
            for prefix in ("/assets/", "/v2/assets/")
            if parsed_url.path.startswith(prefix)
        ),
        None,
    )
    if asset_prefix is None:
        return False
    if handler._api_principal() is None:
        handler.send_error(401)
        return True
    handler._send_workbench_asset(parsed_url.path[len(asset_prefix) :])
    return True


def _handle_operational_get(handler, parsed_url) -> bool:
    app = handler._dashboard_app
    if parsed_url.path == "/api/run/status":
        if not (app._write_allowed(handler) or app._require_auth(handler)):
            handler.send_error(403)
            return True
        handler._send_legacy_json(
            {
                "running": app._run_state["running"],
                "last": app._run_state["last"],
                "current": app._run_state["current"],
            }
        )
        return True

    if parsed_url.path == "/api/login/status":
        if not app._write_allowed(handler):
            handler.send_error(403)
            return True
        # Import through the package facade so historical yuqing.load_watch patches keep working.
        from .. import load_watch, login

        try:
            platforms = load_watch().get("platforms", [])
        except SystemExit:
            platforms = list(login.LOGIN_URLS)
        ok, message = login.bridge_ok()
        handler._send_legacy_json(
            {
                "bridge_ok": ok,
                "bridge_msg": message,
                "platforms": login.status(platforms),
            }
        )
        return True

    if parsed_url.path == "/login":
        if not app._write_allowed(handler):
            handler.send_error(403)
            return True
        handler._send(app.render_login())
        return True

    if parsed_url.path == "/watch":
        if not app._write_allowed(handler):
            handler.send_error(403)
            return True
        handler._send(app.render_watch())
        return True
    return False


def _handle_store_get(handler, parsed_url) -> None:
    app = handler._dashboard_app
    store = app.Store(handler._dashboard_db)
    try:
        query = parse_qs(parsed_url.query)
        if parsed_url.path == "/legacy":
            body = app.render_index(store)
        elif parsed_url.path == "/exec":
            body = app.render_exec(store)
        elif parsed_url.path == "/dash":
            body = app.render_dash(store, query.get("entity", [""])[0])
        elif parsed_url.path == "/chart-data":
            from .. import load_watch

            try:
                watch = load_watch()
            except SystemExit:
                watch = None
            handler._send_legacy_json(
                app.chart_data(store, query.get("entity", [""])[0], watch)
            )
            return
        elif parsed_url.path == "/report":
            body = app.render_report(store, query.get("run_id", [""])[0])
        elif parsed_url.path == "/keywords":
            body = app.render_keywords(store, query)
        elif parsed_url.path == "/annotate":
            body = app.render_annotate(store, query)
        elif parsed_url.path == "/accounts":
            body = app.render_accounts(store)
        elif parsed_url.path == "/api/seed/list":
            from ..keywords import KeywordManager

            manager = KeywordManager(store)
            entity_id = query.get("entity", [None])[0]
            seeds = manager.list_suggestions(
                status="pending", entity_id=entity_id, tag="seed_alias"
            )
            handler._send_legacy_json({"seeds": seeds}, default_str=True)
            return
        elif parsed_url.path == "/api/annotate/queue":
            from .. import analytics

            entity_id = query.get("entity", [None])[0]
            queue = analytics.active_sample(store, entity_id, limit=20)
            handler._send_legacy_json({"queue": queue}, default_str=True)
            return
        elif parsed_url.path == "/api/keywords":
            from ..keywords import KeywordManager

            manager = KeywordManager(store)
            tag = query.get("tag", [None])[0]
            entity_id = query.get("entity", [None])[0]
            keywords = manager.list(tag=tag, entity_id=entity_id)
            suggestions = manager.list_suggestions(
                status="pending", entity_id=entity_id, exclude_tag="seed_alias"
            )
            handler._send_legacy_json(
                {"keywords": keywords, "suggestions": suggestions},
                default_str=True,
            )
            return
        elif parsed_url.path == "/api/incidents":
            status = query.get("status", [None])[0]
            handler._send_legacy_json(
                {"incidents": store.list_incidents(status=status)},
                default_str=True,
            )
            return
        else:
            handler.send_error(404)
            return
    finally:
        store.close()
    handler._send(body)


def dispatch_legacy_get(handler, parsed_url) -> None:
    """Dispatch every non-auth GET route while preserving historical ordering."""
    app = handler._dashboard_app
    if _handle_config(handler, parsed_url):
        return
    if parsed_url.path.startswith("/api/v1/"):
        handler._handle_api_v1_get(parsed_url)
        return
    if _handle_workbench(handler, parsed_url):
        return
    if _handle_operational_get(handler, parsed_url):
        return
    if not app._write_allowed(handler) and app._require_auth(handler) is None:
        handler._redirect(f"/auth/login?next={quote(handler.path, safe='')}")
        return
    _handle_store_get(handler, parsed_url)
