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


def semantic_topics(store, entity_id: str, threshold: float = 0.8, min_size: int = 2) -> list[dict]:
    """负面话题语义归并（激活 embedding）：把"续航差"+"电池不耐用"归一簇，替代字符串精确归并。

    每簇 {size, sample(代表帖摘要), doc_ids, platforms}，按簇大小降序。
    无 embedding → 降级：按 topic_label 字符串分组（等价旧 rising_topics 的分桶口径）。
    """
    negs = [r for r in _rows(store, entity_id) if r.get("polarity") == "neg"]
    if not negs:
        return []
    from . import embed
    id2row = {r["doc_id"]: r for r in negs}
    groups: list[list[str]] = []
    semantic = False
    if embed.available():
        items = [(cid, embed.from_blob(b)) for cid, b in store.embeddings_for(entity_id)
                 if cid in id2row]
        if items:
            groups = embed.cluster(items, threshold=threshold)
            semantic = True
    if not semantic:                    # 降级：topic_label 字符串分桶
        bucket: dict[str, list[str]] = defaultdict(list)
        for r in negs:
            bucket[r.get("topic_label") or "未分类"].append(r["doc_id"])
        groups = list(bucket.values())
    out = []
    for g in groups:
        if len(g) < min_size:
            continue
        rows = [id2row[c] for c in g if c in id2row]
        if not rows:
            continue
        plats = sorted({r["platform"] for r in rows})
        out.append({"size": len(rows), "doc_ids": g, "platforms": plats,
                    "sample": (rows[0].get("summary") or rows[0].get("text") or "")[:44],
                    "semantic": semantic})
    return sorted(out, key=lambda x: x["size"], reverse=True)


def active_sample(store, entity_id: Optional[str] = None, limit: int = 20) -> list[dict]:
    """主动学习采样：从未标注帖里挑最值得人工标的代表样本。

    打分=不确定性(低置信)+两模型分歧+KOL影响+风险；再按 embedding 聚类去近重（水军复制帖折叠）。
    ★保留 ~30% 名额给 pos/neu 极性帖，保证"赞扬/中立/纯传播"立场也有样本可标（否则队列被负面淹没，
    Phase C 分层范例的正面档永远凑不齐）。返回每条附"采样原因"标签。降级：无向量→纯打分排序。
    """
    import math
    rows = [dict(r) for r in store.annotation_candidates(entity_id, limit=200)]
    if not rows:
        return []
    for r in rows:
        conf = r.get("confidence")
        conf = 0.5 if conf is None else conf
        unc = 1 - conf
        disagree = 1.0 if "cross_disagree" in (r.get("signals") or "") else 0.0
        fol = r.get("author_followers") or 0
        kol = min(math.log10(1 + max(fol, 0)) / 6.0, 1.0)     # ≈100万粉封顶
        risk_raw = r.get("risk") or 0
        r["_base"] = 0.35 * unc + 0.25 * disagree + 0.20 * kol + 0.20 * min(risk_raw / 50.0, 1.0)
        reasons = []
        if unc >= 0.4:
            reasons.append(f"低置信{conf:.2f}")
        if disagree:
            reasons.append("两模型分歧")
        if kol >= 0.7:
            reasons.append(f"高影响·粉{fol}")
        if risk_raw >= 30:
            reasons.append(f"高风险{int(risk_raw)}")
        r["_reason"] = "·".join(reasons) or "常规样本"

    rows.sort(key=lambda x: x["_base"], reverse=True)
    n_cover = max(1, int(limit * 0.3))                        # 立场覆盖名额
    hard = _diverse_pick(rows, limit - n_cover, cap=60)
    picked_ids = {r["doc_id"] for r in hard}
    pos_pool = [r for r in rows if r.get("polarity") in ("pos", "neu") and r["doc_id"] not in picked_ids]
    cover = _diverse_pick(pos_pool, n_cover, cap=40)

    out, seen = [], set()
    for r in hard + cover:
        if r["doc_id"] in seen:
            continue
        seen.add(r["doc_id"])
        out.append({"doc_id": r["doc_id"], "platform": r.get("platform"), "author": r.get("author"),
                    "author_followers": r.get("author_followers"), "text": r.get("text"),
                    "url": r.get("url"), "publish_ts": r.get("publish_ts"), "entity_id": r.get("entity_id"),
                    "polarity": r.get("polarity"), "confidence": r.get("confidence"), "risk": r.get("risk"),
                    "reason": r["_reason"]})
        if len(out) >= limit:
            break
    return out


