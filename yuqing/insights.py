# -*- coding: utf-8 -*-
"""Phase 3 决策增量：老板一句话日报 / AI 舆情问答(RAG-lite) / 诉求→需求 / 事件时间线。

全部寄生在已采集归一的同一份数据上，边际成本近零、不新增采集。坚持"人在环路"：
诉求闭环只产结构化建议清单供一键确认，绝不自动灌工单。
"""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Optional


def _self_rows(store, self_entities: Optional[set] = None) -> list[dict]:
    if self_entities is None:
        return [dict(r) for r in store.joined()]
    # 一个帖子可匹配多个自有实体；全局日报按 doc 去重，但不能依赖 legacy clean.entity_id。
    by_doc: dict[str, dict] = {}
    for entity_id in self_entities:
        for row in store.joined(entity_id):
            item = dict(row)
            item["entity_id"] = entity_id
            by_doc.setdefault(item["doc_id"], item)
    return list(by_doc.values())


# --- 老板一句话日报（确定性，无需 LLM，可直接推 IM）---
def oneliner(store, watch: dict) -> str:
    self_ids = {e["id"] for e in watch["entities"] if e.get("type", "self") == "self"}
    rows = _self_rows(store, self_ids)
    n = len(rows)
    if not n:
        return "今日舆情：暂无数据（注意核查采集健康，勿当无负面）"
    neg = sum(r["polarity"] == "neg" for r in rows)
    parts = [f"今日舆情 {n} 条，负面 {neg}（{neg / n:.0%}）"]
    top = max((r for r in rows if r["polarity"] == "neg"), key=lambda r: r["risk"], default=None)
    if top:
        parts.append(f"最高风险 {top['platform']}「{(top['summary'] or '')[:20]}」(风险{top['risk']})")
    return "｜".join(parts)


