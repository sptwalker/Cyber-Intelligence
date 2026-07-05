# -*- coding: utf-8 -*-
"""报告层：数字代码注入 + LLM 只写措辞 + 引用校验器 + 飞书推送。

可信度命门（立项规划）：所有数值/链接由代码从聚合(aggregate)结果注入，Claude 只写
执行摘要/话题点评/行动建议三段散文；每条结论标 [来源:doc_id]；生成后
引用校验器逐一比对 doc_id 真实性，不存在即判不合格。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional

from . import analytics
from . import health

_CITE = re.compile(r"\[来源:([0-9a-f]{6,16})\]")


def aggregate(store, entity_id: str) -> dict:
    """从 features 算聚合指标（全部数字的唯一来源）。"""
    rows = [dict(r) for r in store.joined(entity_id)]
    n = len(rows)
    negs = sorted((r for r in rows if r["polarity"] == "neg"), key=lambda r: r["risk"], reverse=True)
    by_platform: dict[str, dict] = {}
    for r in rows:
        p = by_platform.setdefault(r["platform"], {"total": 0, "neg": 0})
        p["total"] += 1
        p["neg"] += r["polarity"] == "neg"
    topics: dict[str, int] = {}
    for r in negs:
        topics[r["topic_label"]] = topics.get(r["topic_label"], 0) + 1
    return {
        "n_total": n, "n_neg": len(negs),
        "neg_ratio": (len(negs) / n) if n else 0.0,
        "by_platform": by_platform,
        "top_neg": negs[:10],
        "top_topics": sorted(topics.items(), key=lambda kv: kv[1], reverse=True)[:5],
    }


def _cite(doc_id: str) -> str:
    return f"[来源:{doc_id}]"


def sov(store, watch: dict) -> list[dict]:
    """竞品对标：声量份额(SOV) + 净情绪(NSR=(正-负)/总)。自有 + 竞品同口径。"""
    rows = [dict(r) for r in store.joined()]
    per: dict[str, dict] = {}
    for r in rows:
        d = per.setdefault(r["entity_id"], {"n": 0, "pos": 0, "neg": 0})
        d["n"] += 1
        d["pos"] += r["polarity"] == "pos"
        d["neg"] += r["polarity"] == "neg"
    total = sum(d["n"] for d in per.values()) or 1
    out = []
    for ent in watch["entities"]:
        d = per.get(ent["id"], {"n": 0, "pos": 0, "neg": 0})
        out.append({"id": ent["id"], "name": ent.get("aliases", [ent["id"]])[0],
                    "type": ent.get("type", "self"), "mentions": d["n"],
                    "sov": d["n"] / total,
                    "nsr": ((d["pos"] - d["neg"]) / d["n"]) if d["n"] else 0.0})
    return sorted(out, key=lambda x: x["mentions"], reverse=True)


def _prose_stub(entity_name: str, m: dict) -> str:
    """离线成文：完全由数字派生，天然不编造。Claude 路径见 _prose_claude。"""
    lines = [f"本周共采集 **{entity_name}** 相关内容 {m['n_total']} 条，"
             f"其中负面 {m['n_neg']} 条（占比 {m['neg_ratio']:.0%}）。"]
    if m["top_neg"]:
        t = m["top_neg"][0]
        lines.append(f"最需关注的负面集中在「{t['topic_label']}」，代表帖来自 {t['platform']}"
                     f"（互动 {t['likes']}赞/{t['comments']}评）{_cite(t['doc_id'])}。")
    if m["top_topics"]:
        tp = "、".join(f"{k}({v})" for k, v in m["top_topics"])
        lines.append(f"负面话题分布：{tp}。")
    lines.append("**建议**：优先核查上述高风险负面帖并指派责任方；"
                 "关注量骤降/失败的平台数据完整性（见顶部健康状态）。")
    return "\n\n".join(lines)


def _prose_claude(entity_name: str, m: dict, model: str = "claude-sonnet-5") -> str:
    """Claude 只写措辞：禁止输出聚合结果外的数字，只能引用给定 evidence 的 doc_id。"""
    import anthropic

    evidence = [{"doc_id": r["doc_id"], "platform": r["platform"], "summary": r["summary"],
                 "evidence": r["evidence"]} for r in m["top_neg"]]
    facts = {"entity": entity_name, "n_total": m["n_total"], "n_neg": m["n_neg"],
             "neg_ratio": round(m["neg_ratio"], 3),
             "top_topics": m["top_topics"], "evidence": evidence}
    system = ("你给产品运营写舆情周报的三段散文：执行摘要、话题点评、行动建议。"
              "严禁输出下方 facts 里没有的任何数字；每个结论后标注 [来源:doc_id]，"
              "doc_id 只能取自 evidence；无充分证据写'证据不足'，不要编造。")
    msg = anthropic.Anthropic().messages.create(
        model=model, max_tokens=1500, system=system,
        messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def build_report(store, watch: dict, *, run_id: str, now: str,
                 health_by_platform: dict[str, str], use_claude: Optional[bool] = None) -> str:
    """生成周报 Markdown。数字全部来自 aggregate，散文来自 stub/Claude。"""
    if use_claude is None:
        use_claude = bool(os.getenv("ANTHROPIC_API_KEY"))

    parts: list[str] = []
    band = health.banner(health_by_platform)
    if band:
        parts.append(f"> {band}\n")

    for ent in watch["entities"]:
        if ent.get("type") == "competitor":
            continue  # MVP 只报自有；竞品 SOV 是 Phase 1
        m = aggregate(store, ent["id"])
        name = ent.get("aliases", [ent["id"]])[0]
        parts.append(f"# {name} 舆情周报（{now[:10]}）\n")
        parts.append(f"> 数据来源：{'、'.join(watch['platforms'])}；样本 {m['n_total']} 条。"
                     f"**公开渠道抽样、非全量，仅反映相对趋势。**\n")
        prose = _prose_claude(name, m) if use_claude else _prose_stub(name, m)
        parts.append("## 执行摘要\n" + prose + "\n")

        parts.append("## 核心指标\n| 指标 | 值 |\n|---|---|\n"
                     f"| 总声量 | {m['n_total']} |\n| 负面数 | {m['n_neg']} |\n"
                     f"| 负面占比 | {m['neg_ratio']:.0%} |\n")

        parts.append("## 平台分布\n| 平台 | 总量 | 负面 |\n|---|---|---|\n" + "".join(
            f"| {p} | {v['total']} | {v['neg']} |\n" for p, v in m["by_platform"].items()))

        if m["top_neg"]:
            parts.append("## 负面 Top 清单（按风险分）\n| # | 平台 | 风险 | 摘要 | 溯源 |\n|---|---|---|---|---|\n"
                         + "".join(
                f"| {i} | {r['platform']} | {r['risk']} | {(r['summary'] or '')[:30]} | "
                f"[原帖]({r['url'] or '#'}) {_cite(r['doc_id'])} |\n"
                for i, r in enumerate(m["top_neg"], 1)))

        anom = analytics.negative_anomaly(store, ent["id"])
        if anom["anomaly"]:
            parts.append(f"\n> ⚠️ 负面放量异常：{anom['day']} 共 {anom['count']} 条"
                         f"（稳健 z={anom['z']}，显著高于历史基线）\n")

        ab = analytics.aspect_breakdown(store, ent["id"])
        if ab:
            parts.append("## 方面级口碑（ABSA）\n| 方面 | 声量 | 负面 | 正面 | 负面占比 |\n|---|---|---|---|---|\n"
                         + "".join(
                f"| {a['aspect']} | {a['n']} | {a['neg']} | {a['pos']} | {a['neg_ratio']:.0%} |\n"
                for a in ab) + "\n> 按负面占比降序，恶化最快的方面排在最前。\n")

        # 上升话题：仅列有历史基线(上期>0)且本期放量的，避免首次运行全部误判为"上升"
        rt = [x for x in analytics.rising_topics(store, ent["id"], now[:10]) if x["before"] > 0]
        if rt:
            parts.append("## 上升话题（环比放量）\n| 话题 | 上期 | 本期 | 增量 |\n|---|---|---|---|\n"
                         + "".join(f"| {x['topic']} | {x['before']} | {x['after']} | +{x['delta']} |\n"
                                   for x in rt[:5]) + "\n")

    if any(e.get("type") == "competitor" for e in watch["entities"]):
        rows = sov(store, watch)
        parts.append("## 竞品对标（SOV / 净情绪）\n| 对象 | 类型 | 声量 | 份额SOV | 净情绪NSR |\n|---|---|---|---|---|\n"
                     + "".join(
            f"| {r['name']} | {'自有' if r['type']=='self' else '竞品'} | {r['mentions']} "
            f"| {r['sov']:.0%} | {r['nsr']:+.2f} |\n" for r in rows)
                     + "\n> SOV=声量份额，NSR=(正-负)/总；均为**公开抽样口径**，仅供相对对比。\n")

    parts.append("\n---\n*附：情绪判定含中文反讽误判风险，关键负面结论建议人工抽检。*")
    md = "\n".join(parts)
    store.save_report(run_id, now, md)
    return md


def validate_citations(markdown: str, store) -> list[str]:
    """返回 markdown 里引用了但库中不存在的 doc_id（应为空）。"""
    ids = set(_CITE.findall(markdown))
    if not ids:
        return []
    have = {r["doc_id"] for r in store.conn.execute(
        "SELECT doc_id FROM clean WHERE doc_id IN (%s)" % ",".join("?" * len(ids)), tuple(ids))}
    return sorted(ids - have)


def push_feishu(markdown: str, webhook: Optional[str] = None, *, title: str = "舆情周报") -> bool:
    """推送到飞书机器人。无 webhook 则跳过（返回 False）。"""
    webhook = webhook or os.getenv("FEISHU_WEBHOOK")
    if not webhook:
        return False
    body = json.dumps({"msg_type": "text",
                       "content": {"text": f"【{title}】\n{markdown[:3000]}"}}).encode()
    req = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status == 200