def _diverse_pick(rows: list[dict], n: int, cap: int = 60) -> list[dict]:
    """从 base 降序的 rows 里取 n 条多样代表：对 top-cap 做 embedding 聚类，每簇取 base 最高。

    无向量的帖各成独立簇（不去重，符合"无法判相似"）。降级：全无向量→退化为 base 前 n。
    """
    if n <= 0 or not rows:
        return []
    from . import embed
    pool = rows[:cap]
    items = [(r["doc_id"], embed.from_blob(r["embedding"]) if r.get("embedding") else []) for r in pool]
    if not any(v for _, v in items):                          # 全无向量 → 纯 base 排序
        return pool[:n]
    by_id = {r["doc_id"]: r for r in pool}
    picks = []
    for cl in embed.cluster(items, threshold=0.85):
        rep = max((by_id[i] for i in cl), key=lambda x: x["_base"])   # 簇代表=base 最高
        picks.append(rep)
    picks.sort(key=lambda x: x["_base"], reverse=True)
    return picks[:n]


def suggest_targets(store, entity_id: str, aliases: list[str], must_not: list[str] | None = None,
                    top: int = 10, min_sim: float = 0.5) -> list[dict]:
    """监控目标语义扩展（激活 embedding）：从已采数据找与监控对象语义高相关、但当前别名未覆盖的
    高频词/话题簇 → 建议加入监控。人在环路：只出建议，绝不自动改 watch.yaml。

    做法：以别名短语为语义锚，对语义相似≥min_sim 但正文不含任何别名字面（=现有词汇漏掉的）的帖
    做语义聚类，每簇给代表帖+样例+平均相似度。无 embedding key → 返回 []（此能力纯语义，无降级）。
    """
    from . import embed
    if not embed.available():
        return []
    al = [(a or "").strip().lower() for a in (aliases or []) if (a or "").strip()]
    if not al:                          # 别名全空/空白 → 无有效锚，返回空（否则会拿垃圾向量刷一堆建议）
        return []
    mn = [(m or "").strip().lower() for m in (must_not or []) if (m or "").strip()]
    try:
        anchor = embed.embed_one("、".join([a for a in aliases if (a or "").strip()][:3]))
    except Exception:
        return []
    if not anchor:
        return []
    id2row = {r["doc_id"]: r for r in _rows(store, entity_id)}
    # 候选：语义相似≥min_sim，但正文不含别名字面（现有词汇过滤会漏的），且不含 must_not
    cands = []
    for cid, blob in store.embeddings_for(entity_id):
        r = id2row.get(cid)
        if not r:
            continue
        t = (r.get("text") or "").lower()
        if any(a in t for a in al) or any(m in t for m in mn):
            continue                    # 已被别名覆盖 / 被否定词排除，不算"新发现"
        sim = embed.cosine(anchor, embed.from_blob(blob))
        if sim >= min_sim:
            cands.append((cid, embed.from_blob(blob), sim))
    if not cands:
        return []
    groups = embed.cluster([(c, v) for c, v, _ in cands], threshold=0.8)
    sims = {c: s for c, _, s in cands}
    out = []
    for g in groups:
        rows = [id2row[c] for c in g if c in id2row]
        if not rows:
            continue
        avg = round(sum(sims.get(c, 0) for c in g) / len(g), 3)
        out.append({"size": len(rows), "avg_sim": avg,
                    "sample": (rows[0].get("summary") or rows[0].get("text") or "")[:50],
                    "platforms": sorted({r["platform"] for r in rows}),
                    "doc_ids": g})
    return sorted(out, key=lambda x: (x["size"], x["avg_sim"]), reverse=True)[:top]


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


def kol_ranking(store, entity_id: str, limit: int = 15, weights=None) -> list[dict]:
    """KOL/影响力榜（激活 author_followers）：按声量当量排高影响力发声者，分正负立场。

    同一作者多帖合并：声量当量求和、极性取多数。给运营看"谁在放大口碑/谁在带节奏"。
    """
    from .score import Weights, mention_equiv
    weights = weights or Weights()
    agg: dict[str, dict] = defaultdict(
        lambda: {"author": "", "followers": 0, "posts": 0, "pos": 0, "neg": 0,
                 "mention": 0.0, "platform": "", "url": "", "sample": ""})
    for r in _rows(store, entity_id):
        au = (r.get("author") or "").strip()
        if not au:
            continue
        k = f"{r['platform']}:{au}"
        d = agg[k]
        d["author"] = au
        d["platform"] = r["platform"]
        d["followers"] = max(d["followers"], r.get("author_followers") or 0)
        d["posts"] += 1
        if r.get("polarity") == "pos":
            d["pos"] += 1
        elif r.get("polarity") == "neg":
            d["neg"] += 1
        d["mention"] += mention_equiv(r, weights)
        if not d["url"]:
            d["url"], d["sample"] = r.get("url") or "", (r.get("summary") or r.get("text") or "")[:40]
    out = []
    for d in agg.values():
        d["mention"] = round(d["mention"], 2)
        d["stance"] = "负面" if d["neg"] > d["pos"] else "正面" if d["pos"] > d["neg"] else "中性"
        out.append(d)
    return sorted(out, key=lambda x: x["mention"], reverse=True)[:limit]


