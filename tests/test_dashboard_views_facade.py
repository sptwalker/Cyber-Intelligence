# -*- coding: utf-8 -*-
"""Compatibility and rendering regressions for the split legacy dashboard."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yuqing
from yuqing import dashboard, dashboard_views
from yuqing.dashboard_legacy import access, analytics, annotation, common
from yuqing.dashboard_legacy import configuration, keywords, overview, reporting
from yuqing.store import Store


WATCH = {
    "platforms": ["weibo"],
    "entities": [
        {"id": "brand", "type": "self", "aliases": ["品牌"]},
        {"id": "rival", "type": "competitor", "aliases": ["竞品"]},
    ],
}


class DashboardViewsFacadeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / "dashboard.db"))

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_facade_preserves_public_and_private_object_identity(self) -> None:
        exports = {
            "render_index": overview.render_index,
            "render_dash": analytics.render_dash,
            "render_exec": analytics.render_exec,
            "chart_data": analytics.chart_data,
            "_self_entities": analytics._self_entities,
            "render_report": reporting.render_report,
            "render_keywords": keywords.render_keywords,
            "render_login": access.render_login,
            "render_accounts": access.render_accounts,
            "render_watch": configuration.render_watch,
            "render_config": configuration.render_config,
            "render_annotate": annotation.render_annotate,
            "md_to_html": common.md_to_html,
            "_BOLD": common._BOLD,
            "_CSS": common._CSS,
            "_LINK": common._LINK,
            "_STATE_CN": common._STATE_CN,
            "_badge": common._badge,
            "_inline": common._inline,
            "_md_table": common._md_table,
            "_page": common._page,
            "_safe_href": common._safe_href,
        }
        for name, implementation in exports.items():
            with self.subTest(name=name):
                self.assertIs(implementation, getattr(dashboard_views, name))
                self.assertIs(getattr(dashboard_views, name), getattr(dashboard, name))

    def test_legacy_root_watch_monkeypatches_reach_extracted_pages(self) -> None:
        watch_file = Path(self.tmp.name) / "watch.yaml"
        watch_file.write_text("platforms: [weibo]\nentities: []\n", encoding="utf-8")
        with mock.patch.object(yuqing, "load_watch", return_value=WATCH) as load, \
                mock.patch.object(yuqing, "watch_path", return_value=str(watch_file)) as path:
            dash = dashboard_views.render_dash(self.store, "", None)
            watch_page = dashboard_views.render_watch()

        load.assert_called_once_with()
        path.assert_called_once_with()
        self.assertIn("/dash?entity=brand", dash)
        self.assertIn("品牌", dash)
        self.assertIn("platforms: [weibo]", watch_page)

    def test_legacy_pages_keep_existing_api_urls_and_client_hooks(self) -> None:
        with mock.patch.object(yuqing, "load_watch", return_value=WATCH):
            pages = {
                "login": dashboard_views.render_login(),
                "dash": dashboard_views.render_dash(self.store, "brand", WATCH),
                "keywords": dashboard_views.render_keywords(self.store, {}),
                "accounts": dashboard_views.render_accounts(self.store),
                "annotate": dashboard_views.render_annotate(self.store, {}),
            }

        expected_routes = {
            "login": ("/api/login/status", "/api/login/open", "/api/run", "/api/run/stop"),
            "dash": ("/chart-data?entity=", "https://cdn.jsdelivr.net/npm/chart.js@4"),
            "keywords": ("/api/keywords", "/api/run/status"),
            "accounts": ("/api/accounts",),
            "annotate": ("/api/annotate/queue", "/api/annotate"),
        }
        for page_name, routes in expected_routes.items():
            for route in routes:
                with self.subTest(page=page_name, route=route):
                    self.assertIn(route, pages[page_name])

    def test_markdown_and_href_escaping_contract_is_unchanged(self) -> None:
        rendered = dashboard_views.md_to_html(
            "# <标题>\n[安全](https://example.com/'x)\n[危险](javascript:alert(1))"
        )
        self.assertIn("<h1>&lt;标题&gt;</h1>", rendered)
        self.assertIn("href='https://example.com/&#x27;x'", rendered)
        self.assertNotIn("javascript:", rendered)
        self.assertEqual("#", dashboard_views._safe_href("javascript:alert(1)"))


if __name__ == "__main__":
    unittest.main()
