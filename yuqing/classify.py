# -*- coding: utf-8 -*-
"""多维分类器（Phase C）：给每条帖打 主体×立场 标签，重要性由声量当量派生。

引擎无关、可降级：有 LLM key + 标注范例 → few-shot（DeepSeek/MiniMax）；否则规则粗判。
主体维优先查官方账号白名单（确定性），未命中再 LLM/followers。结果落 features.signals，零迁移。
范例来自 annotations 表（Phase A 标注），medoid 分层挑代表样本。
"""

from __future__ import annotations

import hashlib
import json
import sys

from . import llm
from . import embed
from .keywords import SUBJECTS, STANCES

# ---- 输出契约（枚举复用 keywords 的唯一常量，防串味）----
CLASSIFY_SYSTEM = (
    "你是中文舆情多维标注器。对每条帖子判两维并给依据词。\n"
    "【主体】官方=品牌自营号；准官方=经销商/代理/员工/合作方；媒体=资讯/自媒体账号；用户·KOL=普通用户或达人。\n"
    "【立场】赞扬 / 中立 / 批评(理性负面) / 吐槽(情绪化抱怨) / 投诉(要求解决/维权) / 纯传播(无评价的搬运转发)。\n"
    "keywords 给判断依据词，必须逐字来自正文。注意反讽（表面夸实则贬→按真实立场判）。\n"
    "只返回 JSON：{\"items\":[{\"doc_id\":..,\"subject\":..,\"stance\":..,\"subject_conf\":0-1,"
    "\"stance_conf\":0-1,\"keywords\":[..]}]}"
)


def _hash_bucket(doc_id: str, mod: int = 5) -> int:
    return int(hashlib.sha1(doc_id.encode("utf-8")).hexdigest(), 16) % mod


def build_exemplars(store, *, holdout: bool = False, per_stance: int = 2, cap: int = 12) -> list[dict]:
    """从标注挑代表样本作 few-shot 范例：按 stance 分层取 medoid（最中心=最典型）。

    holdout=True 时按 doc_id 哈希划走 20% 给评估（生产调用须 holdout=False，否则白扔 1/5）。
    无 embedding → 退化为每 stance 取最新 per_stance 条。返回 [{doc_id,text,subject,stance}]。
    """
    rows = [dict(r) for r in store.load_annotations()]
    if holdout:
        rows = [r for r in rows if _hash_bucket(r["doc_id"]) != 0]     # 非 test 桶
    by_stance: dict[str, list] = {s: [] for s in STANCES}
    for r in rows:
        if r.get("stance") in by_stance:
            r["_vec"] = embed.from_blob(r["embedding"]) if r.get("embedding") else []
            by_stance[r["stance"]].append(r)
    out: list[dict] = []
    for s, members in by_stance.items():
        if not members:
            continue
        vecs = [m for m in members if m["_vec"]]
        if len(vecs) >= 2:                                             # medoid：与同组均相似度最高
            def _avg(m):
                sims = [embed.cosine(m["_vec"], o["_vec"]) for o in vecs if o is not m]
                return sum(sims) / len(sims) if sims else 0.0
            ranked = sorted(vecs, key=_avg, reverse=True)
        else:
            ranked = sorted(members, key=lambda m: m["doc_id"], reverse=True)   # 无向量→稳定取样
        for m in ranked[:per_stance]:
            out.append({"doc_id": m["doc_id"], "text": (m.get("text") or "")[:200],
                        "subject": m.get("subject"), "stance": s})
    return out[:cap]


def _valid(it: dict) -> bool:
    return (isinstance(it, dict) and it.get("doc_id")
            and it.get("subject") in SUBJECTS and it.get("stance") in STANCES)


def classify_llm(provider: str, docs: list[dict], exemplars: list[dict]) -> dict[str, dict]:
    """few-shot 分类。docs=[{doc_id,text}]。返回 {doc_id: {subject{value,confidence},stance{...},keywords}}。"""
    ex = [{"text": e["text"], "subject": e["subject"], "stance": e["stance"]} for e in exemplars]
    user = ("范例（已人工标注，照此口径判）：\n" + json.dumps(ex, ensure_ascii=False) + "\n\n"
            + "待标注：\n" + json.dumps([{"doc_id": d["doc_id"], "text": (d.get("text") or "")[:400]}
                                        for d in docs], ensure_ascii=False))
    resp = llm.chat_json(provider, CLASSIFY_SYSTEM, user)
    out: dict[str, dict] = {}
    for it in (resp.get("items") or []):
        if not _valid(it):
            continue
        out[it["doc_id"]] = {
            "subject": {"value": it["subject"], "confidence": float(it.get("subject_conf", 0.6) or 0.6)},
            "stance": {"value": it["stance"], "confidence": float(it.get("stance_conf", 0.6) or 0.6)},
            "keywords": [w for w in (it.get("keywords") or []) if isinstance(w, str)]}
    return out


def classify_rule(row: dict, feat: dict) -> dict:
    """离线规则粗判（无 LLM/无范例/LLM 残缺时兜底）。stance 从极性+投诉+危机推，subject 从粉丝粗分。"""
    pol = feat.get("polarity")
    sig = feat.get("signals") or {}
    if sig.get("crisis") or row.get("is_complaint"):
        stance = "投诉"
    elif pol == "neg":
        stance = "批评"
    elif pol == "pos":
        stance = "赞扬"
    else:
        stance = "中立"                                    # 纯传播离线不可辨，归中立
    return {"subject": {"value": "用户·KOL", "confidence": 0.3},   # 官方/媒体靠白名单，规则默认用户
            "stance": {"value": stance, "confidence": 0.3}, "keywords": [], "_src": "rule"}


