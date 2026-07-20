# -*- coding: utf-8 -*-
"""Small client for the optional localhost Collector sidecar."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


SELFCHECK_CONTRACT_VERSION = 1
SELFCHECK_NATIVE_ID = "yuqing-collector-selfcheck-v1"


def base_url() -> str:
    return os.getenv("YUQING_COLLECTOR_URL", "").strip().rstrip("/")


def enabled() -> bool:
    return bool(base_url())


def _request(
    path: str, *, method: str = "GET", body: dict[str, Any] | None = None,
    timeout: int = 15, allow_not_ready: bool = False,
) -> dict[str, Any]:
    target = base_url()
    if not target:
        raise RuntimeError("未配置 Collector sidecar")
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        target + path,
        data=raw,
        method=method,
        headers={"Content-Type": "application/json"} if raw is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = None
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            message = (
                payload.get("error") or payload.get("message")
                if isinstance(payload, dict) else None
            )
        except Exception:
            message = None
        if not (allow_not_ready and exc.code == 503 and isinstance(payload, dict)):
            raise RuntimeError(str(message or f"Collector HTTP {exc.code}")) from exc
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Collector 不可用：{str(exc)[:160]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Collector 返回格式非法")
    if payload.get("success") is False:
        raise RuntimeError(str(payload.get("error") or "Collector 操作失败"))
    return payload


def health(*, timeout: int = 5) -> dict[str, Any]:
    try:
        return _request("/readyz", timeout=timeout, allow_not_ready=True)
    except RuntimeError as exc:
        return {
            "success": False,
            "ready": False,
            "opencli_available": False,
            "browser_connected": False,
            "message": str(exc),
        }


def selfcheck(*, timeout: int = 5) -> dict[str, Any]:
    """Verify the deployed collector item contract through normal normalization."""
    payload = _request("/v1/selfcheck", timeout=timeout)
    if payload.get("contract_version") != SELFCHECK_CONTRACT_VERSION:
        raise RuntimeError("Collector selfcheck contract version mismatch")
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("platform") != "weibo":
        raise RuntimeError("Collector selfcheck item contract invalid")

    from .normalization import normalize

    doc = normalize(
        "weibo", "_collector_selfcheck", item,
        backend="collector-selfcheck", fetched_at="2026-01-01T00:00:00+08:00",
    )
    if doc.native_id != SELFCHECK_NATIVE_ID or not doc.text:
        raise RuntimeError("Collector selfcheck item normalization failed")
    return {
        "ready": True,
        "contract_version": SELFCHECK_CONTRACT_VERSION,
        "platform": doc.platform,
        "native_id": doc.native_id,
        "doc_id": doc.doc_id,
    }


def fetch(
    platform: str, keyword: str, limit: int, *, entry: str = "search",
    user: str | None = None,
) -> list[dict]:
    payload = _request(
        "/v1/fetch", method="POST",
        body={
            "platform": platform,
            "keyword": keyword,
            "limit": limit,
            "entry": entry,
            "user": user or "",
        },
        timeout=150,
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("Collector 未返回采集列表")
    return items


def bridge_status() -> tuple[bool, str]:
    payload = health()
    return bool(payload.get("ready")), str(payload.get("message") or "Collector 状态未知")


def login_status(platforms: list[str]) -> list[dict]:
    query = urllib.parse.urlencode({"platforms": ",".join(platforms)})
    payload = _request(f"/v1/login/status?{query}", timeout=75)
    rows = payload.get("platforms")
    if not isinstance(rows, list):
        raise RuntimeError("Collector 未返回登录状态")
    return rows


def open_login(platform: str) -> str:
    payload = _request(
        "/v1/login/open", method="POST", body={"platform": platform}, timeout=30,
    )
    return str(payload.get("message") or "已打开平台登录页")
