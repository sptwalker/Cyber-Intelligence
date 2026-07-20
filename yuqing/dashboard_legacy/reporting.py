# -*- coding: utf-8 -*-
"""Legacy report page renderer."""

from __future__ import annotations

from ..store import Store
from .common import _page, md_to_html


def render_report(store: Store, run_id: str) -> str:
    row = store.conn.execute("SELECT markdown FROM reports WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return _page("未找到", "<p>未找到该报告。<a href='/'>返回</a></p>")
    return _page(run_id, f"<p><a href='/'>← 返回看板</a></p>{md_to_html(row['markdown'])}")