def aspect_trend(store, entity_id: str, split_day: str) -> list[dict]:
    """方面口碑环比：split_day 前后各方面负面占比对比，找恶化最快的方面（产品总监核心）。"""
    def _breakdown(rows):
        agg: dict[str, dict] = defaultdict(lambda: {"neg": 0, "n": 0})
        for r in rows:
            for a in json.loads(r["signals"] or "{}").get("aspects") or []:
                asp = a.get("aspect", "其他")
                agg[asp]["n"] += 1
                agg[asp]["neg"] += a.get("polarity") == "neg"
        return {k: (v["neg"] / v["n"] if v["n"] else 0.0) for k, v in agg.items()}
    rows = _rows(store, entity_id)
    before = _breakdown([r for r in rows if normalize_day(r.get("publish_ts"), r.get("fetched_at")) < split_day])
    after = _breakdown([r for r in rows if normalize_day(r.get("publish_ts"), r.get("fetched_at")) >= split_day])
    out = [{"aspect": a, "before": round(before.get(a, 0.0), 3), "after": round(after.get(a, 0.0), 3),
            "delta": round(after.get(a, 0.0) - before.get(a, 0.0), 3)}
           for a in set(before) | set(after)]
    return sorted(out, key=lambda x: x["delta"], reverse=True)


def aspect_platform_cross(store, watch: dict) -> dict:
    """方面×平台×实体(自有/竞品) 交叉：每个方面在各平台/各实体的负面占比。竞品方面级对标。"""
    out: dict = {"aspects": set(), "cells": {}}
    for ent in watch.get("entities", []):
        eid = ent["id"]
        agg: dict[tuple, dict] = defaultdict(lambda: {"neg": 0, "n": 0})
        for r in _rows(store, eid):
            for a in json.loads(r["signals"] or "{}").get("aspects") or []:
                asp = a.get("aspect", "其他")
                out["aspects"].add(asp)
                key = (asp, r["platform"])
                agg[key]["n"] += 1
                agg[key]["neg"] += a.get("polarity") == "neg"
        out["cells"][eid] = {f"{asp}|{plat}": round(v["neg"] / v["n"], 3) if v["n"] else 0.0
                             for (asp, plat), v in agg.items()}
    out["aspects"] = sorted(out["aspects"])
    return out


def _semantic_cluster_map(store, entity_id: str, threshold: float = 0.9) -> dict[str, int] | None:
    """用 embedding 语义聚类，返回 {doc_id: 簇号}。无 embedding key/无向量 → None（降级精确哈希）。

    threshold 高(0.9)：只把语义高度相似的归一簇（洗稿/改写=高相似，正常不同内容=低相似）。
    """
    from . import embed
    if not embed.available():
        return None
    items = [(cid, embed.from_blob(b)) for cid, b in store.embeddings_for(entity_id)]
    if not items:
        return None
    mapping: dict[str, int] = {}
    for i, members in enumerate(embed.cluster(items, threshold=threshold)):
        for cid in members:
            mapping[cid] = i
    return mapping


