# -*- coding: utf-8 -*-
"""报告聚合阶段：只从存储快照计算确定性指标。"""

from __future__ import annotations

import json

from .. import analytics


def aggregate(store, entity_id: str, *, since_day: str | None = None) -> dict:
    """从 features 算聚合指标（全部数字的唯一来源）。"""
    rows = [dict(r) for r in store.joined(entity_id)]
    if since_day:
        rows = [
            row for row in rows
            if analytics.normalize_day(row.get("publish_ts"), row.get("fetched_at")) >= since_day
        ]
    n = len(rows)
    negs = sorted((r for r in rows if r["polarity"] == "neg"), key=lambda r: r["risk"], reverse=True)
    by_platform: dict[str, dict] = {}
    for row in rows:
        platform = by_platform.setdefault(row["platform"], {"total": 0, "neg": 0})
        platform["total"] += 1
        platform["neg"] += row["polarity"] == "neg"
    topics: dict[str, int] = {}
    for row in negs:
        topics[row["topic_label"]] = topics.get(row["topic_label"], 0) + 1
    return {
        "n_total": n,
        "n_neg": len(negs),
        "neg_ratio": (len(negs) / n) if n else 0.0,
        "by_platform": by_platform,
        "top_neg": negs[:10],
        "top_topics": sorted(topics.items(), key=lambda item: item[1], reverse=True)[:5],
        "n_degraded_neg": sum(
            bool(json.loads(row.get("signals") or "{}").get("influence_degraded"))
            for row in negs
        ),
    }


def sov(store, watch: dict, *, since_day: str | None = None) -> list[dict]:
    """竞品对标：声量份额(SOV) + 净情绪(NSR=(正-负)/总)。自有 + 竞品同口径。"""
    rows = [dict(r) for r in store.joined_with_entities()]
    if since_day:
        rows = [
            row for row in rows
            if analytics.normalize_day(row.get("publish_ts"), row.get("fetched_at")) >= since_day
        ]
    per: dict[str, dict] = {}
    for row in rows:
        entity = per.setdefault(row["matched_entity_id"], {"n": 0, "pos": 0, "neg": 0})
        entity["n"] += 1
        entity["pos"] += row["polarity"] == "pos"
        entity["neg"] += row["polarity"] == "neg"
    total = sum(entity["n"] for entity in per.values()) or 1
    out = []
    for entity in watch["entities"]:
        counts = per.get(entity["id"], {"n": 0, "pos": 0, "neg": 0})
        out.append({
            "id": entity["id"],
            "name": (entity.get("aliases") or [entity["id"]])[0],
            "type": entity.get("type", "self"),
            "mentions": counts["n"],
            "sov": counts["n"] / total,
            "nsr": ((counts["pos"] - counts["neg"]) / counts["n"]) if counts["n"] else 0.0,
        })
    return sorted(out, key=lambda item: item["mentions"], reverse=True)


__all__ = ["aggregate", "sov"]
