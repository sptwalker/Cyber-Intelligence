# -*- coding: utf-8 -*-
"""报告兼容门面。

历史调用继续从本模块导入；实现分别位于 ``reporting.aggregation``、
``reporting.rendering`` 与 ``reporting.delivery``。门面保留组合点，使已有测试和
集成代码对本模块函数的 monkeypatch 仍然生效。
"""

from __future__ import annotations

from typing import Optional

from . import analytics, health, insights  # 保留历史模块属性与 monkeypatch 兼容
from .reporting import aggregation as _aggregation
from .reporting import delivery as _delivery
from .reporting import rendering as _rendering

_CITE = _delivery._CITE


def aggregate(store, entity_id: str, *, since_day: str | None = None) -> dict:
    """从 features 算聚合指标（全部数字的唯一来源）。"""
    return _aggregation.aggregate(store, entity_id, since_day=since_day)


def sov(store, watch: dict, *, since_day: str | None = None) -> list[dict]:
    """竞品对标：声量份额(SOV) + 净情绪(NSR=(正-负)/总)。"""
    return _aggregation.sov(store, watch, since_day=since_day)


def _cite(doc_id: str) -> str:
    return _rendering._cite(doc_id)


def _prose_stub(entity_name: str, m: dict) -> str:
    """离线成文：完全由数字派生，天然不编造。"""
    return _rendering._prose_stub(entity_name, m, _cite_fn=_cite)


def _prose_claude(entity_name: str, m: dict, model: str = "claude-sonnet-5") -> str:
    """Claude 只写措辞：禁止输出聚合结果外的数字。"""
    return _rendering._prose_claude(entity_name, m, model=model)


def build_report(
    store,
    watch: dict,
    *,
    run_id: str,
    now: str,
    health_by_platform: dict[str, str],
    use_claude: Optional[bool] = None,
) -> str:
    """生成截至 ``now`` 的 7 天周报 Markdown。数字来自同一时间窗口的领域聚合。"""
    dependencies = _rendering.RenderDependencies(
        aggregate=aggregate,
        sov=sov,
        cite=_cite,
        prose_stub=_prose_stub,
        prose_claude=_prose_claude,
    )
    return _rendering.build_report(
        store,
        watch,
        run_id=run_id,
        now=now,
        health_by_platform=health_by_platform,
        use_claude=use_claude,
        _deps=dependencies,
    )


def validate_citations(markdown: str, store) -> list[str]:
    """返回 markdown 里引用了但库中不存在的 doc_id（应为空）。"""
    return _delivery.validate_citations(markdown, store)


def push_feishu(markdown: str, webhook: Optional[str] = None, *, title: str = "舆情周报") -> bool:
    """推送到飞书机器人。无 webhook 则跳过（返回 False）。"""
    return _delivery.push_feishu(markdown, webhook, title=title)


def _feishu_send_card(card: dict, webhook: Optional[str]) -> bool:
    """发送飞书 interactive 卡片。"""
    return _delivery._feishu_send_card(card, webhook)


def push_feishu_card(
    title: str,
    summary_md: str,
    detail_url: str = "",
    *,
    webhook: Optional[str] = None,
    template: str = "blue",
) -> bool:
    """推送飞书交互式报告卡片。"""
    return _delivery.push_feishu_card(
        title,
        summary_md,
        detail_url,
        webhook=webhook,
        template=template,
        _send_card=_feishu_send_card,
    )


def push_feishu_alert_card(alerts: list[dict], *, webhook: Optional[str] = None) -> bool:
    """推送飞书预警卡片；P0 为红色，其余为橙色。"""
    return _delivery.push_feishu_alert_card(
        alerts,
        webhook=webhook,
        _send_card=_feishu_send_card,
    )


def report_url(run_id: str) -> str:
    """报告 HTML 页面地址（看板 /report），base 可配 DASHBOARD_URL，默认本机。"""
    return _delivery.report_url(run_id)


def push_report_notice(run_id: str, *, title: str = "舆情报告") -> bool:
    """推送精简报告通知，正文仍由 HTML 报告页承载。"""
    return _delivery.push_report_notice(
        run_id,
        title=title,
        _push_card=push_feishu_card,
        _report_url=report_url,
    )


__all__ = [
    "aggregate",
    "sov",
    "build_report",
    "validate_citations",
    "push_feishu",
    "push_feishu_card",
    "push_feishu_alert_card",
    "report_url",
    "push_report_notice",
]
