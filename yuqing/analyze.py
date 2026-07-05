# -*- coding: utf-8 -*-
"""分析层：把 clean 帖子抽成结构化情绪/信息。

两条路径，同一份输出契约：
- rule_extract：离线规则 stub，无需 API key，让整条链可测（也是断网兜底）。
- claude_extract：单遍 Claude `extract_opinion` tool，批量、structured output。
铁律：evidence 必须是正文逐字子串，落库前校验，不过关即丢弃并降置信（防幻觉最小护栏）。
"""

from __future__ import annotations

import json
import os
import datetime as _dt
from typing import Optional

from .score import Weights, risk_score

# --- 词典（冷启动种子，后续从误报回灌迭代）---
CRISIS_WORDS = ["维权", "退款", "翻车", "避雷", "塌房", "召回", "爆炸", "起火", "曝光", "315", "诉讼", "欺诈"]
NEG_WORDS = ["垃圾", "差评", "难用", "失望", "坑", "退退退", "拉胯", "别买", "骗", "客服态度", "卡顿", "发热"]
POS_WORDS = ["好用", "推荐", "满意", "yyds", "真香", "点赞", "喜欢", "值得", "绝绝子", "好评"]

# 方面级情绪(ABSA)词典：方面 → 触发词。命中即把该帖情绪归到该方面。
ASPECT_LEXICON = {
    "价格": ["价格", "贵", "便宜", "性价比", "涨价", "割韭菜"],
    "质量": ["质量", "做工", "碎屏", "坏了", "掉漆", "翻新"],
    "性能": ["卡顿", "发热", "流畅", "续航", "掉电", "死机"],
    "服务": ["客服", "售后", "态度", "退款", "维权"],
    "物流": ["物流", "快递", "发货", "配送"],
    "系统": ["系统", "bug", "升级", "广告", "闪退"],
}


def _aspects(text: str, polarity: str) -> list[dict]:
    """规则版 ABSA：命中方面词即记一条（极性取整体极性）。

    ponytail: 每方面独立极性由 Claude 路径更准；规则版先用整体极性够跑通报告。
    """
    return [{"aspect": a, "polarity": polarity}
            for a, kws in ASPECT_LEXICON.items() if any(k in text for k in kws)]

# extract_opinion 工具 schema（Claude tool use 用；也是文档）
EXTRACT_TOOL = {
    "name": "extract_opinion",
    "description": "对一批帖子逐条抽取情绪与关键信息。evidence 必须是正文中的逐字子串。",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "string"},
                        "polarity": {"type": "string", "enum": ["pos", "neg", "neu"]},
                        "intensity": {"type": "number", "description": "0-1"},
                        "confidence": {"type": "number", "description": "0-1"},
                        "is_ironic": {"type": "boolean"},
                        "is_spam": {"type": "boolean"},
                        "topic_label": {"type": "string"},
                        "summary": {"type": "string", "description": "一句话摘要"},
                        "evidence": {"type": "string", "description": "支撑判断的正文逐字子串"},
                        "signals": {
                            "type": "object",
                            "properties": {
                                "crisis": {"type": "boolean"},
                                "bug": {"type": "boolean"},
                                "feature_request": {"type": "boolean"},
                                "competitors": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        "aspects": {
                            "type": "array",
                            "description": "方面级情绪：把情绪绑到具体方面(价格/质量/性能/服务/物流/系统)",
                            "items": {"type": "object", "properties": {
                                "aspect": {"type": "string"},
                                "polarity": {"type": "string", "enum": ["pos", "neg", "neu"]}}},
                        },
                    },
                    "required": ["doc_id", "polarity", "intensity", "confidence", "evidence"],
                },
            }
        },
        "required": ["items"],
    },
}

_SYSTEM = (
    "你是中文舆情情绪与信息抽取器。逐条判断情绪极性(pos/neg/neu)、强度(0-1)、置信度，"
    "识别反讽/水军/投诉/Bug/功能诉求/竞品提及，给一句话摘要和 topic_label。"
    "注意中文反讽与黑话(yyds/拉胯/退退退…)。evidence 必须逐字来自正文，不得改写。"
    "只有毫无评价性内容才判 neu。"
)


def _first_hit(text: str, words: list[str]) -> Optional[str]:
    for wcls in words:
        if wcls in text:
            i = text.index(wcls)
            return text[max(0, i - 6): i + len(wcls) + 6]  # 真子串片段
    return None


def rule_extract(doc: dict) -> dict:
    """离线规则 stub。evidence 保证是正文子串。"""
    text = doc.get("text", "") or ""
    crisis = _first_hit(text, CRISIS_WORDS)
    neg = crisis or _first_hit(text, NEG_WORDS)
    pos = _first_hit(text, POS_WORDS)
    if neg:
        n_hits = sum(w in text for w in CRISIS_WORDS + NEG_WORDS)
        return {"polarity": "neg", "intensity": min(0.4 + 0.15 * n_hits, 1.0), "confidence": 0.55,
                "is_ironic": False, "is_spam": False, "topic_label": "投诉/负面",
                "summary": text[:40], "evidence": neg,
                "signals": {"crisis": bool(crisis), "bug": "卡顿" in text or "发热" in text,
                            "feature_request": False, "competitors": [],
                            "aspects": _aspects(text, "neg")}}
    if pos:
        return {"polarity": "pos", "intensity": 0.6, "confidence": 0.5, "is_ironic": False,
                "is_spam": False, "topic_label": "正面口碑", "summary": text[:40],
                "evidence": pos, "signals": {"aspects": _aspects(text, "pos")}}
    return {"polarity": "neu", "intensity": 0.1, "confidence": 0.4, "is_ironic": False,
            "is_spam": False, "topic_label": "中性", "summary": text[:40],
            "evidence": text[:20], "signals": {"aspects": _aspects(text, "neu")}}


HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"


def route_model(text: str) -> str:
    """分层路由：命中危机/负面词或长文 → Sonnet 深抽；其余海量低价值 → Haiku 省钱。"""
    hot = len(text or "") > 80 or any(w in (text or "") for w in CRISIS_WORDS + NEG_WORDS)
    return SONNET if hot else HAIKU


def claude_extract(docs: list[dict], model: str = SONNET) -> dict[str, dict]:
    """单遍 Claude 批量抽取。需 ANTHROPIC_API_KEY。返回 {doc_id: feature}。"""
    import anthropic  # 惰性导入：离线自检不需要

    client = anthropic.Anthropic()
    payload = [{"doc_id": d["doc_id"], "text": d["text"]} for d in docs]
    msg = client.messages.create(
        model=model, max_tokens=4096, system=_SYSTEM,
        tools=[EXTRACT_TOOL], tool_choice={"type": "tool", "name": "extract_opinion"},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    out = {}
    for block in msg.content:
        if block.type == "tool_use":
            for it in block.input.get("items", []):
                out[it["doc_id"]] = it
    return out


def _validate_evidence(feat: dict, text: str) -> dict:
    """evidence 不是正文子串 → 丢弃 evidence 并降置信（防幻觉）。"""
    ev = feat.get("evidence", "")
    if ev and ev not in text:
        feat = {**feat, "evidence": "", "confidence": min(feat.get("confidence", 0.0), 0.3)}
    return feat


def analyze_pending(store, weights: Optional[Weights] = None, *, use_claude: Optional[bool] = None,
                    now: Optional[str] = None) -> int:
    """对所有缺 features 的 clean 帖做抽取并落库。返回处理条数。"""
    weights = weights or Weights()
    rows = store.clean_missing_features()
    if not rows:
        return 0
    docs = [{"doc_id": r["doc_id"], "text": r["text"]} for r in rows]

    if use_claude is None:
        use_claude = bool(os.getenv("ANTHROPIC_API_KEY"))
    feats: dict[str, dict] = {}
    if use_claude:
        # 分层路由：热样本走 Sonnet，低价值走 Haiku（成本降一个量级）
        hot = [d for d in docs if route_model(d["text"]) == SONNET]
        cold = [d for d in docs if route_model(d["text"]) == HAIKU]
        from .budget import guard, BudgetExceeded
        day = (now or _dt.datetime.now().astimezone().isoformat())[:10]
        try:                             # 成本配额熔断：按实际 API 调用数计（最多两次）
            guard(store, day, add_calls=bool(hot) + bool(cold), add_tokens=len(docs) * 1200)
        except BudgetExceeded:
            use_claude = False           # 超限降级为规则抽取，不烧钱
        else:
            if hot:
                feats.update(claude_extract(hot, model=SONNET))
            if cold:
                feats.update(claude_extract(cold, model=HAIKU))

    for r in rows:
        feat = feats.get(r["doc_id"]) or rule_extract(dict(r))
        feat = _validate_evidence(feat, r["text"] or "")
        sig = feat.get("signals") or {}                  # Claude 可能返回 signals: null
        feat["signals"] = sig
        if "aspects" in feat and "aspects" not in sig:   # Claude 路径 aspects 折进 signals 落库
            sig["aspects"] = feat["aspects"]
        # 打风险分：合并 clean 字段 + 抽取结果
        merged = dict(r)
        merged.update(polarity=feat["polarity"], intensity=feat.get("intensity", 0.0),
                      signals=sig, is_complaint=bool(r["is_complaint"]))
        feat["risk"] = risk_score(merged, weights)
        store.add_feature(r["doc_id"], feat)
    store.commit()
    return len(rows)


if __name__ == "__main__":
    neg = rule_extract({"text": "这手机用了三天就发热卡顿，申请退款客服还不理，避雷！"})
    assert neg["polarity"] == "neg" and neg["signals"]["crisis"] and neg["evidence"] in \
        "这手机用了三天就发热卡顿，申请退款客服还不理，避雷！"
    aspects = {a["aspect"] for a in neg["signals"]["aspects"]}
    assert "性能" in aspects and "服务" in aspects, aspects        # ABSA 命中方面
    pos = rule_extract({"text": "真香，太好用了推荐"})
    assert pos["polarity"] == "pos"
    bad = _validate_evidence({"evidence": "编造的原话", "confidence": 0.9}, "真实正文")
    assert bad["evidence"] == "" and bad["confidence"] <= 0.3
    assert route_model("发热卡顿避雷退款") == SONNET and route_model("还行吧") == HAIKU  # 分层路由
    print("OK analyze:", neg["polarity"], pos["polarity"], "| ABSA", sorted(aspects), "| 路由分层✓")
