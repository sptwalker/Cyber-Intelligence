# -*- coding: utf-8 -*-
"""Response, static-asset, and versioned-API support for dashboard handlers."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse


class ResponseSupportMixin:
    """Keep the response surface consumed by legacy and versioned routes stable."""

    _dashboard_app = None
    _dashboard_db = ""

    def end_headers(self):
        """Advertise the successor for legacy JSON APIs without removing them."""
        path = urlparse(getattr(self, "path", "")).path
        if path.startswith("/api/") and not path.startswith("/api/v1/"):
            self.send_header("Deprecation", "true")
            self.send_header("Link", '</api/v1>; rel="successor-version"')
        super().end_headers()

    def _send(self, body: str, code: int = 200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_workbench_asset(self, asset_name: str) -> None:
        """Serve one packaged asset without allowing path traversal."""
        decoded = unquote(asset_name)
        relative = Path(decoded)
        if (
            not decoded
            or "\x00" in decoded
            or relative.is_absolute()
            or decoded.startswith(("/", "\\"))
        ):
            self.send_error(404)
            return
        root = self._dashboard_app._WORKBENCH_DIR.resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self.send_error(404)
            return
        if not candidate.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
        ):
            content_type += "; charset=utf-8"
        self._send_bytes(candidate.read_bytes(), content_type)

    def _send_json(self, payload: dict, code: int = 200):
        """Send the hardened JSON response used by the versioned API."""
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_legacy_json(
        self,
        payload: dict,
        code: int = 200,
        *,
        default_str: bool = False,
    ) -> None:
        """Send the exact minimal header set used by pre-v1 JSON routes."""
        kwargs = {"ensure_ascii": False}
        if default_str:
            kwargs["default"] = str
        data = json.dumps(payload, **kwargs).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_api_error(self, code: str, message: str, status: int):
        from ..api.responses import error_payload

        self._send_json(error_payload(code, message), status)

    def _api_principal(self) -> dict | None:
        """Return one reusable identity shape for local and OAuth API reads."""
        app = self._dashboard_app
        if app._write_allowed(self):
            return {"open_id": "local", "name": "本机用户", "auth_type": "local"}
        user = app._require_auth(self)
        return ({**user, "auth_type": "oauth"} if user else None)

    def _api_mutation_allowed(self) -> bool:
        """Expose session/origin/forwarded-host checks to versioned routes."""
        return self._dashboard_app._mutation_allowed(self)

    def _handle_api_v1_get(self, parsed_url) -> None:
        from ..dashboard_api_v1 import handle_get

        handle_get(self, parsed_url, self._dashboard_db, self._dashboard_app)

    def _handle_api_v1_post(self, parsed_url) -> None:
        from ..dashboard_api_v1 import handle_post

        handle_post(self, parsed_url, self._dashboard_db, self._dashboard_app)

    def _handle_api_v1_put(self, parsed_url) -> None:
        from ..dashboard_api_v1 import handle_put

        handle_put(self, parsed_url, self._dashboard_db, self._dashboard_app)

    def _redirect(self, location: str, set_cookie: str = ""):
        """Send a 302 redirect, optionally creating or clearing a session cookie."""
        self.send_response(302)
        self.send_header("Location", location)
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()
