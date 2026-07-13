# -*- coding: utf-8 -*-
"""Common response contract and request parsing for ``/api/v1``."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass
class APIError(Exception):
    """Stable, browser-safe API error without an internal stack trace."""

    code: str
    message: str
    status: int = 400

    def __str__(self) -> str:
        return self.message


def generated_at() -> str:
    """Return a timezone-aware ISO 8601 timestamp with second precision."""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def success_payload(
    data: Any,
    *,
    entity_id: str | None = None,
    data_quality: str = "ok",
    quality_notes: Iterable[str] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the phase-one success envelope."""
    response_meta: dict[str, Any] = {
        "generated_at": generated_at(),
        "entity_id": entity_id,
        "data_quality": data_quality,
    }
    notes = [str(note) for note in (quality_notes or []) if str(note).strip()]
    if notes:
        response_meta["quality_notes"] = notes
    if meta:
        response_meta.update(meta)
    return {"success": True, "data": data, "meta": response_meta}


def error_payload(code: str, message: str) -> dict[str, Any]:
    """Build the phase-one error envelope."""
    return {"success": False, "error": {"code": code, "message": message}}


def query_value(
    query: Mapping[str, list[str]], name: str, *, default: str | None = None,
    required: bool = False,
) -> str | None:
    """Read one scalar query parameter and reject repeated/empty values."""
    values = query.get(name)
    if not values:
        if required and default is None:
            raise APIError("MISSING_PARAMETER", f"缺少参数：{name}")
        return default
    if len(values) != 1:
        raise APIError("INVALID_PARAMETER", f"参数 {name} 只能出现一次")
    value = values[0].strip()
    if not value:
        if required and default is None:
            raise APIError("MISSING_PARAMETER", f"参数 {name} 不能为空")
        return default
    return value


def enum_value(
    query: Mapping[str, list[str]], name: str, allowed: Iterable[str], *, default: str,
) -> str:
    """Read a query enum and return a stable validation error when invalid."""
    allowed_values = tuple(allowed)
    value = query_value(query, name, default=default)
    if value not in allowed_values:
        choices = "、".join(allowed_values)
        raise APIError("INVALID_PARAMETER", f"参数 {name} 仅支持：{choices}")
    return value


def json_body(handler, *, max_bytes: int = 1_048_576) -> dict[str, Any]:
    """Read a bounded JSON object body from a ``BaseHTTPRequestHandler``."""
    raw_length = handler.headers.get("Content-Length") or "0"
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise APIError("INVALID_BODY", "Content-Length 非法") from exc
    if length <= 0:
        raise APIError("INVALID_BODY", "请求体不能为空")
    if length > max_bytes:
        raise APIError("BODY_TOO_LARGE", "请求体过大", 413)
    try:
        value = json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise APIError("INVALID_JSON", "请求体不是合法 JSON") from exc
    if not isinstance(value, dict):
        raise APIError("INVALID_BODY", "请求体必须是 JSON 对象")
    return value