# --- AI 舆情问答（RAG-lite：SQL 检索 + 可选 Claude 带引用作答）---
def _like_escape(s: str) -> str:
    """转义 LIKE 元字符，让检索是字面子串匹配（避免 '100%好评' 里的 % 变通配符）。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _retrieve_lexical(store, query: str, k: int = 8) -> list[dict]:
    """词汇兜底：按 query 里的词做字面子串检索，按风险分排序取 top-k。语义不可用时回退。"""
    q = (query or "").strip()
    # 保留 ≥2 字符的词，或单字非 ASCII（合法中文单字），丢掉英文单字/停用符
    terms = [t for t in ([q] + q.split()) if t and (len(t) >= 2 or not t.isascii())]
    if not terms:
        return []
    where = " OR ".join("c.text LIKE ? ESCAPE '\\'" for _ in terms)
    sql = ("SELECT c.doc_id,c.platform,c.text,c.url,f.polarity,f.risk,f.summary "
           f"FROM clean c JOIN features f USING(doc_id) WHERE {where} "
           "ORDER BY f.risk DESC LIMIT ?")
    rows = store.conn.execute(sql, [f"%{_like_escape(t)}%" for t in terms] + [k]).fetchall()
    return [dict(r) for r in rows]


def _fetch_by_ids(store, doc_ids: list[str]) -> dict[str, dict]:
    if not doc_ids:
        return {}
    ph = ",".join("?" * len(doc_ids))
    rows = store.conn.execute(
        f"SELECT c.doc_id,c.platform,c.text,c.url,f.polarity,f.risk,f.summary "
        f"FROM clean c JOIN features f USING(doc_id) WHERE c.doc_id IN ({ph})", doc_ids)
    return {r["doc_id"]: dict(r) for r in rows}


SEM_MIN_SIM = 0.35        # 语义检索相似度下限（宁缺毋滥，低于此视为不相关）


def retrieve(store, query: str, k: int = 8, *, min_sim: float = SEM_MIN_SIM) -> list[dict]:
    """语义检索优先（query向量×库内向量余弦），无 key/无向量时回退词汇匹配。

    命中项带 sim 相似度分（可解释）。语义能召回"电池"→只说"续航"的帖；词汇兜底保证不断。
    集成关键词库：自动扩展同义词（similar标签），如查"发热"同时检索"烫手"。
    """
    from . import embed
    q = (query or "").strip()
    if not q:
        return []

    # 扩展同义词（从关键词库）
    expanded_queries = [q]
    try:
        from .keywords import KeywordManager
        km = KeywordManager(store)
        # 查找query的同义词（similar标签）
        similar_words = km.get_similar_words(q, entity_id=None)  # entity_id=None表示全局搜索
        for sw in similar_words:
            if sw['weight'] >= 0.6:  # 权重阈值：只扩展高相关的同义词
                expanded_queries.append(sw['word'])

        # 如果query本身在词库中，也查找指向它的同义词
        # 例如：query="发热"，找到note含"→发热"的词
        all_similar = km.get_by_tag('similar', entity_id=None)
        for kw in all_similar:
            if kw.get('note') and q in kw['note'] and kw['word'] not in expanded_queries:
                if kw['weight'] >= 0.6:
                    expanded_queries.append(kw['word'])
    except Exception:
        pass  # 关键词库集成失败，不影响检索

    if embed.available():
        try:
            # 对扩展后的查询词都计算向量，取平均（多查询融合）
            qvecs = []
            for eq in expanded_queries[:3]:  # 最多扩展3个同义词，避免语义漂移
                vec = embed.embed_one(eq)
                if vec:
                    qvecs.append(vec)

            if qvecs:
                # 多向量平均融合
                import numpy as np
                qvec = np.mean(qvecs, axis=0).tolist() if len(qvecs) > 1 else qvecs[0]

                cands = [(cid, embed.from_blob(b)) for cid, b in store.embeddings_for()]
                if cands:
                    top = embed.top_k_similar(qvec, cands, k=k, min_sim=min_sim)
                    if top:
                        got = _fetch_by_ids(store, [cid for cid, _ in top])
                        out = []
                        for cid, sim in top:
                            if cid in got:
                                got[cid]["sim"] = round(sim, 3)
                                out.append(got[cid])
                        return out
        except Exception as e:
            import sys
            print(f"[语义检索失败，回退词汇] {str(e)[:120]}", file=sys.stderr)

    # 降级：词汇匹配也用扩展后的查询词（OR组合）
    return _retrieve_lexical(store, " OR ".join(expanded_queries), k)


def ask(store, question: str, *, use_claude: Optional[bool] = None) -> dict:
    """返回 {answer, sources}。无 API key 时给检索式摘要（可离线测），有则 Claude 带引用作答。"""
    hits = retrieve(store, question)
    sources = [h["doc_id"] for h in hits]
    if use_claude is None:
        from . import config
        use_claude = bool(config.resolve("ANTHROPIC_API_KEY"))
    if not hits:
        return {"answer": "未检索到相关舆情。", "sources": []}
    if not use_claude:
        neg = sum(h["polarity"] == "neg" for h in hits)
        top = hits[0]
        tag = f"（语义相似{top['sim']}）" if "sim" in top else ""      # 语义召回标相似度分
        return {"answer": f"检索到 {len(hits)} 条相关，其中负面 {neg} 条。"
                          f"最相关：{top['platform']}「{(top['summary'] or top['text'] or '')[:30]}」{tag}"
                          f"[来源:{top['doc_id']}]。", "sources": sources}
    try:
        import anthropic
        ctx = json.dumps([{"doc_id": h["doc_id"], "platform": h["platform"],
                           "text": (h["text"] or "")[:200]} for h in hits], ensure_ascii=False)
        system = ("基于给定舆情片段回答，禁止编造；每个结论标 [来源:doc_id]，doc_id 只能取自片段。"
                  "无充分依据答'证据不足'。")
        msg = anthropic.Anthropic().messages.create(
            model="claude-sonnet-5", max_tokens=800, system=system,
            messages=[{"role": "user", "content": f"问题：{question}\n\n片段：{ctx}"}])
        return {"answer": "".join(b.text for b in msg.content if b.type == "text"), "sources": sources}
    except Exception:
        # 配了 key 但 SDK/API 不可用时，仍返回可溯源的确定性摘要。
        neg = sum(h["polarity"] == "neg" for h in hits)
        top = hits[0]
        return {"answer": f"检索到 {len(hits)} 条相关，其中负面 {neg} 条。"
                          f"最相关：{top['platform']}「{(top['summary'] or top['text'] or '')[:30]}」"
                          f"[来源:{top['doc_id']}]。", "sources": sources}


# --- 诉求→产品需求闭环（半自动，产建议清单供人工确认，不自动建工单）---
def backlog(
    store, self_entities: Optional[set] = None, *, since_day: str | None = None,
) -> list[dict]:
    """把 Bug/投诉/功能诉求 按 类型×话题 聚成结构化需求条目，按声量×热度降序。"""
    groups: dict[tuple, dict] = {}
    rows = _self_rows(store, self_entities)
    if since_day:
        from .analytics import normalize_day
        rows = [
            row for row in rows
            if normalize_day(row.get("publish_ts"), row.get("fetched_at")) >= since_day
        ]
    for r in rows:
        sig = json.loads(r["signals"] or "{}")
        kind = ("Bug" if sig.get("bug") else "功能诉求" if sig.get("feature_request")
                else "投诉" if r["is_complaint"] else None)
        if not kind:
            continue
        key = (kind, r["topic_label"] or "未分类")
        g = groups.setdefault(key, {"kind": kind, "topic": r["topic_label"] or "未分类",
                                    "count": 0, "heat": 0, "sample": r["doc_id"], "url": r["url"] or ""})
        g["count"] += 1
        g["heat"] += (r["likes"] or 0) + (r["comments"] or 0)
    return sorted(groups.values(), key=lambda x: (x["count"], x["heat"]), reverse=True)


def backlog_csv(items: list[dict]) -> str:
    """导出为 CSV（喂飞书多维表/研发系统前的人工确认载体）。用 csv 模块正确转义逗号/换行。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["类型", "话题", "声量", "热度", "代表帖", "链接"])
    for it in items:
        w.writerow([it["kind"], it["topic"], it["count"], it["heat"], it["sample"], it["url"]])
    return buf.getvalue()


