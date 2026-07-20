# -*- coding: utf-8 -*-
"""Compatibility facade for the legacy stdlib dashboard.

The renderers are retained for existing URLs and integrations, but their
implementations now live in :mod:`yuqing.dashboard_legacy` by page
responsibility.  New production UI work should target ``yuqing/web/workbench``;
legacy routes remain supported during that migration.
"""

from __future__ import annotations

from .dashboard_legacy.access import render_accounts, render_login
from .dashboard_legacy.analytics import (
    _self_entities,
    chart_data,
    render_dash,
    render_exec,
)
from .dashboard_legacy.annotation import render_annotate
from .dashboard_legacy.common import (
    _BOLD,
    _CSS,
    _LINK,
    _STATE_CN,
    _badge,
    _inline,
    _md_table,
    _page,
    _safe_href,
    md_to_html,
)
from .dashboard_legacy.configuration import render_config, render_watch
from .dashboard_legacy.keywords import render_keywords
from .dashboard_legacy.overview import render_index
from .dashboard_legacy.reporting import render_report

__all__ = [
    "chart_data",
    "md_to_html",
    "render_accounts",
    "render_annotate",
    "render_config",
    "render_dash",
    "render_exec",
    "render_index",
    "render_keywords",
    "render_login",
    "render_report",
    "render_watch",
]
