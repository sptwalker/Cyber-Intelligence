# -*- coding: utf-8 -*-
"""Legacy overview page renderer."""

from __future__ import annotations

import html
import json
from collections import Counter

from ..store import Store
from .common import _badge, _page, _safe_href


def render_index(store: Store) -> str:
    """Render the read-only collection health and negative-top overview."""
    conn = store.conn

    latest: dict[str, dict] = {}
    for row in conn.execute("SELECT platform,health,status,n_fetched,ts,note FROM run_log ORDER BY ts DESC"):
        latest.setdefault(row["platform"], dict(row))
    health_rows = "".join(
        f"<tr><td>{html.escape(platform)}</td><td>{_badge(value['health'])}</td>"
        f"<td>{value['n_fetched']}</td><td class=muted>{html.escape(value['ts'])}</td>"
        f"<td class=muted>{html.escape(value['note'] or '')}</td></tr>"
        for platform, value in sorted(latest.items())
    ) or "<tr><td colspan=5 class=muted>暂无采集记录</td></tr>"
    any_bad = any(value["health"] != "ok" for value in latest.values())
    banner = (
        "<p style='color:#cf222e;font-weight:600'>⚠️ 有平台采集异常，下方报告数据可能不全，请人工核查。</p>"
        if any_bad else ""
    )
    pending = store.pending_review_count()
    review_line = (
        f"<p class=muted>📋 待人工复核 <b>{pending}</b> 条"
        f"（<code>python -m yuqing.cli review</code>）</p>" if pending else ""
    )
    pending_incidents = len(store.list_incidents(status="pending_confirmation", limit=1000))
    incident_line = (
        f"<p style='color:#cf222e;font-weight:600'>🚨 待确认危机事件 <b>{pending_incidents}</b> 条 "
        f"（<code>python -m yuqing.cli incidents pending_confirmation</code>）</p>"
        if pending_incidents else ""
    )
    annotated = store.annotated_count()
    annotate_line = f"<p class=muted>📝 已标注 <b>{annotated}</b> 条 · <a href='/annotate'>去标注</a></p>"

    subject_counts, stance_counts = Counter(), Counter()
    for row in conn.execute("SELECT signals FROM features WHERE signals LIKE '%stance%'"):
        try:
            signals = json.loads(row["signals"])
            if signals.get("subject"):
                subject_counts[signals["subject"]] += 1
            if signals.get("stance"):
                stance_counts[signals["stance"]] += 1
        except Exception:
            pass
    if stance_counts:
        distribution = (
            "<p class=muted>🏷️ 主体：" + " · ".join(f"{key} {value}" for key, value in subject_counts.most_common())
            + "　｜　立场：" + " · ".join(f"{key} {value}" for key, value in stance_counts.most_common()) + "</p>"
        )
    else:
        distribution = "<p class=muted>🏷️ 多维标签：暂无（标注样本后重跑分析生效）</p>"
    distribution_line = incident_line + annotate_line + distribution

    from .. import config as cfg
    mode = cfg.mode()
    mode_label = "训练 training" if mode == "training" else "日常 daily"
    mode_hint = "跑批不推飞书，适合调参/标注期" if mode == "training" else "跑批推报告到飞书"

    trend = conn.execute(
        "SELECT substr(c.fetched_at,1,10) day, "
        "SUM(CASE WHEN f.polarity='neg' THEN 1 ELSE 0 END) neg, COUNT(*) total "
        "FROM clean c JOIN features f USING(doc_id) GROUP BY day ORDER BY day"
    ).fetchall()
    peak = max((row["neg"] for row in trend), default=0) or 1
    trend_rows = "".join(
        f"<tr><td class=muted>{html.escape(row['day'] or '')}</td><td>{row['neg']}</td>"
        f"<td>{row['total']}</td><td>{'█' * round(20 * row['neg'] / peak)}</td></tr>"
        for row in trend
    ) or "<tr><td colspan=4 class=muted>暂无数据</td></tr>"

    report_rows = "".join(
        f"<tr><td><a href='/report?run_id={html.escape(row['run_id'])}'>{html.escape(row['run_id'])}</a></td>"
        f"<td class=muted>{html.escape(row['created_at'])}</td></tr>"
        for row in conn.execute("SELECT run_id,created_at FROM reports ORDER BY created_at DESC LIMIT 50")
    ) or "<tr><td colspan=2 class=muted>暂无报告</td></tr>"

    neg_rows = "".join(
        f"<tr><td>{index}</td><td>{html.escape(row['platform'])}</td><td>{row['risk']}</td>"
        f"<td>{html.escape((row['text'] or '')[:50])}</td>"
        f"<td><a href='{_safe_href(row['url'])}' target=_blank rel=noopener>原帖</a> "
        f"<span class=muted>{html.escape(row['doc_id'])}</span></td></tr>"
        for index, row in enumerate(conn.execute(
            "SELECT c.platform,c.text,c.url,c.doc_id,f.risk FROM clean c JOIN features f USING(doc_id)"
            " WHERE f.polarity='neg' ORDER BY f.risk DESC LIMIT 20"
        ), 1)
    ) or "<tr><td colspan=5 class=muted>暂无负面</td></tr>"

    body = (
        "<h1>舆情监控看板 <span class=muted>（数据只读）</span> "
        "<a href='/login' style='font-size:14px'>🔐 登录与采集</a> "
        "<a href='/annotate' style='font-size:14px'>📝 标注</a> "
        "<a href='/watch' style='font-size:14px'>🎯 监控配置</a> "
        "<a href='/keywords' style='font-size:14px'>📖 关键词库</a> "
        "<a href='/exec' style='font-size:14px'>📊 高管概览</a> "
        "<a href='/dash' style='font-size:14px'>📈 战情室</a> "
        "<a href='/config' style='font-size:14px'>⚙️ 系统配置</a></h1>" + banner + review_line + distribution_line
        + f"<p class=muted>运行模式：<b>{mode_label}</b>（{mode_hint}）</p>"
        + "<h2>采集健康（各平台最近一次）</h2>"
        + "<table><tr><th>平台</th><th>状态</th><th>条数</th><th>时间</th><th>备注</th></tr>" + health_rows + "</table>"
        + "<h2>负面日趋势</h2><table><tr><th>日期</th><th>负面</th><th>总量</th><th></th></tr>" + trend_rows + "</table>"
        + "<h2>报告历史</h2><table><tr><th>run_id</th><th>生成时间</th></tr>" + report_rows + "</table>"
        + "<h2>负面 Top（按风险分）</h2>"
        + "<table><tr><th>#</th><th>平台</th><th>风险</th><th>摘要</th><th>溯源</th></tr>" + neg_rows + "</table>"
    )
    return _page("舆情监控看板", body)
