# -*- coding: utf-8 -*-
"""Thin HTTP adapter for dashboard assets, legacy routes, auth, and API v1."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from .dashboard_http_parts import (
    AuthFlowMixin,
    ResponseSupportMixin,
    dispatch_get,
    dispatch_post,
    dispatch_put,
)


def make_handler(db: str, app):
    """Build the historical handler class around explicit internal route modules."""

    class Handler(AuthFlowMixin, ResponseSupportMixin, BaseHTTPRequestHandler):
        _dashboard_app = app
        _dashboard_db = db

        def do_GET(self):
            dispatch_get(self)

        def do_POST(self):
            dispatch_post(self)

        def do_PUT(self):
            dispatch_put(self)

        def log_message(self, *args):
            """Keep the stdlib server quiet, as the compatibility handler always has."""

    return Handler
