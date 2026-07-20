# -*- coding: utf-8 -*-
"""Health scores and anomaly detection built on time-series read models."""

from __future__ import annotations

import json
from statistics import median

from . import analytics_timeseries as _timeseries

MIN_ANOMALY_COUNT = 5
BHI_WEIGHTS = {"sentiment": 0.40, "volume": 0.20, "crisis": 0.30, "aspect": 0.10}


def robust_z(history: list[float], x: float) -> float:
    """Return a robust median/MAD z-score."""
    if not history:
        return 0.0
    med = median(history)
    deviations = [abs(h - med) for h in history]
    mad = median(deviations)
    if mad > 0:
        return round((x - med) / (1.4826 * mad), 2)
    mean_ad = sum(deviations) / len(deviations)
    if mean_ad > 0:
        return round((x - med) / (1.2533 * mean_ad), 2)
    return 0.0 if x == med else (100.0 if x > med else -100.0)


def negative_anomaly(
    store, entity_id: str, *, since_day: str | None = None,
    _daily_negative_series_fn=None, _robust_z_fn=None, _min_anomaly_count: int | None = None,
) -> dict:
    daily_negative_series = _daily_negative_series_fn or _timeseries.daily_negative_series
    z_score = _robust_z_fn or robust_z
    min_count = MIN_ANOMALY_COUNT if _min_anomaly_count is None else _min_anomaly_count
    series = daily_negative_series(store, entity_id)
    if since_day:
        series = [(day, count) for day, count in series if day >= since_day]
    if not series:
        return {"day": None, "count": 0, "z": 0.0, "anomaly": False}
    day, count = series[-1]
    z = z_score([c for _, c in series[:-1]], count)
    return {"day": day, "count": count, "z": z,
            "anomaly": z >= 2.0 and count >= min_count}


def _bhi_components(total, pos, neg, mention, neg_mention, crisis, worst_aspect_neg) -> dict:
    nsr = (pos - neg) / total if total else 0.0
    sentiment = max(0.0, min(100.0, 50 + 50 * nsr))
    vol_neg = (neg_mention / mention) if mention else (neg / total if total else 0.0)
    volume = 100 * (1 - min(1.0, vol_neg))
    crisis_h = 100 * (1 - min(1.0, (crisis / total) if total else 0.0))
    aspect_h = 100 * (1 - min(1.0, worst_aspect_neg))
    return {"sentiment": round(sentiment, 1), "volume": round(volume, 1),
            "crisis": round(crisis_h, 1), "aspect": round(aspect_h, 1)}


def _bhi_label(bhi) -> str:
    return "健康" if bhi >= 70 else "关注" if bhi >= 50 else "预警" if bhi >= 30 else "危机"


def brand_health(
    store, entity_id: str, weights=None, bhi_weights=None, *, since_day: str | None = None,
    _rows_fn=None, _normalize_day_fn=None, _aspect_breakdown_fn=None,
    _bhi_components_fn=None, _bhi_label_fn=None,
) -> dict:
    """Calculate the current weighted brand-health index."""
    from .score import Weights, mention_equiv

    rows_fn = _rows_fn or _timeseries._rows
    normalize = _normalize_day_fn or _timeseries.normalize_day
    aspect_breakdown = _aspect_breakdown_fn or _timeseries.aspect_breakdown
    build_components = _bhi_components_fn or _bhi_components
    label_for = _bhi_label_fn or _bhi_label
    weights = weights or Weights()
    bw = bhi_weights or BHI_WEIGHTS
    rows = rows_fn(store, entity_id)
    if since_day:
        rows = [
            row for row in rows
            if normalize(row.get("publish_ts"), row.get("fetched_at")) >= since_day
        ]
    total = len(rows)
    if not total:
        return {"bhi": None, "label": "无数据", "components": {}, "n": 0, "crisis_neg": 0}
    pos = sum(r["polarity"] == "pos" for r in rows)
    neg = sum(r["polarity"] == "neg" for r in rows)
    mention = sum(mention_equiv(r, weights) for r in rows)
    neg_mention = sum(mention_equiv(r, weights) for r in rows if r["polarity"] == "neg")
    crisis = sum(1 for r in rows if r["polarity"] == "neg"
                 and json.loads(r["signals"] or "{}").get("crisis"))
    aspects = aspect_breakdown(store, entity_id, since_day=since_day)
    worst = aspects[0]["neg_ratio"] if aspects else 0.0
    components = build_components(total, pos, neg, mention, neg_mention, crisis, worst)
    bhi = round(sum(components[k] * bw[k] for k in bw), 1)
    return {"bhi": bhi, "label": label_for(bhi), "components": components,
            "n": total, "crisis_neg": crisis}


def bhi_trend(
    store, entity_id: str, weights=None, bhi_weights=None, *, _daily_series_fn=None,
    _bhi_components_fn=None,
) -> list[dict]:
    """Calculate per-day BHI trend (aspect component is neutral per day)."""
    daily_series = _daily_series_fn or _timeseries.daily_series
    build_components = _bhi_components_fn or _bhi_components
    bw = bhi_weights or BHI_WEIGHTS
    out = []
    for d in daily_series(store, entity_id, weights):
        components = build_components(d["total"], d["pos"], d["neg"], d["mention"],
                                      d["neg_mention"], d["crisis"], 0.0)
        components["aspect"] = 100.0
        bhi = round(sum(components[k] * bw[k] for k in bw), 1)
        out.append({"day": d["day"], "bhi": bhi, "total": d["total"]})
    return out
