# -*- coding: utf-8 -*-
"""Request-scoped helpers shared by the versioned dashboard routes."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping
from urllib.parse import parse_qs

from .api.responses import json_body, query_value, success_payload


@dataclass(frozen=True)
class APIResponse:
    """One JSON response before it is written to the HTTP adapter."""

    data: Any
    entity_id: str | None
    data_quality: str = "ok"
    quality_notes: tuple[str, ...] = ()
    status: int = 200


@dataclass(frozen=True)
class CSVResponse:
    """One downloadable CSV response."""

    text: str
    filename: str = "yuqing-backlog.csv"


@dataclass
class RequestContext:
    """Dependencies and request-local state for a dashboard API call.

    The HTTP handler remains the compatibility boundary.  Domain routes use
    this object so Store creation/closing and request parsing are consistent.
    """

    handler: Any
    url: Any
    db: str
    app: Any
    _query: Mapping[str, list[str]] | None = field(default=None, init=False)
    _principal_loaded: bool = field(default=False, init=False)
    _principal: dict[str, Any] | None = field(default=None, init=False)

    @property
    def path(self) -> str:
        return self.url.path

    @property
    def query(self) -> Mapping[str, list[str]]:
        if self._query is None:
            self._query = parse_qs(self.url.query, keep_blank_values=True)
        return self._query

    def query_value(self, name: str, *, default: str | None = None,
                    required: bool = False) -> str | None:
        return query_value(self.query, name, default=default, required=required)

    def body(self) -> dict[str, Any]:
        return json_body(self.handler)

    def load_watch(self) -> dict[str, Any]:
        # Import the package-level facade at call time. Existing tests and
        # deployments monkeypatch ``yuqing.load_watch``.
        from . import load_watch
        return load_watch()

    @contextmanager
    def store(self) -> Iterator[Any]:
        store = self.app.Store(self.db)
        try:
            yield store
        finally:
            store.close()

    def principal(self) -> dict[str, Any] | None:
        if not self._principal_loaded:
            self._principal = self.handler._api_principal()
            self._principal_loaded = True
        return self._principal

    def actor(self) -> str:
        principal = self.principal() or {}
        return principal.get("name") or principal.get("open_id") or "unknown"

    def send_error(self, code: str, message: str, status: int) -> None:
        self.handler._send_api_error(code, message, status)

    def send_response(self, response: APIResponse | CSVResponse) -> None:
        if isinstance(response, CSVResponse):
            data = response.text.encode("utf-8-sig")
            self.handler.send_response(200)
            self.handler.send_header("Content-Type", "text/csv; charset=utf-8")
            self.handler.send_header(
                "Content-Disposition", f'attachment; filename="{response.filename}"',
            )
            self.handler.send_header("Content-Length", str(len(data)))
            self.handler.send_header("X-Content-Type-Options", "nosniff")
            self.handler.end_headers()
            self.handler.wfile.write(data)
            return
        self.handler._send_json(success_payload(
            response.data,
            entity_id=response.entity_id,
            data_quality=response.data_quality,
            quality_notes=response.quality_notes,
        ), response.status)
