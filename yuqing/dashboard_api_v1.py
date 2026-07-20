# -*- coding: utf-8 -*-
"""Compatibility facade for versioned dashboard API route handlers."""

from __future__ import annotations

from .dashboard_context import RequestContext
from .dashboard_routes import dispatch_get, dispatch_post, dispatch_put


def handle_get(self, u, db: str, app) -> None:
    """Dispatch a GET request while preserving the historical entry point."""
    dispatch_get(RequestContext(self, u, db, app))


def handle_post(self, u, db: str, app) -> None:
    """Dispatch a POST request while preserving the historical entry point."""
    dispatch_post(RequestContext(self, u, db, app))


def handle_put(self, u, db: str, app) -> None:
    """Dispatch a PUT request while preserving the historical entry point."""
    dispatch_put(RequestContext(self, u, db, app))
