# -*- coding: utf-8 -*-
"""Deterministic analysis read model shared with reports and legacy dashboards."""

from __future__ import annotations

from typing import Any

from .. import analytics
from ..report import aggregate
from .collection import latest_platform_runs
from .overview import RANGES, filter_range, resolve_entity
from .responses import APIError


def build_analysis(store, watch: dict, *, entity_id: str | None = None,
                   range_name: str = "7d") -> tuple[dict[str, Any], str, list[str]]:
    if range_name not in RANGES:
        raise APIError("INVALID_PARAMETER", "参数 range 仅支持：7d、30d、90d")
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    metrics = aggregate(store, resolved_id)
    aspects = analytics.aspect_breakdown(store, resolved_id)
    daily = filter_range(analytics.daily_series(store, resolved_id), RANGES[range_name])
    bhi = filter_range(analytics.bhi_trend(store, resolved_id), RANGES[range_name])
    semantic_topics = analytics.semantic_topics(store, resolved_id)[:8]
    platforms = [str(item) for item in (watch.get("platforms") or [])]
    _, quality, quality_notes = latest_platform_runs(store, resolved_id, platforms)

    sample_count = metrics["n_total"]
    if sample_count == 0:
        confidence = "no_data"
        confidence_note = "暂无已分析样本，不能形成情绪结论。"
    elif quality != "ok":
        confidence = "degraded"
        confidence_note = "采集状态不完整，分析结果仅反映当前已入库样本。"
    elif sample_count < 20:
        confidence = "low_sample"
        confidence_note = f"当前仅 {sample_count} 条样本，趋势波动可能较大。"
    else:
        confidence = "normal"
        confidence_note = f"当前分析基于 {sample_count} 条已入库样本。"

    data = {
        "entity": {"id": resolved_id, "name": entity_name},
        "range": range_name,
        "metrics_scope": "all_available",
        "sample": {
            "count": sample_count,
            "confidence": confidence,
            "note": confidence_note,
        },
        "sentiment_trend": [
            {
                "day": item["day"], "positive": item["pos"], "negative": item["neg"],
                "neutral": item["neu"], "total": item["total"],
            }
            for item in daily
        ],
        "bhi_trend": bhi,
        "aspects": [
            {
                **item,
                "neg_ratio": round(item["neg_ratio"], 4),
                "net_sentiment": round((item["pos"] - item["neg"]) / item["n"], 4)
                if item["n"] else None,
            }
            for item in aspects
        ],
        "topics": [{"topic": topic, "count": count} for topic, count in metrics["top_topics"]],
        "semantic_topics": semantic_topics,
    }
    return data, quality, quality_notes