def resolve_subject(store, row: dict, item: dict | None) -> tuple[str, float]:
    """主体判定优先级：白名单(确定性 1.0) > LLM > followers 粗兜底。"""
    wl = store.account_type(row.get("author") or "", row.get("platform") or "")
    if wl:
        return wl, 1.0
    if item and item.get("subject"):
        return item["subject"]["value"], item["subject"]["confidence"]
    return "用户·KOL", 0.3                                  # followers 现无官方标记，统一兜底用户


def validate_keywords(item: dict, text: str) -> dict:
    """依据词必须是正文逐字子串（防幻觉），非子串剔除。"""
    if item and item.get("keywords"):
        item["keywords"] = [w for w in item["keywords"] if w and w in (text or "")]
    return item


def importance_bucket(equiv: float, risk: float) -> str:
    """重要性分档：用声量当量(覆盖全极性)为主，risk 为负面加成。risk 对非负面恒 0，故不能单用。"""
    s = max(equiv or 0.0, risk or 0.0)
    if s >= 30 or (risk or 0) >= 30:
        return "高"
    if s >= 8:
        return "中"
    return "低"


def evaluate(store) -> dict:
    """留出 20% 标注算 主体/立场 准确率 + 混淆矩阵。标注不足返回 note，不报错。"""
    rows = [dict(r) for r in store.load_annotations()]
    test = [r for r in rows if _hash_bucket(r["doc_id"]) == 0 and r.get("subject") and r.get("stance")]
    if len(test) < 5:
        return {"n": len(test), "note": "标注不足（需≥5 条留出集）"}
    exemplars = build_exemplars(store, holdout=True)
    prov = "deepseek" if llm.available("deepseek") else ("minimax" if llm.available("minimax") else None)
    if not prov or not exemplars:
        return {"n": len(test), "note": "无 LLM key 或无范例，跳过评估"}
    pred = classify_llm(prov, [{"doc_id": r["doc_id"], "text": r.get("text")} for r in test], exemplars)
    res = {"n": len(test)}
    for dim in ("subject", "stance"):
        correct, conf = 0, {}
        for r in test:
            p = pred.get(r["doc_id"], {}).get(dim, {}).get("value")
            t = r.get(dim)
            if p == t:
                correct += 1
            conf[(t, p)] = conf.get((t, p), 0) + 1
        res[dim] = {"acc": round(correct / len(test), 3),
                    "confusion": {f"{k[0]}→{k[1]}": v for k, v in sorted(conf.items())}}
    return res


if __name__ == "__main__":
    # 规则粗判
    assert classify_rule({"is_complaint": 1}, {"polarity": "neg", "signals": {}})["stance"]["value"] == "投诉"
    assert classify_rule({}, {"polarity": "pos", "signals": {}})["stance"]["value"] == "赞扬"
    assert classify_rule({}, {"polarity": "neu", "signals": {}})["stance"]["value"] == "中立"
    assert classify_rule({}, {"polarity": "neg", "signals": {"crisis": True}})["stance"]["value"] == "投诉"

    # 重要性纠偏：正面高传播(equiv 大, risk=0) 不被判低
    assert importance_bucket(40, 0) == "高" and importance_bucket(2, 0) == "低" and importance_bucket(10, 0) == "中"

    # 依据词幻觉过滤
    assert validate_keywords({"keywords": ["退款", "编造词"]}, "申请退款")["keywords"] == ["退款"]

    # classify_llm：mock 返回含 残缺/非法枚举/合法 三条 → 只留合法
    llm.chat_json = lambda p, s, u, **k: {"items": [
        {"doc_id": "d1", "subject": "用户·KOL", "stance": "投诉", "subject_conf": 0.8, "stance_conf": 0.9, "keywords": ["退款"]},
        {"doc_id": "d2", "subject": "外星人", "stance": "投诉"},        # 非法主体枚举 → 丢
        {"doc_id": "d3", "stance": "赞扬"}]}                            # 缺 subject → 丢
    got = classify_llm("deepseek", [{"doc_id": "d1", "text": "退款"}], [])
    assert set(got) == {"d1"} and got["d1"]["subject"]["value"] == "用户·KOL"

    # 白名单优先：命中即确定性 1.0，盖过 LLM
    from .store import Store as _S
    s = _S(":memory:")
    s.add_account("央视新闻", "媒体", platform="weibo")
    subj, conf = resolve_subject(s, {"author": "央视新闻", "platform": "weibo"},
                                 {"subject": {"value": "用户·KOL", "confidence": 0.9}})
    assert subj == "媒体" and conf == 1.0, (subj, conf)
    subj2, _ = resolve_subject(s, {"author": "路人"}, {"subject": {"value": "用户·KOL", "confidence": 0.7}})
    assert subj2 == "用户·KOL"

    # build_exemplars 分层 + 无向量降级（无 embedding 仍出范例）
    from .store import CleanDoc as _CD
    s2 = _S(":memory:")
    for i, st in enumerate(["投诉", "投诉", "赞扬", "赞扬", "中立"]):
        d = _CD.build(platform="weibo", entity_id="e", native_id=f"n{i}", text=f"帖{st}{i}")
        s2.add_clean(d)
        s2.add_annotation(d.doc_id, subject="用户·KOL", stance=st, importance="中", entity_id="e", ts="t")
    ex = build_exemplars(s2, per_stance=1)
    stances = {e["stance"] for e in ex}
    assert "投诉" in stances and "赞扬" in stances and "中立" in stances, stances    # 每档有代表

    print("OK classify: 规则粗判/重要性纠偏/依据词过滤/LLM枚举校验/白名单优先/范例分层 全通")
