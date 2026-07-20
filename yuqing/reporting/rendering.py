# -*- coding: utf-8 -*-
"""报告成文阶段：把聚合结果渲染为可校验的 Markdown。"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from typing import Callable, Optional

from .. import analytics, health, insights
from .aggregation import aggregate, sov


def _cite(doc_id: str) -> str:
    return f"[来源:{doc_id}]"


def _prose_stub(
    entity_name: str,
    metrics: dict,
    *,
    _cite_fn: Callable[[str], str] = _cite,
) -> str:
    """离线成文：完全由数字派生，天然不编造。Claude 路径见 _prose_claude。"""
    lines = [
        f"本周共采集 **{entity_name}** 相关内容 {metrics['n_total']} 条，"
        f"其中负面 {metrics['n_neg']} 条（占比 {metrics['neg_ratio']:.0%}）。"
    ]
    if metrics["top_neg"]:
        top = metrics["top_neg"][0]
        lines.append(
            f"最需关注的负面集中在「{top['topic_label']}」，代表帖来自 {top['platform']}"
            f"（互动 {top['likes']}赞/{top['comments']}评）{_cite_fn(top['doc_id'])}。"
        )
    if metrics["top_topics"]:
        topics = "、".join(f"{topic}({count})" for topic, count in metrics["top_topics"])
        lines.append(f"负面话题分布：{topics}。")
    lines.append(
        "**建议**：优先核查上述高风险负面帖并指派责任方；"
        "关注量骤降/失败的平台数据完整性（见顶部健康状态）。"
    )
    return "\n\n".join(lines)


def _prose_claude(entity_name: str, metrics: dict, model: str = "claude-sonnet-5") -> str:
    """Claude 只写措辞：禁止输出聚合结果外的数字，只能引用给定 evidence 的 doc_id。"""
    import anthropic

    evidence = [
        {
            "doc_id": row["doc_id"],
            "platform": row["platform"],
            "summary": row["summary"],
            "evidence": row["evidence"],
        }
        for row in metrics["top_neg"]
    ]
    facts = {
        "entity": entity_name,
        "n_total": metrics["n_total"],
        "n_neg": metrics["n_neg"],
        "neg_ratio": round(metrics["neg_ratio"], 3),
        "top_topics": metrics["top_topics"],
        "evidence": evidence,
    }
    system = (
        "你给产品运营写舆情周报的三段散文：执行摘要、话题点评、行动建议。"
        "严禁输出下方 facts 里没有的任何数字；每个结论后标注 [来源:doc_id]，"
        "doc_id 只能取自 evidence；无充分证据写'证据不足'，不要编造。"
    )
    from .. import config

    client = anthropic.Anthropic(api_key=config.resolve("ANTHROPIC_API_KEY") or None)
    message = client.messages.create(
        model=model,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
    )
    return "".join(block.text for block in message.content if block.type == "text")


@dataclass(frozen=True)
class RenderDependencies:
    """门面传入的组合点，用于保留历史 monkeypatch 行为。"""

    aggregate: Callable[..., dict] = aggregate
    sov: Callable[..., list[dict]] = sov
    cite: Callable[[str], str] = _cite
    prose_stub: Callable[[str, dict], str] = _prose_stub
    prose_claude: Callable[[str, dict], str] = _prose_claude


def build_report(
    store,
    watch: dict,
    *,
    run_id: str,
    now: str,
    health_by_platform: dict[str, str],
    use_claude: Optional[bool] = None,
    _deps: RenderDependencies | None = None,
) -> str:
    """生成截至 ``now`` 的 7 天周报 Markdown。数字来自同一时间窗口的领域聚合。"""
    dependencies = _deps or RenderDependencies()
    if use_claude is None:
        from .. import config

        use_claude = bool(config.resolve("ANTHROPIC_API_KEY"))

    report_day = _dt.date.fromisoformat(now[:10])
    since_day = (report_day - _dt.timedelta(days=6)).isoformat()
    split_day = (report_day - _dt.timedelta(days=2)).isoformat()
    parts: list[str] = []
    band = health.banner(health_by_platform)
    if band:
        parts.append(f"> {band}\n")

    for entity in watch["entities"]:
        if entity.get("type") == "competitor":
            continue
        metrics = dependencies.aggregate(store, entity["id"], since_day=since_day)
        name = (entity.get("aliases") or [entity["id"]])[0]
        parts.append(f"# {name} 舆情周报（{now[:10]}）\n")
        parts.append(
            f"> 统计周期：{since_day} 至 {now[:10]}；数据来源：{'、'.join(watch['platforms'])}；"
            f"样本 {metrics['n_total']} 条。"
            f"**公开渠道抽样、非全量，仅反映相对趋势。**\n"
        )
        if use_claude:
            try:
                prose = dependencies.prose_claude(name, metrics)
            except Exception:
                prose = dependencies.prose_stub(name, metrics)
        else:
            prose = dependencies.prose_stub(name, metrics)
        parts.append("## 执行摘要\n" + prose + "\n")

        parts.append(
            "## 核心指标\n| 指标 | 值 |\n|---|---|\n"
            f"| 总声量 | {metrics['n_total']} |\n| 负面数 | {metrics['n_neg']} |\n"
            f"| 负面占比 | {metrics['neg_ratio']:.0%} |\n"
        )

        parts.append(
            "## 平台分布\n| 平台 | 总量 | 负面 |\n|---|---|---|\n"
            + "".join(
                f"| {platform} | {values['total']} | {values['neg']} |\n"
                for platform, values in metrics["by_platform"].items()
            )
        )

        if metrics["top_neg"]:
            rows_md = ""
            for index, row in enumerate(metrics["top_neg"], 1):
                degraded = "⚠降级" if json.loads(row.get("signals") or "{}").get("influence_degraded") else ""
                rows_md += (
                    f"| {index} | {row['platform']} | {row['risk']}{degraded} | "
                    f"{(row['summary'] or '')[:30]} | [原帖]({row['url'] or '#'}) "
                    f"{dependencies.cite(row['doc_id'])} |\n"
                )
            parts.append(
                "## 负面 Top 清单（按风险分）\n| # | 平台 | 风险 | 摘要 | 溯源 |\n"
                "|---|---|---|---|---|\n" + rows_md
            )

        degraded_count = metrics["n_degraded_neg"]
        if degraded_count:
            parts.append(
                f"\n> 📉 可信度标注：本产品 {degraded_count} 条负面风险分为**影响力降级**（⚠降级）"
                "——平台无点赞/转发数据（如微博搜索），权重仅按'存在'计，不含真实传播影响力，"
                "**勿据此跨平台比较声量**，需第三方数据补齐。\n"
            )

        anomaly = analytics.negative_anomaly(store, entity["id"], since_day=since_day)
        if anomaly["anomaly"]:
            parts.append(
                f"\n> ⚠️ 负面放量异常：{anomaly['day']} 共 {anomaly['count']} 条"
                f"（稳健 z={anomaly['z']}，显著高于历史基线）\n"
            )

        aspects = analytics.aspect_breakdown(store, entity["id"], since_day=since_day)
        if aspects:
            parts.append(
                "## 方面级口碑（ABSA）\n| 方面 | 声量 | 负面 | 正面 | 负面占比 |\n"
                "|---|---|---|---|---|\n"
                + "".join(
                    f"| {aspect['aspect']} | {aspect['n']} | {aspect['neg']} | {aspect['pos']} "
                    f"| {aspect['neg_ratio']:.0%} |\n"
                    for aspect in aspects
                )
                + "\n> 按负面占比降序，恶化最快的方面排在最前。\n"
            )

        rising = [
            item
            for item in analytics.rising_topics(
                store, entity["id"], split_day, since_day=since_day,
            )
            if item["before"] > 0
        ]
        if rising:
            parts.append(
                "## 上升话题（环比放量）\n| 话题 | 上期 | 本期 | 增量 |\n|---|---|---|---|\n"
                + "".join(
                    f"| {item['topic']} | {item['before']} | {item['after']} | +{item['delta']} |\n"
                    for item in rising[:5]
                )
                + "\n"
            )

    if any(entity.get("type") == "competitor" for entity in watch["entities"]):
        rows = dependencies.sov(store, watch, since_day=since_day)
        parts.append(
            "## 竞品对标（SOV / 净情绪）\n| 对象 | 类型 | 声量 | 份额SOV | 净情绪NSR |\n"
            "|---|---|---|---|---|\n"
            + "".join(
                f"| {row['name']} | {'自有' if row['type'] == 'self' else '竞品'} | {row['mentions']} "
                f"| {row['sov']:.0%} | {row['nsr']:+.2f} |\n"
                for row in rows
            )
            + "\n> SOV=声量份额，NSR=(正-负)/总；均为**公开抽样口径**，仅供相对对比。\n"
        )

    self_ids = {
        entity["id"]
        for entity in watch["entities"]
        if entity.get("type", "self") == "self"
    }
    backlog = insights.backlog(store, self_ids, since_day=since_day)
    if backlog:
        parts.append(
            "## 用户诉求→产品需求（待人工确认，不自动建工单）\n"
            "| 类型 | 话题 | 声量 | 热度 | 代表 |\n|---|---|---|---|---|\n"
            + "".join(
                f"| {item['kind']} | {item['topic']} | {item['count']} | {item['heat']} | "
                f"[原帖]({item['url'] or '#'}) {dependencies.cite(item['sample'])} |\n"
                for item in backlog[:8]
            )
        )

    parts.append("\n---\n*附：情绪判定含中文反讽误判风险，关键负面结论建议人工抽检。*")
    markdown = "\n".join(parts)
    store.save_report(run_id, now, markdown)
    return markdown


__all__ = ["RenderDependencies", "build_report"]
