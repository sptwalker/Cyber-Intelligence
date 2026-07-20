# -*- coding: utf-8 -*-
"""Feishu OAuth flow for the dashboard compatibility handler."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


class AuthFlowMixin:
    """Retain the historical Handler auth helper methods as thin adapters."""

    def _auth_login(self):
        auth_login(self)

    def _auth_callback(self):
        auth_callback(self)

    def _auth_logout(self):
        auth_logout(self)


def auth_login(handler) -> None:
    """Generate state and redirect to Feishu, or render the setup hint."""
    from .. import config

    app = handler._dashboard_app
    parsed_url = urlparse(handler.path)
    next_path = parse_qs(parsed_url.query).get("next", ["/"])[0]
    app_id = config.resolve("FEISHU_APP_ID")
    redirect_uri = config.resolve("FEISHU_REDIRECT_URI")
    if not app_id or not redirect_uri:
        handler._send(app.render_auth_hint())
        return
    state = app._new_state(next_path)
    handler._redirect(app._feishu_authorize_url(app_id, redirect_uri, state))


def auth_callback(handler) -> None:
    """Validate OAuth state, exchange the code, and establish the session."""
    app = handler._dashboard_app
    query = parse_qs(urlparse(handler.path).query)
    code = query.get("code", [""])[0]
    state = query.get("state", [""])[0]
    state_data = app._oauth_states.pop(state, None)
    if not code or not state_data:
        handler._send(
            app.render_auth_error("授权校验失败（state 无效或已过期），请重新登录。"),
            400,
        )
        return
    try:
        token = app._feishu_user_access_token(code)
        user = app._feishu_user_info(token)
    except Exception as exc:
        handler._send(app.render_auth_error(f"飞书登录失败：{str(exc)[:200]}"), 502)
        return
    sid = app._new_session(user)
    handler._redirect(
        app._safe_next(state_data.get("next", "/")),
        app._cookie_header(sid),
    )


def auth_logout(handler) -> None:
    """Delete the current session and expire the browser cookie."""
    app = handler._dashboard_app
    sid = app._sid_from_cookie(handler)
    if sid:
        app._session_delete(sid)
    handler._redirect("/auth/login", app._cookie_header("", clear=True))


def dispatch_auth_get(handler, parsed_url) -> bool:
    """Handle one public auth route and report whether it matched."""
    routes = {
        "/auth/login": handler._auth_login,
        "/auth/callback": handler._auth_callback,
        "/auth/logout": handler._auth_logout,
    }
    action = routes.get(parsed_url.path)
    if action is None:
        return False
    action()
    return True