def suspicious_clusters(store, entity_id: str, min_size: int = 3) -> list[dict]:
    """异常账号簇：同一内容簇被多账号发布 → 疑似水军/搬运/控评。同簇跨≥min_size个不同作者=高度可疑。

    有 embedding → 语义聚类（挡洗稿/改写，"这盒子真好用"和"此盒子很好用"归一簇）；
    无 embedding → 降级精确哈希 content_cluster（只挡完全复制粘贴，采集层已算好）。接口不变。
    """
    sem_map = _semantic_cluster_map(store, entity_id)
    clusters: dict = defaultdict(
        lambda: {"authors": set(), "docs": [], "sample": "", "platforms": set()})
    for r in _rows(store, entity_id):
        key = sem_map.get(r["doc_id"]) if sem_map is not None else r.get("content_cluster")
        if key is None or key == "":
            continue
        c = clusters[key]
        c["authors"].add((r.get("author") or "").strip() or "?")
        c["docs"].append(r["doc_id"])
        c["platforms"].add(r["platform"])
        if not c["sample"]:
            c["sample"] = (r.get("text") or "")[:44]
    out = [{"cluster": str(cc), "n_authors": len(c["authors"]), "n_docs": len(c["docs"]),
            "platforms": sorted(c["platforms"]), "sample": c["sample"],
            "semantic": sem_map is not None}
           for cc, c in clusters.items() if len(c["authors"]) >= min_size]
    return sorted(out, key=lambda x: (x["n_authors"], x["n_docs"]), reverse=True)


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

    # 3-B KOL榜：高粉大V合并多帖、按声量当量排、立场判定
    ks = _S(":memory:")
    for i, (au, fol, pol) in enumerate([("大V", 3_000_000, "neg"), ("大V", 3_000_000, "neg"),
                                        ("素人", 50, "pos")]):
        d = _CD.build(platform="weibo", entity_id="e", native_id=f"k{i}", text="发热退款" if pol == "neg" else "好用",
                      author=au, author_followers=fol, likes=500, publish_ts="2026-07-01", fetched_at="2026-07-01T00:00:00")
        ks.add_clean(d); ks.add_feature(d.doc_id, {"polarity": pol})
    kol = kol_ranking(ks, "e")
    assert kol[0]["author"] == "大V" and kol[0]["posts"] == 2 and kol[0]["stance"] == "负面"
    assert kol[0]["mention"] > kol[1]["mention"]             # 大V声量当量 > 素人

    # 3-C 方面趋势：服务方面负面占比恶化
    at = aspect_trend(hn, "e", "2026-07-01")
    assert any(x["aspect"] == "服务" for x in at)
    cross = aspect_platform_cross(hn, {"entities": [{"id": "e", "type": "self"}]})
    assert "服务" in cross["aspects"] and "e" in cross["cells"]

    # 3-D 异常账号簇：同内容跨3作者=疑似水军
    ss2 = _S(":memory:")
    for i, au in enumerate(["号A", "号B", "号C"]):
        d = _CD.build(platform="weibo", entity_id="e", native_id=f"s{i}", text="一模一样的控评文案推荐买",
                      author=au, publish_ts="2026-07-01", fetched_at="2026-07-01T00:00:00")
        ss2.add_clean(d); ss2.add_feature(d.doc_id, {"polarity": "pos"})
    sus = suspicious_clusters(ss2, "e")
    assert sus and sus[0]["n_authors"] == 3, sus                # 同簇3个不同账号
    assert suspicious_clusters(ks, "e") == []                    # 正常数据无异常簇

    # V3 语义聚类：mock embedding，"续航差"和"电池不耐用"归一簇；洗稿识别为同簇
    import os as _os3
    from . import embed as _emb3
    _os3.environ["EMBED_API_KEY"] = "x"
    _vecmap = {"续航": [1.0, 0.0, 0.0], "电池": [0.97, 0.02, 0.0],   # 续航≈电池(语义近)
               "外观": [0.0, 0.0, 1.0]}
    def _vec_for(txt):
        for kw, v in _vecmap.items():
            if kw in txt:
                return v
        return [0.0, 1.0, 0.0]
    st3 = _S(":memory:")
    for i, txt in enumerate(["续航差电不耐用", "电池掉得快", "外观丑"]):
        d = _CD.build(platform="weibo", entity_id="e", native_id=f"t{i}", text=txt, publish_ts="2026-07-01", fetched_at="2026-07-01T00:00:00")
        st3.add_clean(d); st3.add_feature(d.doc_id, {"polarity": "neg", "topic_label": txt[:4]})
        st3.set_embedding(d.doc_id, _emb3.to_blob(_vec_for(txt)))
    st3.commit()
    stopics = semantic_topics(st3, "e", threshold=0.9, min_size=2)
    assert stopics and stopics[0]["size"] == 2 and stopics[0]["semantic"], stopics  # 续航+电池归一簇
    # V3-B 洗稿识别：语义相近的控评被判同簇（精确哈希挡不住）
    st4 = _S(":memory:")
    for i, (au, txt) in enumerate([("A", "这盒子真心好用推荐"), ("B", "此盒子确实好用值得推荐"),
                                   ("C", "该盒子很好用建议入手")]):   # 改写/洗稿，哈希不同但语义近
        d = _CD.build(platform="weibo", entity_id="e", native_id=f"w{i}", text=txt, author=au, publish_ts="2026-07-01", fetched_at="2026-07-01T00:00:00")
        st4.add_clean(d); st4.add_feature(d.doc_id, {"polarity": "pos"})
        st4.set_embedding(d.doc_id, _emb3.to_blob([1.0, 0.02 * i, 0.0]))   # 高度相似
    st4.commit()
    sus2 = suspicious_clusters(st4, "e", min_size=3)
    assert sus2 and sus2[0]["semantic"] and sus2[0]["n_authors"] == 3, sus2  # 洗稿被识别同簇
    _os3.environ.pop("EMBED_API_KEY")

    # V4 监控目标扩展：语义相关但不含别名的帖 → 建议清单（人工确认）
    st5 = _S(":memory:")
    _v = {"盒子": [1.0, 0.0], "机顶盒": [0.96, 0.05], "天气": [0.0, 1.0]}
    for i, txt in enumerate(["这机顶盒卡", "另一台机顶盒也卡", "今天天气好"]):
        d = _CD.build(platform="weibo", entity_id="e", native_id=f"g{i}", text=txt, publish_ts="2026-07-01", fetched_at="2026-07-01T00:00:00")
        st5.add_clean(d); st5.add_feature(d.doc_id, {"polarity": "neg"})
        st5.set_embedding(d.doc_id, _emb3.to_blob(_v["机顶盒"] if "机顶盒" in txt else _v["天气"]))
    st5.commit()
    _os3.environ["EMBED_API_KEY"] = "x"
    _emb3.embed_one = lambda t, **kw: _v["盒子"]              # 别名锚向量
    sug = suggest_targets(st5, "e", aliases=["盒子"], min_sim=0.5)
    assert sug and "机顶盒" in sug[0]["sample"] and sug[0]["size"] == 2, sug  # 建议"机顶盒"簇
    assert all("天气" not in x["sample"] for x in sug)         # 无关词不建议
    assert suggest_targets(st5, "e", aliases=[]) == []          # 无别名锚→空
    assert suggest_targets(st5, "e", aliases=["  ", ""]) == []   # 全空白别名→空(不拿垃圾向量刷建议)
    _os3.environ.pop("EMBED_API_KEY")
    _emb3.available = lambda: False                            # 硬置无 key（config.resolve 会读到本机真实 key，须 mock 才 hermetic）
    assert suggest_targets(st5, "e", aliases=["盒子"]) == []    # 无 key→空(纯语义能力)

    # active_sample 主动学习采样：近重折叠 + 正面覆盖 + 无向量降级
    from .store import Store as _SA, CleanDoc as _CDA
    from . import embed as _embA
    sa = _SA(":memory:")
    def _mk(nid, pol, conf, risk, fol, vec):
        d = _CDA.build(platform="weibo", entity_id="e", native_id=nid, text=nid, author="a" + nid)
        sa.add_clean(d)
        sa.conn.execute("UPDATE clean SET author_followers=?, embedding=? WHERE doc_id=?",
                        (fol, _embA.to_blob(vec), d.doc_id))
        sa.add_feature(d.doc_id, {"polarity": pol, "confidence": conf, "risk": risk, "signals": {}})
    _mk("n1", "neg", 0.3, 45, 120000, [1.0, 0.0, 0.0])
    _mk("n2", "neg", 0.3, 45, 120000, [0.99, 0.02, 0.0])       # 与 n1 近重
    _mk("n3", "neg", 0.35, 40, 500, [0.0, 1.0, 0.0])
    _mk("p1", "pos", 0.9, 0, 3000, [0.0, 0.0, 1.0])
    sa.commit()
    _res = active_sample(sa, "e", limit=4)
    _dup = sum(1 for r in _res if r["doc_id"] in
               [x[0] for x in sa.conn.execute("SELECT doc_id FROM clean WHERE native_id IN('n1','n2')")])
    assert _dup <= 1, "近重帖应聚类折叠"
    assert any(r["polarity"] == "pos" for r in _res), "应保留正面样本(立场覆盖)"
    sb = _SA(":memory:")                                        # 无向量降级
    for i in range(3):
        d = _CDA.build(platform="weibo", entity_id="e", native_id=f"z{i}", text="t", author="a")
        sb.add_clean(d); sb.add_feature(d.doc_id, {"polarity": "neg", "confidence": 0.3, "risk": 40, "signals": {}})
    sb.commit()
    assert len(active_sample(sb, "e", limit=5)) == 3, "无向量→纯打分排序"

    print(f"OK analytics: z-score+归一+时序 | BHI 好={bh_good['bhi']} 坏={bh_bad['bhi']}"
          f" | KOL/方面/交叉/异常簇 | V3语义(归并+洗稿) | V4目标扩展 | 主动学习采样 全通")
