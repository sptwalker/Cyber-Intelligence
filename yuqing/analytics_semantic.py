# -*- coding: utf-8 -*-
"""Embedding-backed semantic clustering and monitoring-target discovery."""

from __future__ import annotations

from collections import defaultdict

from . import analytics_timeseries as _timeseries


def semantic_topics(
    store, entity_id: str, threshold: float = 0.8, min_size: int = 2,
    *, since_day: str | None = None, _rows_fn=None, _normalize_day_fn=None,
) -> list[dict]:
    """Cluster negative topics semantically, with a deterministic label fallback."""
    rows_fn = _rows_fn or _timeseries._rows
    normalize = _normalize_day_fn or _timeseries.normalize_day
    negatives = [r for r in rows_fn(store, entity_id) if r.get("polarity") == "neg"]
    if since_day:
        negatives = [
            row for row in negatives
            if normalize(row.get("publish_ts"), row.get("fetched_at")) >= since_day
        ]
    if not negatives:
        return []
    from . import embed

    id2row = {r["doc_id"]: r for r in negatives}
    groups: list[list[str]] = []
    semantic = False
    if embed.available():
        items = [(cid, embed.from_blob(blob)) for cid, blob in store.embeddings_for(entity_id)
                 if cid in id2row]
        if items:
            groups = embed.cluster(items, threshold=threshold)
            semantic = True
    if not semantic:
        bucket: dict[str, list[str]] = defaultdict(list)
        for row in negatives:
            bucket[row.get("topic_label") or "未分类"].append(row["doc_id"])
        groups = list(bucket.values())
    out = []
    for group in groups:
        if len(group) < min_size:
            continue
        rows = [id2row[cid] for cid in group if cid in id2row]
        if not rows:
            continue
        out.append({"size": len(rows), "doc_ids": group,
                    "platforms": sorted({r["platform"] for r in rows}),
                    "sample": (rows[0].get("summary") or rows[0].get("text") or "")[:44],
                    "semantic": semantic})
    return sorted(out, key=lambda x: x["size"], reverse=True)


def suggest_targets(
    store, entity_id: str, aliases: list[str], must_not: list[str] | None = None,
    top: int = 10, min_sim: float = 0.5, *, _rows_fn=None,
) -> list[dict]:
    """Suggest semantically related terms not covered by current aliases."""
    from . import embed

    if not embed.available():
        return []
    al = [(a or "").strip().lower() for a in (aliases or []) if (a or "").strip()]
    if not al:
        return []
    mn = [(m or "").strip().lower() for m in (must_not or []) if (m or "").strip()]
    try:
        anchor = embed.embed_one("、".join([a for a in aliases if (a or "").strip()][:3]))
    except Exception:
        return []
    if not anchor:
        return []
    rows_fn = _rows_fn or _timeseries._rows
    id2row = {r["doc_id"]: r for r in rows_fn(store, entity_id)}
    candidates = []
    for cid, blob in store.embeddings_for(entity_id):
        row = id2row.get(cid)
        if not row:
            continue
        text = (row.get("text") or "").lower()
        if any(alias in text for alias in al) or any(term in text for term in mn):
            continue
        vector = embed.from_blob(blob)
        similarity = embed.cosine(anchor, vector)
        if similarity >= min_sim:
            candidates.append((cid, vector, similarity))
    if not candidates:
        return []
    groups = embed.cluster([(cid, vector) for cid, vector, _ in candidates], threshold=0.8)
    similarities = {cid: similarity for cid, _, similarity in candidates}
    out = []
    for group in groups:
        rows = [id2row[cid] for cid in group if cid in id2row]
        if not rows:
            continue
        average = round(sum(similarities.get(cid, 0) for cid in group) / len(group), 3)
        out.append({"size": len(rows), "avg_sim": average,
                    "sample": (rows[0].get("summary") or rows[0].get("text") or "")[:50],
                    "platforms": sorted({row["platform"] for row in rows}),
                    "doc_ids": group})
    return sorted(out, key=lambda x: (x["size"], x["avg_sim"]), reverse=True)[:top]


def _semantic_cluster_map(store, entity_id: str, threshold: float = 0.9) -> dict[str, int] | None:
    from . import embed

    if not embed.available():
        return None
    items = [(cid, embed.from_blob(blob)) for cid, blob in store.embeddings_for(entity_id)]
    if not items:
        return None
    mapping: dict[str, int] = {}
    for index, members in enumerate(embed.cluster(items, threshold=threshold)):
        for cid in members:
            mapping[cid] = index
    return mapping


def suspicious_clusters(
    store, entity_id: str, min_size: int = 3, *,
    _semantic_cluster_map_fn=None, _rows_fn=None,
) -> list[dict]:
    """Find content clusters published by at least ``min_size`` authors."""
    cluster_map_fn = _semantic_cluster_map_fn or _semantic_cluster_map
    rows_fn = _rows_fn or _timeseries._rows
    semantic_map = cluster_map_fn(store, entity_id)
    clusters: dict = defaultdict(
        lambda: {"authors": set(), "docs": [], "sample": "", "platforms": set()})
    for row in rows_fn(store, entity_id):
        key = semantic_map.get(row["doc_id"]) if semantic_map is not None else row.get("content_cluster")
        if key is None or key == "":
            continue
        cluster = clusters[key]
        cluster["authors"].add((row.get("author") or "").strip() or "?")
        cluster["docs"].append(row["doc_id"])
        cluster["platforms"].add(row["platform"])
        if not cluster["sample"]:
            cluster["sample"] = (row.get("text") or "")[:44]
    out = [{"cluster": str(key), "n_authors": len(cluster["authors"]),
            "n_docs": len(cluster["docs"]), "platforms": sorted(cluster["platforms"]),
            "sample": cluster["sample"], "semantic": semantic_map is not None}
           for key, cluster in clusters.items() if len(cluster["authors"]) >= min_size]
    return sorted(out, key=lambda x: (x["n_authors"], x["n_docs"]), reverse=True)
