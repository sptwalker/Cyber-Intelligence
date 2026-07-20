# -*- coding: utf-8 -*-
"""Report list/detail, source traceability, and deterministic generation."""

from __future__ import annotations

import datetime as _dt
import re
import secrets
import threading
from typing import Any
from urllib.parse import urlparse

from ..report import build_report, validate_citations
from .collection import latest_platform_runs
from .entities import resolve_entity
from .responses import APIError


_CITATION = re.compile(r"\[来源:([0-9a-f]{6,16})\]")
_generation_lock = threading.Lock()


def _safe_url(value: str) -> str | None:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return parsed.geturl()


def _title(markdown: str, run_id: str) -> str:
    for line in str(markdown or "").splitlines():
        if line.startswith("# "):
            return line[2:].strip() or run_id
    return run_id


def _quality(store, watch: dict, entity_id: str) -> tuple[str, list[str]]:
    platforms = [str(item) for item in (watch.get("platforms") or [])]
    _, quality, notes = latest_platform_runs(store, entity_id, platforms)
    return quality, notes


def _report_watch(watch: dict, requested: str | None) -> tuple[dict, str, str]:
    entity_id, entity_name = resolve_entity(watch, requested)
    entities = watch.get("entities") or []
    selected = next((item for item in entities if str(item.get("id")) == entity_id), None)
    if selected is None or selected.get("type", "self") != "self":
        raise APIError("INVALID_ENTITY", "报告仅支持自有监控对象")
    scoped = dict(watch)
    scoped["entities"] = [selected] + [
        item for item in entities if item is not selected and item.get("type") == "competitor"
    ]
    return scoped, entity_id, entity_name


def build_report_list(
    store, watch: dict, *, entity_id: str | None = None, limit: int = 100,
) -> tuple[dict[str, Any], str, list[str]]:
    """Return persisted reports newest first without rendering Markdown as HTML."""
    _, resolved_id, entity_name = _report_watch(watch, entity_id)
    rows = store.conn.execute(
        "SELECT run_id,created_at,markdown FROM reports ORDER BY created_at DESC LIMIT ?",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    items = [
        {
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "title": _title(row["markdown"], row["run_id"]),
            "citation_count": len(set(_CITATION.findall(row["markdown"] or ""))),
        }
        for row in rows
    ]
    quality, notes = _quality(store, watch, resolved_id)
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "items": items,
        "count": len(items),
    }, quality, notes


def build_report_detail(
    store, watch: dict, run_id: str, *, entity_id: str | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Return one report and its citation identifiers."""
    _, resolved_id, entity_name = _report_watch(watch, entity_id)
    row = store.conn.execute(
        "SELECT run_id,created_at,markdown FROM reports WHERE run_id=?", (run_id,),
    ).fetchone()
    if row is None:
        raise APIError("NOT_FOUND", "报告不存在", 404)
    markdown = row["markdown"] or ""
    quality, notes = _quality(store, watch, resolved_id)
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "report": {
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "title": _title(markdown, row["run_id"]),
            "markdown": markdown,
            "citations": sorted(set(_CITATION.findall(markdown))),
        },
    }, quality, notes


def build_source_document(
    store, watch: dict, doc_id: str, *, entity_id: str | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Return a cited source document only when it belongs to the selected entity."""
    _, resolved_id, entity_name = _report_watch(watch, entity_id)
    if resolved_id not in store.entities_for_doc(doc_id):
        raise APIError("NOT_FOUND", "来源文档不存在", 404)
    row = store.conn.execute(
        "SELECT c.doc_id,c.platform,c.author,c.text,c.url,c.publish_ts,c.fetched_at,"
        "f.polarity,f.confidence,f.risk,f.topic_label,f.summary,f.evidence "
        "FROM clean c LEFT JOIN features f USING(doc_id) WHERE c.doc_id=?",
        (doc_id,),
    ).fetchone()
    if row is None:
        raise APIError("NOT_FOUND", "来源文档不存在", 404)
    document = dict(row)
    document["url"] = _safe_url(document.get("url"))
    quality, notes = _quality(store, watch, resolved_id)
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "document": document,
    }, quality, notes


def generate_report(
    store, watch: dict, *, entity_id: str | None = None, now: str | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Generate one deterministic report while rejecting concurrent requests."""
    scoped_watch, resolved_id, _ = _report_watch(watch, entity_id)
    if not _generation_lock.acquire(blocking=False):
        raise APIError("REPORT_GENERATION_IN_PROGRESS", "已有报告正在生成", 409)
    try:
        timestamp = now or _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        run_id = "manual-" + _dt.datetime.fromisoformat(timestamp).strftime("%Y%m%d-%H%M%S")
        run_id += "-" + secrets.token_hex(3)
        platforms = [str(item) for item in (watch.get("platforms") or [])]
        platform_rows, quality, notes = latest_platform_runs(store, resolved_id, platforms)
        health_by_platform = {item["platform"]: item["health"] for item in platform_rows}
        markdown = build_report(
            store, scoped_watch, run_id=run_id, now=timestamp,
            health_by_platform=health_by_platform, use_claude=False,
        )
        invalid = validate_citations(markdown, store)
        if invalid:
            store.conn.rollback()
            raise APIError("INVALID_REPORT", "报告引用校验失败", 500)
        store.conn.commit()
        data, _, _ = build_report_detail(
            store, watch, run_id, entity_id=resolved_id,
        )
        data["generated"] = True
        return data, quality, notes
    finally:
        _generation_lock.release()
