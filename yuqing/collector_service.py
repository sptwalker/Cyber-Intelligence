# -*- coding: utf-8 -*-
"""Local-only HTTP boundary around Chromium/opencli collection capabilities."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .collect import (
    OPENCLI_SITE,
    _OPENCLI,
    _fetch_heimao,
    _fetch_opencli,
    _fetch_opencli_userposts,
)
from . import login


ALLOWED_PLATFORMS = frozenset(OPENCLI_SITE) | {"heimao"}
ALLOWED_ENTRIES = frozenset({"search", "user-posts"})
MAX_BODY_BYTES = 32 * 1024
SELFCHECK_CONTRACT_VERSION = 1
SELFCHECK_NATIVE_ID = "yuqing-collector-selfcheck-v1"


class CollectorRequestError(ValueError):
    pass


def _text(value, *, field: str, limit: int, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise CollectorRequestError(f"{field} 不能为空")
    if len(result) > limit:
        raise CollectorRequestError(f"{field} 过长")
    return result


def fetch_items(payload: dict) -> list[dict]:
    platform = _text(payload.get("platform"), field="platform", limit=32, required=True)
    entry = _text(payload.get("entry") or "search", field="entry", limit=32, required=True)
    keyword = _text(payload.get("keyword"), field="keyword", limit=200)
    user = _text(payload.get("user"), field="user", limit=200)
    try:
        limit = max(1, min(int(payload.get("limit", 50)), 100))
    except (TypeError, ValueError) as exc:
        raise CollectorRequestError("limit 必须是整数") from exc
    if platform not in ALLOWED_PLATFORMS:
        raise CollectorRequestError("不支持的平台")
    if entry not in ALLOWED_ENTRIES:
        raise CollectorRequestError("不支持的采集入口")
    if entry == "user-posts":
        if platform == "heimao":
            raise CollectorRequestError("黑猫不支持 user-posts")
        if not user:
            raise CollectorRequestError("user-posts 必须提供 user")
        return _fetch_opencli_userposts(OPENCLI_SITE[platform], user, limit)
    if not keyword:
        raise CollectorRequestError("search 必须提供 keyword")
    if platform == "heimao":
        return _fetch_heimao(keyword, limit)
    return _fetch_opencli(platform, keyword, limit)


def health_payload() -> dict:
    opencli_available = bool(shutil.which(_OPENCLI))
    browser_connected = False
    message = "Collector 未检测到 opencli"
    if opencli_available:
        try:
            output = subprocess.run(
                [_OPENCLI, "doctor"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=8,
            )
            text = (output.stdout or "") + (output.stderr or "")
            browser_connected = "Extension: connected" in text
            message = (
                "Collector 浏览器桥已连接"
                if browser_connected
                else "Collector 已启动，等待 Chromium 扩展连接"
            )
        except Exception as exc:
            message = f"Collector opencli 检测失败：{str(exc)[:120]}"
    return {
        "success": True,
        "ready": opencli_available and browser_connected,
        "opencli_available": opencli_available,
        "browser_connected": browser_connected,
        "message": message,
    }


def selfcheck_payload() -> dict:
    """Return a deterministic item without touching a platform or login session."""
    return {
        "success": True,
        "contract_version": SELFCHECK_CONTRACT_VERSION,
        "item": {
            "platform": "weibo",
            "id": SELFCHECK_NATIVE_ID,
            "text": "Yuqing collector readiness selfcheck",
            "user": {"nickname": "collector-selfcheck"},
            "created_at": "2026-01-01T00:00:00+08:00",
            "url": "https://example.invalid/yuqing-collector-selfcheck-v1",
        },
    }


class CollectorHandler(BaseHTTPRequestHandler):
    server_version = "YuqingCollector/1"

    def _json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise CollectorRequestError("Content-Length 非法") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise CollectorRequestError("请求体为空或过大")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectorRequestError("请求体不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise CollectorRequestError("请求体必须是对象")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/v1/selfcheck":
            self._json(selfcheck_payload())
            return
        if parsed.path in {"/healthz", "/readyz"}:
            payload = health_payload()
            self._json(payload, 200 if parsed.path == "/healthz" or payload["ready"] else 503)
            return
        if parsed.path == "/v1/login/status":
            platforms = [
                item for item in parse_qs(parsed.query).get("platforms", [""])[0].split(",")
                if item in ALLOWED_PLATFORMS
            ]
            self._json({"success": True, "platforms": login.status(platforms)})
            return
        self._json({"success": False, "error": "NOT_FOUND"}, 404)

    def do_POST(self) -> None:
        try:
            payload = self._body()
            if self.path == "/v1/fetch":
                items = fetch_items(payload)
                self._json({"success": True, "items": items, "count": len(items)})
                return
            if self.path == "/v1/login/open":
                platform = _text(
                    payload.get("platform"), field="platform", limit=32, required=True,
                )
                if platform not in login.LOGIN_URLS:
                    raise CollectorRequestError("该平台不需要或不支持交互登录")
                login.open_login(platform)
                self._json({
                    "success": True,
                    "message": f"已在 Collector Chromium 打开 {platform} 登录页",
                })
                return
            self._json({"success": False, "error": "NOT_FOUND"}, 404)
        except CollectorRequestError as exc:
            self._json({"success": False, "error": str(exc)}, 400)
        except Exception as exc:
            self._json({"success": False, "error": str(exc)[:240]}, 502)

    def log_message(self, format, *args) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8788) -> None:
    os.environ.pop("YUQING_COLLECTOR_URL", None)
    print(f"Collector sidecar listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), CollectorHandler).serve_forever()


if __name__ == "__main__":
    serve(
        host=os.getenv("YUQING_COLLECTOR_HOST", "127.0.0.1"),
        port=int(os.getenv("YUQING_COLLECTOR_PORT", "8788")),
    )
