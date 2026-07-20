# -*- coding: utf-8 -*-
"""Active-learning sampling and keyword/alias discovery workflows."""

from __future__ import annotations

import json
import math
import re
import shutil

from .watch_config import validate_watch, watch_path

STOPWORDS = {
    "这个", "那个", "真的", "感觉", "现在", "已经", "还是", "就是", "可以", "什么", "怎么",
    "他们", "我们", "自己", "东西", "时候", "问题", "产品", "手机", "游戏", "视频", "大家",
    "一个", "有点", "非常", "特别", "还有", "但是", "所以", "因为", "如果", "这么", "那么",
    "购买", "使用", "评测", "开箱", "分享", "推荐", "体验", "官方", "发布",
}


def active_sample(
    store, entity_id: str | None = None, limit: int = 20, *, _diverse_pick_fn=None,
) -> list[dict]:
    """Select uncertain, influential, and diverse samples for human labeling."""
    rows = [dict(row) for row in store.annotation_candidates(entity_id, limit=200)]
    if not rows:
        return []
    for row in rows:
        if entity_id:
            row["entity_id"] = entity_id
        confidence = row.get("confidence")
        confidence = 0.5 if confidence is None else confidence
        uncertainty = 1 - confidence
        disagree = 1.0 if "cross_disagree" in (row.get("signals") or "") else 0.0
        followers = row.get("author_followers") or 0
        kol = min(math.log10(1 + max(followers, 0)) / 6.0, 1.0)
        risk_raw = row.get("risk") or 0
        row["_base"] = (0.35 * uncertainty + 0.25 * disagree + 0.20 * kol
                         + 0.20 * min(risk_raw / 50.0, 1.0))
        reasons = []
        if uncertainty >= 0.4:
            reasons.append(f"低置信{confidence:.2f}")
        if disagree:
            reasons.append("两模型分歧")
        if kol >= 0.7:
            reasons.append(f"高影响·粉{followers}")
        if risk_raw >= 30:
            reasons.append(f"高风险{int(risk_raw)}")
        row["_reason"] = "·".join(reasons) or "常规样本"
    rows.sort(key=lambda row: row["_base"], reverse=True)
    diverse_pick = _diverse_pick_fn or _diverse_pick
    n_cover = max(1, int(limit * 0.3))
    hard = diverse_pick(rows, limit - n_cover, cap=60)
    picked_ids = {row["doc_id"] for row in hard}
    pos_pool = [row for row in rows if row.get("polarity") in ("pos", "neu")
                and row["doc_id"] not in picked_ids]
    cover = diverse_pick(pos_pool, n_cover, cap=40)

    out, seen = [], set()
    for row in hard + cover:
        if row["doc_id"] in seen:
            continue
        seen.add(row["doc_id"])
        out.append({"doc_id": row["doc_id"], "platform": row.get("platform"),
                    "author": row.get("author"), "author_followers": row.get("author_followers"),
                    "text": row.get("text"), "url": row.get("url"),
                    "publish_ts": row.get("publish_ts"), "entity_id": row.get("entity_id"),
                    "polarity": row.get("polarity"), "confidence": row.get("confidence"),
                    "risk": row.get("risk"), "reason": row["_reason"]})
        if len(out) >= limit:
            break
    return out


def _diverse_pick(rows: list[dict], n: int, cap: int = 60) -> list[dict]:
    if n <= 0 or not rows:
        return []
    from . import embed

    pool = rows[:cap]
    items = [(row["doc_id"], embed.from_blob(row["embedding"]) if row.get("embedding") else [])
             for row in pool]
    if not any(vector for _, vector in items):
        return pool[:n]
    by_id = {row["doc_id"]: row for row in pool}
    picks = []
    for cluster in embed.cluster(items, threshold=0.85):
        representative = max((by_id[cid] for cid in cluster), key=lambda row: row["_base"])
        picks.append(representative)
    picks.sort(key=lambda row: row["_base"], reverse=True)
    return picks[:n]


def _ngrams(text: str, lo: int = 2, hi: int = 4) -> set:
    out = set()
    segment = ""
    for ch in (text or "")[:200]:
        if "一" <= ch <= "鿿" or ch.isalnum():
            segment += ch
        else:
            segment = ""
            continue
        for n in range(lo, hi + 1):
            if len(segment) >= n:
                out.add(segment[-n:])
    return out


