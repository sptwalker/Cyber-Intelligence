# -*- coding: utf-8 -*-
"""Phase 2 分析增强：稳健 z-score 异常、方面级口碑聚合、上升话题、日序列。

全部纯函数/只读，吃 clean⋈features，供报告与看板消费。
"""

from __future__ import annotations

import json
from collections import defaultdict
from statistics import median
from typing import Optional


def robust_z(history: list[float], x: float) -> float:
    """中位数+MAD 的稳健 z 分（抗历史尖峰污染，优于 mean/std）。

    z = (x−median)/std_est，std_est = 1.4826·MAD。MAD=0（低方差历史）时退回
    平均绝对离差；历史完全恒定时，任何偏离视为强异常（±100），避免 MAD=0 致盲。
    """
    if not history:
        return 0.0
    med = median(history)
    devs = [abs(h - med) for h in history]
    mad = median(devs)
    if mad > 0:
        return round((x - med) / (1.4826 * mad), 2)
    mean_ad = sum(devs) / len(devs)
    if mean_ad > 0:
        return round((x - med) / (1.2533 * mean_ad), 2)
    return 0.0 if x == med else (100.0 if x > med else -100.0)


def _rows(store, entity_id: Optional[str] = None) -> list[dict]:
    return [dict(r) for r in store.joined(entity_id)]


def daily_negative_series(store, entity_id: str) -> list[tuple[str, int]]:
    """按天(取 fetched_at 前10位)统计负面条数，升序。供时序看板/基线。

    只计有效 ISO 日期；无日期(空 fetched_at)的行跳过——否则 'unknown' 桶会排到
    最后被误当成"最新一天"，既造假异常又漏掉真正最新日的异常。
    """
    by_day: dict[str, int] = defaultdict(int)
    for r in _rows(store, entity_id):
        if r["polarity"] == "neg":
            day = (r["fetched_at"] or "")[:10]
            if len(day) == 10 and day[4] == "-":
                by_day[day] += 1
    return sorted(by_day.items())


MIN_ANOMALY_COUNT = 5   # 绝对下限护栏：负面数太少不判异常（避免 3→6 的假放量）


def negative_anomaly(store, entity_id: str) -> dict:
    """最新一天的负面量相对历史的稳健 z 分（z≥2 且绝对数≥下限才算异常放量）。"""
    series = daily_negative_series(store, entity_id)
    if not series:
        return {"day": None, "count": 0, "z": 0.0, "anomaly": False}
    day, count = series[-1]
    z = robust_z([c for _, c in series[:-1]], count)
    return {"day": day, "count": count, "z": z,
            "anomaly": z >= 2.0 and count >= MIN_ANOMALY_COUNT}


def aspect_breakdown(store, entity_id: str) -> list[dict]:
    """方面级口碑：每个方面的正/负/中计数与负面占比，按负面占比降序。"""
    agg: dict[str, dict] = defaultdict(lambda: {"pos": 0, "neg": 0, "neu": 0})
    for r in _rows(store, entity_id):
        for a in json.loads(r["signals"] or "{}").get("aspects") or []:  # aspects 可能为 null
            pol = a.get("polarity", "neu")
            if pol in ("pos", "neg", "neu"):
                agg[a.get("aspect", "其他")][pol] += 1
    out = []
    for aspect, c in agg.items():
        n = c["pos"] + c["neg"] + c["neu"]
        out.append({"aspect": aspect, **c, "n": n, "neg_ratio": c["neg"] / n if n else 0.0})
    return sorted(out, key=lambda x: (x["neg_ratio"], x["neg"]), reverse=True)


def rising_topics(store, entity_id: str, split_day: str) -> list[dict]:
    """上升话题：split_day 及以后 vs 之前，按话题计数增量降序（放量=苗头）。

    split_day 为 'YYYY-MM-DD'；用 fetched_at 前10位分前后窗。
    """
    before: dict[str, int] = defaultdict(int)
    after: dict[str, int] = defaultdict(int)
    for r in _rows(store, entity_id):
        t = r["topic_label"] or "未分类"
        (after if (r["fetched_at"] or "")[:10] >= split_day else before)[t] += 1
    topics = set(before) | set(after)
    out = [{"topic": t, "before": before[t], "after": after[t],
            "delta": after[t] - before[t]} for t in topics]
    return sorted([x for x in out if x["delta"] > 0], key=lambda x: x["delta"], reverse=True)


if __name__ == "__main__":
    assert robust_z([1, 1, 1, 1], 1) == 0.0                 # 恒定且无偏离
    assert robust_z([1, 2, 1, 2, 1], 10) >= 2.0             # 明显放量
    assert robust_z([1, 1, 1], 5) == 100.0                  # 恒定历史+偏离→强异常(不致盲)
    assert robust_z([], 5) == 0.0
    print("OK analytics: 稳健z-score 生效")
