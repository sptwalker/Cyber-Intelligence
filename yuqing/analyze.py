# -*- coding: utf-8 -*-
"""分析层：把 clean 帖子抽成结构化情绪/信息。

引擎优先级 llm > claude > rule，同一份输出契约：
- cross_extract：deepseek 主抽 + MiniMax 交叉复核（负面/低置信），分歧进人工复核队列。
- claude_extract：Claude `extract_opinion` tool，Haiku/Sonnet 分层路由。
- rule_extract：离线规则 stub，无需 API key，让整条链可测（也是断网/LLM失败的兜底）。
铁律：任何引擎失败/残缺都降级规则、绝不阻塞跑批；evidence 必须正文逐字子串，落库前校验（防幻觉）。
"""

from __future__ import annotations

import json
import datetime as _dt
import sys
from typing import Optional

from .score import Weights, risk_score, influence_degraded, mention_equiv
from . import llm
from . import config

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


def rule_extract(doc: dict, keyword_mgr=None) -> dict:
    """离线规则 stub。evidence 保证是正文子串。

    集成关键词库：检测complaint/selling_point，辅助情感判断。
    """
    text = doc.get("text", "") or ""
    entity_id = doc.get("entity_id")

    # 从关键词库获取吐槽点和卖点
    complaint_words = []
    selling_point_words = []
    if keyword_mgr:
        try:
            complaints = keyword_mgr.get_complaints(entity_id=entity_id, min_weight=0.6)
            complaint_words = [kw['word'] for kw in complaints]

            selling_points = keyword_mgr.get_selling_points(entity_id=entity_id, min_weight=0.6)
            selling_point_words = [kw['word'] for kw in selling_points]
        except Exception:
            pass  # 关键词库读取失败，不影响分析

    # 检测关键词库中的complaint
    kw_complaint = _first_hit(text, complaint_words) if complaint_words else None
    # 检测关键词库中的selling_point
    kw_selling = _first_hit(text, selling_point_words) if selling_point_words else None

    # 原有规则检测
    crisis = _first_hit(text, CRISIS_WORDS)
    neg = crisis or _first_hit(text, NEG_WORDS) or kw_complaint  # 整合关键词库complaint
    pos = _first_hit(text, POS_WORDS) or kw_selling  # 整合关键词库selling_point

    # 计算命中数（包含关键词库）
    n_neg_hits = (sum(w in text for w in CRISIS_WORDS + NEG_WORDS) +
                  sum(w in text for w in complaint_words))
    n_pos_hits = (sum(w in text for w in POS_WORDS) +
                  sum(w in text for w in selling_point_words))

    # 情感判断：优先负面（负面信号更重要）
    if neg and n_neg_hits > n_pos_hits:
        return {"polarity": "neg", "intensity": min(0.4 + 0.15 * n_neg_hits, 1.0), "confidence": 0.55,
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
ANALYSIS_VERSION = "opinion-v2"
PROMPT_VERSION = "opinion-extract-v2"


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


def _claude_feats(docs: list[dict]) -> dict[str, dict]:
    """Claude 分层路由抽取（热 Sonnet / 冷 Haiku）。"""
    out = {}
    hot = [d for d in docs if route_model(d["text"]) == SONNET]
    cold = [d for d in docs if route_model(d["text"]) == HAIKU]
    if hot:
        out.update(claude_extract(hot, model=SONNET))
    if cold:
        out.update(claude_extract(cold, model=HAIKU))
    return out


def llm_extract(provider: str, docs: list[dict]) -> dict[str, dict]:
    """用 deepseek/MiniMax（OpenAI 兼容 JSON）批量抽取，字段对齐 EXTRACT_TOOL schema。"""
    payload = [{"doc_id": d["doc_id"], "text": d["text"]} for d in docs]
    user = ("对下列帖子逐条抽取，只返回 JSON：{\"items\":[{doc_id, polarity(pos/neg/neu), "
            "intensity(0-1), confidence(0-1), is_ironic(bool), summary, evidence(正文逐字子串), "
            "signals:{crisis,bug,feature_request,competitors[]}, aspects:[{aspect,polarity}]}]}。\n"
            "帖子：" + json.dumps(payload, ensure_ascii=False))
    resp = llm.chat_json(provider, _SYSTEM, user)
    # 只保留 doc_id + 合法 polarity 的条目；残缺条目丢弃 → 下游规则兜底（防残缺 item 打崩跑批）
    return {it["doc_id"]: it for it in resp.get("items", [])
            if it.get("doc_id") and it.get("polarity") in ("pos", "neg", "neu")}


def _cross(primary: dict, checker: Optional[dict]) -> dict:
    """交叉分析：两模型极性不一致 → 置信打低并标 needs_review（喂人工复核）；一致判负 → 提高置信。"""
    feat = dict(primary)
    if not checker:
        return feat
    p, c = primary.get("polarity"), checker.get("polarity")
    sig = dict(primary.get("signals") or {})     # 拷贝，避免 shallow copy 回写污染 primary.signals
    feat["signals"] = sig
    if p != c:
        feat["confidence"] = min(feat.get("confidence", 0.5), 0.4)
        sig["cross_disagree"] = f"{p}vs{c}"      # 两模型分歧，进复核队列
    elif p == "neg":
        feat["confidence"] = max(feat.get("confidence", 0.5), 0.8)
    return feat


def cross_extract(docs: list[dict]) -> dict[str, dict]:
    """deepseek 主抽 + MiniMax 对负面/低置信样本交叉复核。缺 key 时自动只用可用的一家。"""
    has_ds, has_mm = llm.available("deepseek"), llm.available("minimax")
    primary_prov = "deepseek" if has_ds else "minimax"
    primary = llm_extract(primary_prov, docs)
    # 只对 主模型判负 或 低置信 的样本，用另一家交叉复核（省调用）
    if has_ds and has_mm:
        recheck = [d for d in docs
                   if (primary.get(d["doc_id"], {}).get("polarity") == "neg"
                       or primary.get(d["doc_id"], {}).get("confidence", 1.0) < 0.6)]
        checker = llm_extract("minimax", recheck) if recheck else {}
        return {did: _cross(primary[did], checker.get(did)) for did in primary}
    return primary


def _pick_engine(use_claude: Optional[bool]) -> str:
    """引擎选择：显式 use_claude=False → 规则(离线/自检)；否则按可用性 llm > claude > 规则。

    绝不用 os.getenv(ANTHROPIC) 把 None 塌成 False——那会让"只有 deepseek/minimax、
    没 Claude"被误判成规则，整套交叉分析静默失效（曾真实踩坑）。
    """
    if use_claude is False:
        return "rule"
    if llm.available("deepseek") or llm.available("minimax"):
        return "llm"
    if use_claude or config.resolve("ANTHROPIC_API_KEY"):
        return "claude"
    return "rule"


def analyze_pending(store, weights: Optional[Weights] = None, *, use_claude: Optional[bool] = None,
                    now: Optional[str] = None) -> int:
    """对所有缺 features 的 clean 帖做抽取并落库。返回处理条数。"""
    weights = weights or Weights()
    rows = store.clean_missing_features(ANALYSIS_VERSION)
    if not rows:
        return 0
    docs = [{"doc_id": r["doc_id"], "text": r["text"]} for r in rows]

    engine = _pick_engine(use_claude)
    analyzed_at = now or _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if engine == "llm":
        models = [p for p in ("deepseek", "minimax") if llm.available(p)]
        model_name = "+".join(models)
    elif engine == "claude":
        model_name = f"{HAIKU}+{SONNET}"
    else:
        model_name = "deterministic-rules"

    feats: dict[str, dict] = {}
    cls: dict[str, dict] = {}
    if engine != "rule":
        from .budget import guard, BudgetExceeded
        day = (now or _dt.datetime.now().astimezone().isoformat())[:10]
        try:
            guard(store, day, add_calls=2, add_tokens=len(docs) * 1200)   # 保守计 2（防少计致超支）
            feats = cross_extract(docs) if engine == "llm" else _claude_feats(docs)
        except BudgetExceeded:
            pass                             # 超限降级为规则抽取，不烧钱
        except Exception as e:               # LLM 网络/JSON 失败绝不阻塞跑批，降级规则
            print(f"[{engine} 抽取失败，降级规则] {str(e)[:150]}", file=sys.stderr)
            feats = {}
        # 多维分类（主体×立场）few-shot：独立 try，与抽取互不影响；无范例则跳过走规则粗判
        try:
            from . import classify
            exemplars = classify.build_exemplars(store, holdout=False)
            prov = "deepseek" if llm.available("deepseek") else ("minimax" if llm.available("minimax") else None)
            if exemplars and prov:
                guard(store, day, add_calls=1, add_tokens=len(docs) * 400)
                cls = classify.classify_llm(prov, docs, exemplars)
        except Exception as e:
            print(f"[分类降级规则] {str(e)[:150]}", file=sys.stderr)
            cls = {}

    # 初始化关键词管理器（用于辅助规则判断）
    keyword_mgr = None
    try:
        from .keywords import KeywordManager
        keyword_mgr = KeywordManager(store)
    except Exception:
        pass  # 关键词库不可用，不影响分析

    # 每实体自定义危机词（watch.yaml crisis_boost）：命中即强制 crisis 信号 → risk×1.5。
    # 引擎无关的确定性覆盖（rule/LLM/claude 都生效），补全全局 CRISIS_WORDS 覆盖不到的产品专属词。
    entity_crisis: dict[str, list] = {}
    try:
        from . import load_watch
        for e in load_watch().get("entities", []):
            cb = e.get("crisis_boost") or []
            if cb:
                entity_crisis[e["id"]] = cb
    except Exception:
        pass

    for r in rows:
        extracted = feats.get(r["doc_id"])
        feat = extracted or rule_extract(dict(r), keyword_mgr)
        result_engine = engine if extracted is not None else ("rule" if engine == "rule" else f"{engine}:rule_fallback")
        result_model = model_name if extracted is not None else "deterministic-rules"
        if feat.get("polarity") not in ("pos", "neg", "neu"):   # LLM 残缺兜底：任何非法极性 → 规则
            feat = rule_extract(dict(r), keyword_mgr)
        feat = _validate_evidence(feat, r["text"] or "")
        sig = feat.get("signals") or {}                  # Claude 可能返回 signals: null
        feat["signals"] = sig
        if "aspects" in feat and "aspects" not in sig:   # Claude 路径 aspects 折进 signals 落库
            sig["aspects"] = feat["aspects"]
        # 每实体危机词覆盖：命中则强制 crisis（并保证负面），确定性、引擎无关
        related_entities = store.entities_for_doc(r["doc_id"]) or [r["entity_id"]]
        extra = [w for eid in related_entities for w in entity_crisis.get(eid, [])]
        if extra and any(w in (r["text"] or "") for w in extra):
            sig["crisis"] = True
            if feat.get("polarity") == "neu":
                feat["polarity"] = "neg"
        # 多维标签（主体×立场）：LLM 分类缺失→规则粗判；主体走白名单优先
        from . import classify
        c = cls.get(r["doc_id"]) or classify.classify_rule(dict(r), feat)
        c = classify.validate_keywords(c, r["text"] or "")
        subj_v, subj_c = classify.resolve_subject(store, dict(r), c)
        sig["subject"], sig["subject_conf"] = subj_v, subj_c
        sig["stance"] = c["stance"]["value"]
        sig["stance_conf"] = c["stance"]["confidence"]
        if c.get("keywords"):
            sig["stance_keywords"] = c["keywords"]
        # 打风险分：合并 clean 字段 + 抽取结果
        merged = dict(r)
        merged.update(polarity=feat["polarity"], intensity=feat.get("intensity", 0.0),
                      signals=sig, is_complaint=bool(r["is_complaint"]))
        feat["risk"] = risk_score(merged, weights)
        sig["importance"] = classify.importance_bucket(mention_equiv(merged, weights), feat["risk"])
        if feat["risk"] > 0 and influence_degraded(merged):
            sig["influence_degraded"] = True     # 风险分缺互动数据(如微博搜索)，报告须标注降级
        store.add_feature(
            r["doc_id"], feat, analysis_version=ANALYSIS_VERSION,
            engine=result_engine, model=result_model, prompt_version=PROMPT_VERSION,
            analyzed_at=analyzed_at,
        )
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
    # 交叉分析：两模型极性分歧 → 置信打低+标 cross_disagree(进复核)；一致判负 → 提高置信
    dis = _cross({"polarity": "neg", "confidence": 0.7}, {"polarity": "neu"})
    assert dis["confidence"] <= 0.4 and dis["signals"]["cross_disagree"], dis
    agree = _cross({"polarity": "neg", "confidence": 0.5}, {"polarity": "neg"})
    assert agree["confidence"] >= 0.8 and "cross_disagree" not in agree.get("signals", {})
    assert _cross({"polarity": "pos", "confidence": 0.9}, None)["confidence"] == 0.9  # 无复核不变

    # 引擎选择回归：有 deepseek/minimax 但无 Claude → 必须 llm（曾误判成 rule 致交叉分析静默失效）
    _orig = llm.available
    llm.available = lambda p: p in ("deepseek", "minimax")
    try:
        assert _pick_engine(None) == "llm", "有LLM key时默认必须走llm，不能塌成rule"
        assert _pick_engine(False) == "rule", "显式关闭走规则(离线/自检)"
        llm.available = lambda p: False
        assert _pick_engine(None) in ("claude", "rule")             # 无LLM key时看Claude/规则
    finally:
        llm.available = _orig

    # LLM 返回残缺（缺 polarity/doc_id）被丢弃 → 下游规则兜底（不打崩）
    llm.chat_json = lambda *a, **k: {"items": [
        {"doc_id": "d1", "polarity": "neg", "confidence": 0.5, "evidence": ""},
        {"doc_id": "d2", "summary": "缺polarity"},          # 丢
        {"polarity": "pos"}]}                                # 缺doc_id 丢
    assert set(llm_extract("deepseek", [{"doc_id": "d1", "text": "x"}])) == {"d1"}

    # 关键可靠性：LLM 抽取抛异常 → analyze_pending 降级规则、全量入库，绝不 wedge
    import os as _os
    from .store import Store as _S, CleanDoc as _CD
    _os.environ["DEEPSEEK_API_KEY"] = "x"                    # 使 engine=llm
    def _boom(docs):
        raise RuntimeError("网络炸了")
    globals()["cross_extract"] = _boom
    _st = _S(":memory:")
    _st.add_clean(_CD.build(platform="weibo", entity_id="e", native_id="n1", text="发热退款避雷"))
    _n = analyze_pending(_st, now="2026-07-06T10:00:00+08:00")
    _os.environ.pop("DEEPSEEK_API_KEY")
    assert _n == 1 and _st.conn.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 1, \
        "LLM 失败必须降级规则入库，不能空转"

    # crisis_boost 每实体危机词：命中"死机"(不在全局 CRISIS_WORDS)→ 强制 crisis 信号
    import json as _json, yuqing as _pkg
    _orig_lw = _pkg.load_watch
    _pkg.load_watch = lambda *a, **k: {"entities": [{"id": "cb", "crisis_boost": ["死机"]}]}
    try:
        _st3 = _S(":memory:")
        _st3.add_clean(_CD.build(platform="weibo", entity_id="cb", native_id="n3", text="开机就死机，体验很差"))
        analyze_pending(_st3, now="2026-07-06T10:00:00+08:00")
        _sig = _json.loads(_st3.conn.execute("SELECT signals FROM features").fetchone()[0])
        assert _sig.get("crisis") is True, "crisis_boost 命中未强制 crisis"
    finally:
        _pkg.load_watch = _orig_lw

    # 多维分类接线（离线走 classify_rule）：features.signals 落 subject/stance/importance；白名单盖主体
    _st4 = _S(":memory:")
    _st4.add_account("央视新闻", "媒体", platform="weibo")
    _st4.add_clean(_CD.build(platform="weibo", entity_id="e", native_id="m1",
                             text="申请退款客服不理", author="央视新闻"))
    analyze_pending(_st4, now="2026-07-06T10:00:00+08:00")
    _s4 = _json.loads(_st4.conn.execute("SELECT signals FROM features").fetchone()[0])
    assert _s4.get("subject") == "媒体" and _s4.get("subject_conf") == 1.0, "白名单未确定性判主体"
    assert _s4.get("stance") in ("投诉", "批评") and _s4.get("importance") in ("高", "中", "低"), _s4

    print("OK analyze:", neg["polarity"], pos["polarity"], "| ABSA", sorted(aspects),
          "| 路由分层✓ | 交叉分析✓ | LLM失败降级✓ | crisis_boost✓ | 多维分类(白名单/立场/重要性)✓")