def extract_seed_candidates(
    store, entity_id: str, aliases: list[str], must_not=None,
    min_sim: float = 0.5, min_df: int = 2, min_lift: float = 3.0,
    min_score: float = 0.5, km=None, *, _suggest_targets_fn=None,
    _stopwords=None, _ngrams_fn=None,
) -> list[dict]:
    """Mine semantically relevant terms and route them to feature/seed queues."""
    from .keywords import KeywordManager

    km = km or KeywordManager(store)
    suggest_targets = _suggest_targets_fn
    if suggest_targets is None:
        from . import analytics_semantic
        suggest_targets = analytics_semantic.suggest_targets
    clusters = suggest_targets(store, entity_id, aliases, must_not, min_sim=min_sim)
    if not clusters:
        return []
    corpus = [dict(row) for row in store.joined(entity_id)]
    total_docs = len(corpus) or 1
    document_frequency: dict[str, int] = {}
    stance_words = set()
    for word in (km.get_complaints(entity_id) + km.get_selling_points(entity_id)
                 + km.get_competitors(entity_id) + km.get_by_tag("stopword", entity_id)):
        stance_words.add(word["word"])
    stop = (_stopwords if _stopwords is not None else STOPWORDS) | {
        word["word"] for word in km.get_by_tag("stopword", entity_id)
    }
    tag_of = {
        "complaint": {word["word"] for word in km.get_complaints(entity_id)},
        "selling_point": {word["word"] for word in km.get_selling_points(entity_id)},
        "competitor": {word["word"] for word in km.get_competitors(entity_id)},
    }
    ngrams = _ngrams_fn or _ngrams

    def document_frequency_for(word):
        if word not in document_frequency:
            document_frequency[word] = sum(1 for row in corpus if word in (row.get("text") or ""))
        return document_frequency[word]

    out, seen = [], set()
    for cluster in clusters:
        texts = [next((row for row in corpus if row["doc_id"] == doc_id), None)
                 for doc_id in cluster["doc_ids"]]
        texts = [row for row in texts if row]
        cluster_size = len(texts) or 1
        candidate_df: dict[str, int] = {}
        for row in texts:
            for gram in ngrams(row.get("text") or ""):
                candidate_df[gram] = candidate_df.get(gram, 0) + 1
        for word, cluster_df in candidate_df.items():
            if word in seen or word in stop or len(word) < 2 or cluster_df < min_df:
                continue
            lift = (cluster_df / cluster_size) / max(document_frequency_for(word) / total_docs,
                                                     1.0 / total_docs)
            if lift < min_lift:
                continue
            frequency = cluster_df / cluster_size
            score = 0.4 * min(1, frequency) + 0.3 * min(1, lift / 5) + 0.3 * cluster["avg_sim"]
            if score < min_score:
                continue
            seen.add(word)
            if word in stance_words:
                tag = next((tag for tag, words in tag_of.items() if word in words), "related")
                kind = "feature"
            elif lift >= 5 and len(word) >= 3:
                tag, kind = "seed_alias", "seed"
            else:
                tag, kind = "related", "feature"
            out.append({"word": word, "kind": kind, "suggested_tag": tag,
                        "score": round(score, 3), "lift": round(lift, 2),
                        "df_clu": cluster_df, "avg_sim": cluster["avg_sim"],
                        "source_docs": cluster["doc_ids"], "sample": cluster["sample"]})
    return sorted(out, key=lambda x: x["score"], reverse=True)


def mine_and_queue(store, watch: dict, km=None, *, _extract_seed_candidates_fn=None) -> dict:
    """Queue mined feature and seed suggestions for self-owned entities."""
    from .keywords import KeywordManager

    km = km or KeywordManager(store)
    extract = _extract_seed_candidates_fn or extract_seed_candidates
    n_seed = n_feature = 0
    for entity in watch.get("entities", []):
        if entity.get("type", "self") != "self":
            continue
        entity_id = entity["id"]
        for candidate in extract(store, entity_id, entity.get("aliases") or [entity_id],
                                 entity.get("must_not"), km=km):
            try:
                km.add_suggestion(
                    candidate["word"], candidate["suggested_tag"], entity_id,
                    score=candidate["score"],
                    reason=f"挖词·区分度×{candidate['lift']}·共现{candidate['df_clu']}",
                    source_docs=json.dumps(candidate["source_docs"]),
                )
                if candidate["kind"] == "seed":
                    n_seed += 1
                else:
                    n_feature += 1
            except Exception:
                pass
    return {"seed": n_seed, "feature": n_feature}


def append_alias(
    entity_id: str,
    word: str,
    *,
    _validate_watch_fn=None,
    _watch_path_fn=None,
) -> tuple[bool, str]:
    """Append a term to the configured entity aliases with validation/backup."""
    validate = _validate_watch_fn or validate_watch
    path = (_watch_path_fn or watch_path)()
    try:
        text = open(path, encoding="utf-8").read()
    except Exception as exc:
        return False, f"读取失败：{exc}"
    if not word or word in text:
        return False, "词为空或已存在"
    lines = text.splitlines(keepends=True)
    in_block = False
    for index, line in enumerate(lines):
        if re.match(rf"\s*-\s*id:\s*{re.escape(entity_id)}\s*$", line):
            in_block = True
            continue
        if in_block and re.match(r"\s*-\s*id:\s*", line):
            break
        if in_block:
            match = re.match(r"(?P<pre>\s*aliases:\s*\[)(?P<body>.*?)(?P<post>\]\s*.*)$", line)
            if match:
                lines[index] = (f'{match.group("pre")}{match.group("body")}, "{word}"'
                                f'{match.group("post")}')
                candidate = "".join(lines)
                ok, message = validate(candidate)
                if not ok:
                    return False, f"改后不合法：{message}"
                try:
                    shutil.copyfile(path, path + ".bak")
                except FileNotFoundError:
                    pass
                open(path, "w", encoding="utf-8").write(candidate)
                return True, f"已追加到 {entity_id}.aliases"
    return False, "未找到该实体的单行 aliases（可能是 block 风格），请用编辑器手动加"
