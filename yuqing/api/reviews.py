# -*- coding: utf-8 -*-
"""Review queue read/write model for the phase-one workbench."""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import json
from typing import Any

from .overview import resolve_entity
from .responses import APIError


REVIEW_STATUSES = ("all", "pending", "approved", "rejected")
CONFIDENCE_BUCKETS = ("all", "low", "mid", "high")
VERDICT_LABELS = {
    "ok": "通过",
    "reject": "拒绝",
    "correct_positive": "改为正面",
    "correct_neutral": "改为中性",
    "correct_negative": "改为负面",
    "irony": "标记反讽",
    "spam": "标记水军",
    "irrelevant": "标记串味",
    "crisis": "危机确认",
}


def _positive_int(value: str | int | None, *, name: str, default: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise APIError("INVALID_PARAMETER", f"参数 {name} 必须是整数") from exc
    if parsed < 1 or parsed > maximum:
        raise APIError("INVALID_PARAMETER", f"参数 {name} 必须在 1～{maximum} 之间")
    return parsed


def _encode_cursor(row: dict[str, Any], filters: dict[str, Any]) -> str:
    payload = {
        "risk": float(row.get("risk") or 0.0),
        "confidence": float(row.get("confidence") or 0.0),
        "doc_id": str(row["doc_id"]),
        **filters,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, filters: dict[str, Any]) -> tuple[float, float, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        risk = float(payload["risk"])
        confidence = float(payload["confidence"])
        doc_id = str(payload["doc_id"])
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise APIError("INVALID_CURSOR", "分页游标无效") from exc
    if not doc_id or any(payload.get(key) != value for key, value in filters.items()):
        raise APIError("INVALID_CURSOR", "分页游标与当前筛选条件不匹配")
    return risk, confidence, doc_id


def _queue_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if float(row.get("confidence") or 0.0) < 0.6:
        reasons.append("low_confidence")
    if bool(row.get("is_ironic")):
        reasons.append("irony")
    if float(row.get("risk") or 0.0) >= 30:
        reasons.append("high_risk")
    try:
        signals = json.loads(row.get("signals") or "{}")
    except (TypeError, json.JSONDecodeError):
        signals = {}
    if isinstance(signals, dict) and ("cross_disagree" in signals or bool(signals.get("cross_disagree"))):
        reasons.append("model_disagreement")
    return reasons


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    verdict = row.get("verdict")
    status = "pending" if not verdict else ("approved" if verdict == "ok" else "rejected")
    return {
        "doc_id": row["doc_id"],
        "platform": row.get("platform") or "",
        "author": row.get("author") or "",
        "text": row.get("text") or "",
        "url": row.get("url") or "",
        "published_at": row.get("publish_ts") or "",
        "status": status,
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS.get(str(verdict), str(verdict or "")),
        "note": row.get("review_note") or "",
        "reviewed_at": row.get("reviewed_at") or "",
        "actor": row.get("actor") or "",
        "machine_polarity": row.get("polarity") or "neu",
        "confidence": float(row.get("confidence") or 0.0),
        "risk": float(row.get("risk") or 0.0),
        "topic_label": row.get("topic_label") or "",
        "is_ironic": bool(row.get("is_ironic")),
        "is_spam": bool(row.get("is_spam")),
        "queue_reasons": _queue_reasons(row),
    }


_SELECT = """
WITH latest_review AS (
    SELECT r.doc_id,r.verdict,r.note,r.ts,r.actor
    FROM review r
    WHERE r.rowid=(SELECT MAX(r2.rowid) FROM review r2 WHERE r2.doc_id=r.doc_id)
)
SELECT c.doc_id,c.platform,c.author,c.text,c.url,c.publish_ts,
       f.polarity,f.confidence,f.is_ironic,f.is_spam,f.risk,f.topic_label,f.signals,
       rv.verdict,rv.note AS review_note,rv.ts AS reviewed_at,rv.actor
FROM clean c
JOIN features f USING(doc_id)
LEFT JOIN latest_review rv ON rv.doc_id=c.doc_id
"""


def build_reviews(
    store,
    watch: dict,
    *,
    entity_id: str | None = None,
    status: str = "pending",
    platform: str | None = None,
    confidence: str = "all",
    limit: str | int | None = None,
    cursor: str | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Return a filtered, cursor-paginated review queue."""
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    if status not in REVIEW_STATUSES:
        raise APIError("INVALID_PARAMETER", "参数 status 仅支持：all、pending、approved、rejected")
    if confidence not in CONFIDENCE_BUCKETS:
        raise APIError("INVALID_PARAMETER", "参数 confidence 仅支持：all、low、mid、high")
    platforms = [str(item) for item in (watch.get("platforms") or [])]
    if platform and platform not in platforms:
        raise APIError("INVALID_PARAMETER", "参数 platform 不在监控平台配置中")
    page_size = _positive_int(limit, name="limit", default=20, maximum=100)
    filters = {
        "entity_id": resolved_id,
        "status": status,
        "platform": platform or "",
        "confidence_bucket": confidence,
    }

    where = [
        "EXISTS(SELECT 1 FROM document_entities de WHERE de.doc_id=c.doc_id AND de.entity_id=?)",
        "(COALESCE(f.confidence,0)<0.6 OR COALESCE(f.is_ironic,0)=1 OR COALESCE(f.risk,0)>=30 "
        "OR COALESCE(f.signals,'') LIKE '%cross_disagree%')",
    ]
    args: list[Any] = [resolved_id]
    if status == "pending":
        where.append("rv.doc_id IS NULL")
    elif status == "approved":
        where.append("rv.verdict='ok'")
    elif status == "rejected":
        where.append("rv.doc_id IS NOT NULL AND rv.verdict<>'ok'")
    if platform:
        where.append("c.platform=?")
        args.append(platform)
    if confidence == "low":
        where.append("COALESCE(f.confidence,0)<0.5")
    elif confidence == "mid":
        where.append("COALESCE(f.confidence,0)>=0.5 AND COALESCE(f.confidence,0)<=0.8")
    elif confidence == "high":
        where.append("COALESCE(f.confidence,0)>0.8")

    where_sql = " WHERE " + " AND ".join(where)
    total = store.conn.execute(
        "SELECT COUNT(*) FROM (" + _SELECT + where_sql + ")", tuple(args),
    ).fetchone()[0]

    page_where = list(where)
    page_args = list(args)
    if cursor:
        last_risk, last_confidence, last_doc_id = _decode_cursor(cursor, filters)
        page_where.append(
            "(COALESCE(f.risk,0)<? OR (COALESCE(f.risk,0)=? AND COALESCE(f.confidence,0)>?) "
            "OR (COALESCE(f.risk,0)=? AND COALESCE(f.confidence,0)=? AND c.doc_id>?))"
        )
        page_args.extend([
            last_risk, last_risk, last_confidence,
            last_risk, last_confidence, last_doc_id,
        ])
    sql = _SELECT + " WHERE " + " AND ".join(page_where)
    sql += " ORDER BY COALESCE(f.risk,0) DESC,COALESCE(f.confidence,0) ASC,c.doc_id ASC LIMIT ?"
    rows = [dict(row) for row in store.conn.execute(sql, (*page_args, page_size + 1)).fetchall()]
    has_more = len(rows) > page_size
    page = rows[:page_size]
    next_cursor = _encode_cursor(page[-1], filters) if has_more and page else None
    data = {
        "entity": {"id": resolved_id, "name": entity_name},
        "items": [_serialize(row) for row in page],
        "count": len(page),
        "total": total,
        "next_cursor": next_cursor,
        "platforms": platforms,
        "verdict_options": [
            {"value": value, "label": label} for value, label in VERDICT_LABELS.items()
        ],
        "filters": {
            "status": status, "platform": platform, "confidence": confidence, "limit": page_size,
        },
    }
    return data, "ok", []


def save_review(
    store,
    watch: dict,
    doc_id: str,
    *,
    verdict: str,
    entity_id: str | None = None,
    note: str = "",
    actor: str = "",
    now: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Append one auditable review verdict and return its persisted representation."""
    resolved_id, _ = resolve_entity(watch, entity_id)
    if verdict not in VERDICT_LABELS:
        raise APIError("INVALID_VERDICT", "复核结论不受支持")
    clean_doc_id = str(doc_id or "").strip()
    if not clean_doc_id or not store.document_exists(clean_doc_id):
        raise APIError("NOT_FOUND", "文档不存在", 404)
    if resolved_id not in store.entities_for_doc(clean_doc_id):
        raise APIError("NOT_FOUND", "文档不属于当前监控对象", 404)
    clean_note = str(note or "").strip()
    if len(clean_note) > 1000:
        raise APIError("INVALID_BODY", "复核备注不能超过 1000 个字符")
    timestamp = now or _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    store.add_review(
        clean_doc_id, verdict, clean_note, timestamp, actor=str(actor or "")[:100], commit=commit,
    )
    return {
        "doc_id": clean_doc_id,
        "status": "approved" if verdict == "ok" else "rejected",
        "verdict": verdict,
        "verdict_label": VERDICT_LABELS[verdict],
        "note": clean_note,
        "reviewed_at": timestamp,
        "actor": str(actor or "")[:100],
    }


def save_review_batch(
    store,
    watch: dict,
    items: Any,
    *,
    entity_id: str | None = None,
    actor: str = "",
) -> dict[str, Any]:
    """Persist up to 100 reviews and return a result for every submitted item."""
    if not isinstance(items, list) or not items:
        raise APIError("INVALID_BODY", "items 必须是非空数组")
    if len(items) > 100:
        raise APIError("BATCH_TOO_LARGE", "单次批量复核最多 100 条", 413)
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            results.append({
                "index": index, "success": False,
                "error": {"code": "INVALID_ITEM", "message": "复核项必须是 JSON 对象"},
            })
            continue
        doc_id = str(item.get("doc_id") or "").strip()
        if doc_id in seen:
            results.append({
                "index": index, "doc_id": doc_id, "success": False,
                "error": {"code": "DUPLICATE_DOCUMENT", "message": "同一批次不能重复提交文档"},
            })
            continue
        seen.add(doc_id)
        try:
            review = save_review(
                store, watch, doc_id,
                verdict=str(item.get("verdict") or "").strip(),
                entity_id=entity_id,
                note=str(item.get("note") or ""),
                actor=actor,
                now=now,
                commit=False,
            )
            results.append({"index": index, "doc_id": doc_id, "success": True, "review": review})
        except APIError as exc:
            results.append({
                "index": index, "doc_id": doc_id, "success": False,
                "error": {"code": exc.code, "message": exc.message},
            })
    store.commit()
    succeeded = sum(1 for item in results if item["success"])
    return {
        "results": results,
        "submitted": len(items),
        "succeeded": succeeded,
        "failed": len(items) - succeeded,
    }
