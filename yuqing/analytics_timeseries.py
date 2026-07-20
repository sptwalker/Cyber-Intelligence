# -*- coding: utf-8 -*-
"""Date normalization and deterministic time-series read models.

This module owns the shared ``clean JOIN features`` row reader used by the
health, semantic, and learning analytics boundaries.  Public callers should
normally import these functions through :mod:`yuqing.analytics`, which keeps
the historical compatibility surface intact.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections import defaultdict
from typing import Optional

_YMD = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
_MD = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.月]\s*(\d{1,2})(?!\d)")
_NDAYS = re.compile(r"(\d+)\s*天前")


def normalize_day(publish_ts: str, fetched_at: str) -> str:
    """Normalize platform timestamps to ``YYYY-MM-DD``."""
    ts = (publish_ts or "").strip()
    base = (fetched_at or "")[:10]
    try:
        ref = _dt.date.fromisoformat(base) if len(base) == 10 else None
    except ValueError:
        ref = None
    if not ts:
        return base
    if ref is not None:
        if any(k in ts for k in ("今天", "刚刚", "小时前", "分钟前", "秒前")):
            return base
        if "前天" in ts:
            return (ref - _dt.timedelta(days=2)).isoformat()
        if "昨天" in ts:
            return (ref - _dt.timedelta(days=1)).isoformat()
        m = _NDAYS.search(ts)
        if m:
            return (ref - _dt.timedelta(days=int(m.group(1)))).isoformat()
    m = _YMD.search(ts)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return _dt.date(y, mo, d).isoformat()
        except ValueError:
            return base
    m = _MD.search(ts)
    if m and ref is not None:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            day = _dt.date(ref.year, mo, d)
        except ValueError:
            return base
        if day > ref:
            try:
                day = _dt.date(ref.year - 1, mo, d)
            except ValueError:
                return base
        return day.isoformat()
    return base


def _rows(store, entity_id: Optional[str] = None) -> list[dict]:
    return [dict(r) for r in store.joined(entity_id)]


def daily_series(
    store, entity_id: Optional[str] = None, weights=None, *,
    _rows_fn=None, _normalize_day_fn=None,
) -> list[dict]:
    """Aggregate sentiment, mention-equivalent volume, and risk by publish day."""
    from .score import Weights, mention_equiv

    rows_fn = _rows_fn or _rows
    normalize = _normalize_day_fn or normalize_day
    weights = weights or Weights()
    agg: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "pos": 0, "neg": 0, "neu": 0, "mention": 0.0,
                 "neg_mention": 0.0, "crisis": 0, "risk": 0.0})
    for r in rows_fn(store, entity_id):
        day = normalize(r.get("publish_ts"), r.get("fetched_at"))
        if not (len(day) == 10 and day[4] == "-"):
            continue
        d = agg[day]
        d["total"] += 1
        pol = r.get("polarity")
        if pol in ("pos", "neg", "neu"):
            d[pol] += 1
        me = mention_equiv(r, weights)
        d["mention"] += me
        if pol == "neg":
            d["neg_mention"] += me
            if json.loads(r["signals"] or "{}").get("crisis"):
                d["crisis"] += 1
        d["risk"] += r.get("risk") or 0.0
    out = []
    for day in sorted(agg):
        d = agg[day]
        d["day"] = day
        d["neg_ratio"] = d["neg"] / d["total"] if d["total"] else 0.0
        d["mention"] = round(d["mention"], 2)
        d["neg_mention"] = round(d["neg_mention"], 2)
        d["risk"] = round(d["risk"], 2)
        out.append(d)
    return out


def daily_negative_series(
    store, entity_id: str, *, _daily_series_fn=None,
) -> list[tuple[str, int]]:
    daily = _daily_series_fn or daily_series
    return [(d["day"], d["neg"]) for d in daily(store, entity_id) if d["neg"] > 0]


def aspect_breakdown(
    store, entity_id: str, *, since_day: str | None = None,
    _rows_fn=None, _normalize_day_fn=None,
) -> list[dict]:
    """Aggregate positive, negative, and neutral aspect sentiment."""
    rows_fn = _rows_fn or _rows
    normalize = _normalize_day_fn or normalize_day
    agg: dict[str, dict] = defaultdict(lambda: {"pos": 0, "neg": 0, "neu": 0})
    rows = rows_fn(store, entity_id)
    if since_day:
        rows = [
            row for row in rows
            if normalize(row.get("publish_ts"), row.get("fetched_at")) >= since_day
        ]
    for r in rows:
        for a in json.loads(r["signals"] or "{}").get("aspects") or []:
            pol = a.get("polarity", "neu")
            if pol in ("pos", "neg", "neu"):
                agg[a.get("aspect", "其他")][pol] += 1
    out = []
    for aspect, counts in agg.items():
        n = counts["pos"] + counts["neg"] + counts["neu"]
        out.append({"aspect": aspect, **counts, "n": n,
                    "neg_ratio": counts["neg"] / n if n else 0.0})
    return sorted(out, key=lambda x: (x["neg_ratio"], x["neg"]), reverse=True)


def rising_topics(
    store, entity_id: str, split_day: str, *, since_day: str | None = None,
    _rows_fn=None, _normalize_day_fn=None,
) -> list[dict]:
    """Compare topic counts before and after a batch split day."""
    rows_fn = _rows_fn or _rows
    normalize = _normalize_day_fn or normalize_day
    before: dict[str, int] = defaultdict(int)
    after: dict[str, int] = defaultdict(int)
    rows = rows_fn(store, entity_id)
    if since_day:
        rows = [
            row for row in rows
            if normalize(row.get("publish_ts"), row.get("fetched_at")) >= since_day
        ]
    for r in rows:
        topic = r["topic_label"] or "未分类"
        (after if (r["fetched_at"] or "")[:10] >= split_day else before)[topic] += 1
    topics = set(before) | set(after)
    out = [{"topic": topic, "before": before[topic], "after": after[topic],
            "delta": after[topic] - before[topic]} for topic in topics]
    return sorted([item for item in out if item["delta"] > 0],
                  key=lambda item: item["delta"], reverse=True)


def kol_ranking(store, entity_id: str, limit: int = 15, weights=None, *, _rows_fn=None) -> list[dict]:
    """Rank authors by mention-equivalent reach and sentiment stance."""
    from .score import Weights, mention_equiv

    rows_fn = _rows_fn or _rows
    weights = weights or Weights()
    agg: dict[str, dict] = defaultdict(
        lambda: {"author": "", "followers": 0, "posts": 0, "pos": 0, "neg": 0,
                 "mention": 0.0, "platform": "", "url": "", "sample": ""})
    for r in rows_fn(store, entity_id):
        author = (r.get("author") or "").strip()
        if not author:
            continue
        key = f"{r['platform']}:{author}"
        d = agg[key]
        d["author"] = author
        d["platform"] = r["platform"]
        d["followers"] = max(d["followers"], r.get("author_followers") or 0)
        d["posts"] += 1
        if r.get("polarity") == "pos":
            d["pos"] += 1
        elif r.get("polarity") == "neg":
            d["neg"] += 1
        d["mention"] += mention_equiv(r, weights)
        if not d["url"]:
            d["url"] = r.get("url") or ""
            d["sample"] = (r.get("summary") or r.get("text") or "")[:40]
    out = []
    for d in agg.values():
        d["mention"] = round(d["mention"], 2)
        d["stance"] = "负面" if d["neg"] > d["pos"] else "正面" if d["pos"] > d["neg"] else "中性"
        out.append(d)
    return sorted(out, key=lambda x: x["mention"], reverse=True)[:limit]


def aspect_trend(store, entity_id: str, split_day: str, *, _rows_fn=None, _normalize_day_fn=None) -> list[dict]:
    """Compare aspect negative ratios before and after a split day."""
    rows_fn = _rows_fn or _rows
    normalize = _normalize_day_fn or normalize_day

    def breakdown(rows):
        agg: dict[str, dict] = defaultdict(lambda: {"neg": 0, "n": 0})
        for r in rows:
            for a in json.loads(r["signals"] or "{}").get("aspects") or []:
                aspect = a.get("aspect", "其他")
                agg[aspect]["n"] += 1
                agg[aspect]["neg"] += a.get("polarity") == "neg"
        return {k: (v["neg"] / v["n"] if v["n"] else 0.0) for k, v in agg.items()}

    rows = rows_fn(store, entity_id)
    before = breakdown([r for r in rows if normalize(r.get("publish_ts"), r.get("fetched_at")) < split_day])
    after = breakdown([r for r in rows if normalize(r.get("publish_ts"), r.get("fetched_at")) >= split_day])
    out = [{"aspect": aspect, "before": round(before.get(aspect, 0.0), 3),
            "after": round(after.get(aspect, 0.0), 3),
            "delta": round(after.get(aspect, 0.0) - before.get(aspect, 0.0), 3)}
           for aspect in set(before) | set(after)]
    return sorted(out, key=lambda x: x["delta"], reverse=True)


def aspect_platform_cross(store, watch: dict, *, _rows_fn=None) -> dict:
    """Build aspect × platform × entity negative-ratio cells."""
    rows_fn = _rows_fn or _rows
    out: dict = {"aspects": set(), "cells": {}}
    for ent in watch.get("entities", []):
        eid = ent["id"]
        agg: dict[tuple, dict] = defaultdict(lambda: {"neg": 0, "n": 0})
        for r in rows_fn(store, eid):
            for a in json.loads(r["signals"] or "{}").get("aspects") or []:
                aspect = a.get("aspect", "其他")
                out["aspects"].add(aspect)
                key = (aspect, r["platform"])
                agg[key]["n"] += 1
                agg[key]["neg"] += a.get("polarity") == "neg"
        out["cells"][eid] = {
            f"{aspect}|{platform}": round(value["neg"] / value["n"], 3) if value["n"] else 0.0
            for (aspect, platform), value in agg.items()
        }
    out["aspects"] = sorted(out["aspects"])
    return out
