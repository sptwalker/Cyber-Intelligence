# -*- coding: utf-8 -*-
"""Top-level method routing for the dashboard HTTP handler."""

from __future__ import annotations

from urllib.parse import urlparse

from .auth import dispatch_auth_get
from .get_routes import dispatch_legacy_get
from .post_routes import dispatch_legacy_post


def dispatch_get(handler) -> None:
    parsed_url = urlparse(handler.path)
    if dispatch_auth_get(handler, parsed_url):
        return
    dispatch_legacy_get(handler, parsed_url)


def dispatch_post(handler) -> None:
    dispatch_legacy_post(handler, urlparse(handler.path))


def dispatch_put(handler) -> None:
    parsed_url = urlparse(handler.path)
    if parsed_url.path.startswith("/api/v1/"):
        handler._handle_api_v1_put(parsed_url)
        return
    handler.send_error(404)