# --- 跨平台事件时间线（限定关键词内，按时间还原扩散链）---
def timeline(store, keyword: str) -> list[dict]:
    """命中关键词的帖子按时间升序，还原'微博起火→知乎深扒→抖音扩散→黑猫沉淀'。

    多词（空格分隔）需全部命中，避免 CLI 传入 '星海 手机' 被当整串精确匹配而静默空结果。
    """
    toks = [t for t in keyword.split() if t] or ([keyword] if keyword else [])
    if not toks:
        return []
    hits = [r for r in _self_rows(store) if all(t in (r["text"] or "") for t in toks)]
    hits.sort(key=lambda r: (r["publish_ts"] or r["fetched_at"] or ""))
    return [{"time": r["publish_ts"] or r["fetched_at"] or "", "platform": r["platform"],
             "summary": (r["summary"] or r["text"] or "")[:30], "doc_id": r["doc_id"]}
            for r in hits]


if __name__ == "__main__":
    # V2-A 语义检索：mock embed，验证语义召回(不含关键词的同义帖) + 无 key 回退词汇
    import os as _os
    from .store import Store, CleanDoc
    from . import embed as _emb
    # 造 3 条：查询"电池"，只有一条说"续航"(语义相关无字面)，一条说"外观"(无关)
    s = Store(":memory:")
    vecs = {"续航": [1.0, 0.9, 0.0], "外观": [0.0, 0.1, 1.0], "电池": [1.0, 0.95, 0.0]}
    for i, kw in enumerate(["续航", "外观"]):
        d = CleanDoc.build(platform="weibo", entity_id="e", native_id=f"n{i}", text=f"这盒子{kw}一般", fetched_at="t")
        s.add_clean(d)
        s.add_feature(d.doc_id, {"polarity": "neg", "risk": 1})
        s.set_embedding(d.doc_id, _emb.to_blob(vecs[kw]))
    s.commit()
    _os.environ["EMBED_API_KEY"] = "x"
    _emb.embed_one = lambda t, **kw: vecs.get("电池")          # mock 查询向量
    hits = retrieve(s, "电池", k=3)
    assert hits and "续航" in hits[0]["text"] and "sim" in hits[0], hits   # 语义召回同义帖+带分
    assert all("外观" not in h["text"] for h in hits), "无关帖不应召回"
    _os.environ.pop("EMBED_API_KEY")
    # 无 key → 回退词汇：查"续航"字面命中
    lex = retrieve(s, "续航")
    assert lex and "续航" in lex[0]["text"] and "sim" not in lex[0]        # 词汇兜底无 sim 分
    print("OK insights: 语义检索(召回同义帖'续航'带相似度分) + 无key回退词汇 全通")
    from .store import Store, CleanDoc
    s = Store(":memory:")
    for i, (txt, ts, plat) in enumerate([
        ("星海手机发热卡顿要退款", "2026-07-01T09:00:00", "weibo"),
        ("深扒星海手机发热问题", "2026-07-02T09:00:00", "zhihu"),
        ("星海手机维权退款投诉", "2026-07-03T09:00:00", "heimao")]):
        d = CleanDoc.build(platform=plat, entity_id="p", native_id=f"n{i}", text=txt,
                           publish_ts=ts, likes=100, fetched_at=ts)
        d.is_complaint = "退款" in txt or "投诉" in txt
        s.add_clean(d)
    from .analyze import analyze_pending
    analyze_pending(s, use_claude=False)
    watch = {"entities": [{"id": "p", "type": "self", "aliases": ["星海手机"]}]}
    assert "今日舆情" in oneliner(s, watch)
    assert ask(s, "发热")["sources"], "检索应命中"
    bl = backlog(s)
    assert any(x["kind"] == "Bug" for x in bl), bl          # 发热卡顿→Bug
    tl = timeline(s, "星海手机")
    assert [t["platform"] for t in tl] == ["weibo", "zhihu", "heimao"], tl   # 按时间还原扩散
    assert timeline(s, "星海 手机")                                          # 多词全命中(不静默空)
    assert retrieve(s, "热")                                                # 单字中文可检索
    assert retrieve(s, "100%不存在") == []                                  # LIKE 通配被转义为字面
    # CSV 逗号/换行安全：含逗号的话题不错列
    csv_out = backlog_csv([{"kind": "Bug", "topic": "续航,发热", "count": 1, "heat": 0,
                            "sample": "d1", "url": "u"}])
    import csv as _csv
    rows = list(_csv.reader(io.StringIO(csv_out)))
    assert rows[1][1] == "续航,发热" and len(rows[1]) == 6, rows            # 逗号被正确转义
    print("OK insights: 老板日报 / 问答 / 诉求→需求 / 事件时间线 全通")
