# -*- coding: utf-8 -*-
"""报告校验与投递阶段：引用完整性、飞书 payload 和报告链接。"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Callable, Optional

_CITE = re.compile(r"\[来源:([0-9a-f]{6,16})\]")


def validate_citations(markdown: str, store) -> list[str]:
    """返回 markdown 里引用了但库中不存在的 doc_id（应为空）。"""
    ids = set(_CITE.findall(markdown))
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    have = {
        row["doc_id"]
        for row in store.conn.execute(
            f"SELECT doc_id FROM clean WHERE doc_id IN ({placeholders})",
            tuple(ids),
        )
    }
    return sorted(ids - have)


def push_feishu(markdown: str, webhook: Optional[str] = None, *, title: str = "舆情周报") -> bool:
    """推送到飞书机器人。无 webhook 则跳过（返回 False）。"""
    from .. import config

    webhook = webhook or config.resolve("FEISHU_WEBHOOK")
    if not webhook:
        return False
    body = json.dumps({
        "msg_type": "text",
        "content": {"text": f"【{title}】\n{markdown[:3000]}"},
    }).encode()
    request = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status == 200


def _feishu_send_card(card: dict, webhook: Optional[str]) -> bool:
    """把 card 主体包成 interactive 消息发到飞书群机器人。无 webhook 则跳过。"""
    from .. import config

    webhook = webhook or config.resolve("FEISHU_WEBHOOK")
    if not webhook:
        return False
    body = json.dumps({"msg_type": "interactive", "card": card}, ensure_ascii=False).encode()
    request = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status == 200


def push_feishu_card(
    title: str,
    summary_md: str,
    detail_url: str = "",
    *,
    webhook: Optional[str] = None,
    template: str = "blue",
    _send_card: Callable[[dict, Optional[str]], bool] | None = None,
) -> bool:
    """推送飞书交互式报告卡片。"""
    elements: list[dict] = [{"tag": "markdown", "content": (summary_md or "")[:2000]}]
    if detail_url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看完整报告"},
                "type": "primary",
                "url": detail_url,
            }],
        })
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": title[:100]},
            "template": template,
        },
        "elements": elements,
    }
    return (_send_card or _feishu_send_card)(card, webhook)


def push_feishu_alert_card(
    alerts: list[dict],
    *,
    webhook: Optional[str] = None,
    _send_card: Callable[[dict, Optional[str]], bool] | None = None,
) -> bool:
    """推送飞书预警卡片；P0 为红色，其余为橙色。"""
    if not alerts:
        return False
    template = "red" if any(alert.get("level") == "P0" for alert in alerts) else "orange"
    elements: list[dict] = []
    for index, alert in enumerate(alerts):
        if index:
            elements.append({"tag": "hr"})
        level = alert.get("level", "")
        platform = alert.get("platform", "")
        summary = (alert.get("summary") or "")[:80]
        if alert.get("kind") == "health":
            elements.append({
                "tag": "markdown",
                "content": f"**[{level}] 数据健康 · {platform}**\n{summary}",
            })
            continue
        pending = " · 待人工确认" if alert.get("status") == "pending_confirmation" else ""
        incident = f"\n事件：`{alert['incident_id']}`" if alert.get("incident_id") else ""
        elements.append({
            "tag": "markdown",
            "content": (
                f"**[{level}] {platform} · 风险分 {alert.get('risk', '—')}{pending}**\n"
                f"{summary}{incident}"
            ),
        })
        if alert.get("url"):
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看原帖"},
                    "type": "default",
                    "url": alert["url"],
                }],
            })
    card = {
        "header": {
            "title": {"tag": "plain_text", "content": f"🚨 舆情实时预警 {len(alerts)} 条"},
            "template": template,
        },
        "elements": elements,
    }
    return (_send_card or _feishu_send_card)(card, webhook)


def report_url(run_id: str) -> str:
    """报告 HTML 页面地址（看板 /report），base 可配 DASHBOARD_URL，默认本机。"""
    from .. import config

    base = config.resolve("DASHBOARD_URL") or "http://127.0.0.1:8000"
    return f"{base.rstrip('/')}/report?run_id={run_id}"


def push_report_notice(
    run_id: str,
    *,
    title: str = "舆情报告",
    _push_card: Callable[..., bool] | None = None,
    _report_url: Callable[[str], str] | None = None,
) -> bool:
    """推送精简报告通知，正文仍由 HTML 报告页承载。"""
    summary = f"舆情报告已更新，点击下方按钮查看完整报告。\n\n**run_id**：`{run_id}`"
    return (_push_card or push_feishu_card)(
        title,
        summary,
        (_report_url or report_url)(run_id),
    )


__all__ = [
    "validate_citations",
    "push_feishu",
    "push_feishu_card",
    "push_feishu_alert_card",
    "report_url",
    "push_report_notice",
]
