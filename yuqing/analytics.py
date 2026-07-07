# -*- coding: utf-8 -*-
"""Phase 2 分析增强：稳健 z-score 异常、方面级口碑聚合、上升话题、日序列。

全部纯函数/只读，吃 clean⋈features，供报告与看板消费。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections import defaultdict
from statistics import median
from typing import Optional

_YMD = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")   # 带4位年，任意分隔
_MD = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.月]\s*(\d{1,2})(?!\d)")            # 无年，月日(前后非数字防误吞年份)
_NDAYS = re.compile(r"(\d+)\s*天前")


def normalize_day(publish_ts: str, fetched_at: str) -> str:
    """把各平台杂乱的发布时间归一成 YYYY-MM-DD；归不出则兜底用跑批日(fetched_at)。

    覆盖：带年(2026-04-16 / 2026.7.3 / 2025年12月30日 任意分隔)、无年月日(06-30 / 07月03日,借跑批年
    并做跨年回退)、相对时间(今天/刚刚/N小时前→跑批日; 昨天/前天/N天前→回退)。覆盖不到→兜底跑批日。
    """
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
    m = _YMD.search(ts)                      # 带 4 位年（任意分隔符）
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return _dt.date(y, mo, d).isoformat()
        except ValueError:
            return base
    m = _MD.search(ts)                       # 月-日 无年 → 借跑批年；落到跑批日之后则退一年(跨年帖)
    if m and ref is not None:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            day = _dt.date(ref.year, mo, d)
        except ValueError:
            return base
        if day > ref:                        # "12-30"在1月跑批→应是去年，退一年防未来日期污染趋势
            try:
                day = _dt.date(ref.year - 1, mo, d)
            except ValueError:
                return base
        return day.isoformat()
    return base


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


def daily_series(store, entity_id: Optional[str] = None, weights=None) -> list[dict]:
    """时序骨架：按**帖子发布日**聚合，每天 {day,total,pos,neg,neu,neg_ratio,mention,risk}，升序。

    mention=声量当量之和(跨平台可比,非简单计数)。所有趋势/BHI/异动都建在这上面。
    按发布日(normalize_day)而非跑批日 → 首次跑批就有多天趋势。
    """
    from .score import Weights, mention_equiv
    weights = weights or Weights()
    agg: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "pos": 0, "neg": 0, "neu": 0, "mention": 0.0,
                 "neg_mention": 0.0, "crisis": 0, "risk": 0.0})
    for r in _rows(store, entity_id):
        day = normalize_day(r.get("publish_ts"), r.get("fetched_at"))
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


def daily_negative_series(store, entity_id: str) -> list[tuple[str, int]]:
    """按**发布日**统计负面条数，升序。供异动基线（放量按真实发布日更准）。"""
    return [(d["day"], d["neg"]) for d in daily_series(store, entity_id) if d["neg"] > 0]


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


# 品牌健康指数 BHI（均衡型，0-100，越高越健康）。权重可调（config 旋钮）。
BHI_WEIGHTS = {"sentiment": 0.40, "volume": 0.20, "crisis": 0.30, "aspect": 0.10}


def _bhi_components(total, pos, neg, mention, neg_mention, crisis, worst_aspect_neg) -> dict:
    """四个 0-100 分量（越高越健康）。volume 用声量当量→爆款负面权重更高。"""
    nsr = (pos - neg) / total if total else 0.0
    sentiment = max(0.0, min(100.0, 50 + 50 * nsr))          # 净情绪 [-1,1]→[0,100]
    vol_neg = (neg_mention / mention) if mention else (neg / total if total else 0.0)
    volume = 100 * (1 - min(1.0, vol_neg))                   # 声量加权负面占比
    crisis_h = 100 * (1 - min(1.0, (crisis / total) if total else 0.0))   # 危机命中占比
    aspect_h = 100 * (1 - min(1.0, worst_aspect_neg))        # 最差方面拖累
    return {"sentiment": round(sentiment, 1), "volume": round(volume, 1),
            "crisis": round(crisis_h, 1), "aspect": round(aspect_h, 1)}


def _bhi_label(bhi) -> str:
    return "健康" if bhi >= 70 else "关注" if bhi >= 50 else "预警" if bhi >= 30 else "危机"


def brand_health(store, entity_id: str, weights=None, bhi_weights=None) -> dict:
    """当前品牌健康指数 BHI（0-100）+ 四分量 + 等级。这是给高管的单一可信分。"""
    from .score import Weights, mention_equiv
    weights = weights or Weights()
    bw = bhi_weights or BHI_WEIGHTS
    rows = _rows(store, entity_id)
    total = len(rows)
    if not total:
        return {"bhi": None, "label": "无数据", "components": {}, "n": 0, "crisis_neg": 0}
    pos = sum(r["polarity"] == "pos" for r in rows)
    neg = sum(r["polarity"] == "neg" for r in rows)
    mention = sum(mention_equiv(r, weights) for r in rows)
    neg_mention = sum(mention_equiv(r, weights) for r in rows if r["polarity"] == "neg")
    crisis = sum(1 for r in rows if r["polarity"] == "neg"
                 and json.loads(r["signals"] or "{}").get("crisis"))
    ab = aspect_breakdown(store, entity_id)
    worst = ab[0]["neg_ratio"] if ab else 0.0
    comp = _bhi_components(total, pos, neg, mention, neg_mention, crisis, worst)
    bhi = round(sum(comp[k] * bw[k] for k in bw), 1)
    return {"bhi": bhi, "label": _bhi_label(bhi), "components": comp, "n": total, "crisis_neg": crisis}


def bhi_trend(store, entity_id: str, weights=None, bhi_weights=None) -> list[dict]:
    """逐日 BHI 趋势。方面分量按天算太贵→per-day 略去(视为满分)。

    注意：因此趋势的 BHI 会比 brand_health 的整体 BHI 略高(最多约 方面权重×100=10分)——
    整体分含真实最差方面拖累、趋势不含。二者并列展示时，趋势按"不含方面"口径理解。
    """
    bw = bhi_weights or BHI_WEIGHTS
    out = []
    for d in daily_series(store, entity_id, weights):
        comp = _bhi_components(d["total"], d["pos"], d["neg"], d["mention"],
                               d["neg_mention"], d["crisis"], 0.0)
        comp["aspect"] = 100.0                               # per-day 不算方面，中性
        bhi = round(sum(comp[k] * bw[k] for k in bw), 1)
        out.append({"day": d["day"], "bhi": bhi, "total": d["total"]})
    return out


if __name__ == "__main__":
    assert robust_z([1, 1, 1, 1], 1) == 0.0                 # 恒定且无偏离
    assert robust_z([1, 2, 1, 2, 1], 10) >= 2.0             # 明显放量
    assert robust_z([1, 1, 1], 5) == 100.0                  # 恒定历史+偏离→强异常(不致盲)
    assert robust_z([], 5) == 0.0

    # normalize_day：各平台杂乱时间归一到发布日
    B = "2026-07-07"                                          # 跑批日兜底
    assert normalize_day("2026-04-16", B) == "2026-04-16"    # 小红书 ISO
    assert normalize_day("2026-5-30", B) == "2026-05-30"     # 贴吧 无补零
    assert normalize_day("07月03日 09:59", B) == "2026-07-03"  # 微博 月日借年
    assert normalize_day("06-30 16:25", B) == "2026-06-30"   # 值得买
    assert normalize_day("今天09:52", B) == B                 # 相对→跑批日
    assert normalize_day("3天前", B) == "2026-07-04"          # N天前
    assert normalize_day("昨天", B) == "2026-07-06"
    assert normalize_day("", B) == B and normalize_day("乱码格式", B) == B  # 空/未知→兜底
    # 年处理修复(finder 3项)：带年任意分隔用真年、点分格式、跨年回退防未来日
    assert normalize_day("2025年12月30日", B) == "2025-12-30"  # 带真年,不借跑批年
    assert normalize_day("2026.07.03", B) == "2026-07-03"     # 点分格式,不被 _MD 误吞年
    assert normalize_day("2026/7/3", B) == "2026-07-03"
    assert normalize_day("12-30 08:00", "2026-01-02T00:00:00") == "2025-12-30"  # 跨年退一年,非未来日
    assert normalize_day("06-30", B) == "2026-06-30"          # 无年月日仍借跑批年(不越过跑批日)

    # daily_series：按发布日聚合 情绪/声量当量/风险
    from .store import Store, CleanDoc
    s = Store(":memory:")
    for i, (day, pol, plays) in enumerate([("2026-07-01", "neg", 0), ("2026-07-01", "pos", 0),
                                            ("2026-07-03", "neg", 1_000_000)]):
        d = CleanDoc.build(platform="bilibili", entity_id="e", native_id=f"n{i}",
                           text="x", publish_ts=day, plays=plays, fetched_at="2026-07-07T00:00:00")
        s.add_clean(d)
        s.add_feature(d.doc_id, {"polarity": pol, "risk": 5.0 if pol == "neg" else 0.0})
    s.commit()
    ser = daily_series(s, "e")
    assert [x["day"] for x in ser] == ["2026-07-01", "2026-07-03"]        # 按发布日分桶
    assert ser[0]["total"] == 2 and ser[0]["neg"] == 1 and ser[0]["pos"] == 1
    assert ser[1]["mention"] > ser[0]["mention"]                          # 百万播放→声量当量更高
    assert daily_negative_series(s, "e") == [("2026-07-01", 1), ("2026-07-03", 1)]

    # BHI 品牌健康指数（均衡型）：全正面→高分，全危机负面→低分，趋势逐日
    from .store import Store as _S, CleanDoc as _CD
    hp = _S(":memory:")   # 全正面
    for i in range(4):
        d = _CD.build(platform="weibo", entity_id="e", native_id=f"p{i}", text="好用推荐", publish_ts="2026-07-01", likes=100, fetched_at="2026-07-01T00:00:00")
        hp.add_clean(d); hp.add_feature(d.doc_id, {"polarity": "pos", "signals": {"aspects": [{"aspect": "性能", "polarity": "pos"}]}})
    hp.commit()
    bh_good = brand_health(hp, "e")
    hn = _S(":memory:")   # 全危机负面
    for i in range(4):
        d = _CD.build(platform="heimao", entity_id="e", native_id=f"n{i}", text="退款维权避雷", publish_ts="2026-07-01", fetched_at="2026-07-01T00:00:00", is_complaint=True)
        hn.add_clean(d); hn.add_feature(d.doc_id, {"polarity": "neg", "risk": 5, "signals": {"crisis": True, "aspects": [{"aspect": "服务", "polarity": "neg"}]}})
    hn.commit()
    bh_bad = brand_health(hn, "e")
    assert bh_good["bhi"] > 70 and bh_good["label"] == "健康", bh_good
    assert bh_bad["bhi"] < 40 and bh_bad["label"] in ("预警", "危机"), bh_bad
    assert set(bh_good["components"]) == {"sentiment", "volume", "crisis", "aspect"}
    tr = bhi_trend(hn, "e")
    assert tr and tr[0]["day"] == "2026-07-01" and tr[0]["bhi"] < 50
    print(f"OK analytics: z-score+归一+时序 | BHI 好={bh_good['bhi']}({bh_good['label']}) 坏={bh_bad['bhi']}({bh_bad['label']})")
