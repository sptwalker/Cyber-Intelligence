# -*- coding: utf-8 -*-
"""Compatibility checks for extracted shared contracts."""

from __future__ import annotations

import unittest

from yuqing import collect, dashboard, dashboard_runtime, dashboard_views
from yuqing.api import overview
from yuqing.api.entities import configured_entities, resolve_entity
from yuqing.normalization import normalize


class ModuleBoundaryTest(unittest.TestCase):
    def test_legacy_imports_reexport_extracted_contracts(self) -> None:
        self.assertIs(collect.normalize, normalize)
        self.assertIs(overview.configured_entities, configured_entities)
        self.assertIs(overview.resolve_entity, resolve_entity)

    def test_normalization_contract_is_unchanged(self) -> None:
        document = normalize(
            "weibo",
            "product",
            {
                "id": "post-1",
                "text": "申请退款",
                "user": {"nickname": "用户", "followers": "1.2万"},
                "likes": "10w+",
            },
            backend="fixture",
            fetched_at="2026-07-20T00:00:00+08:00",
        )
        self.assertEqual("post-1", document.native_id)
        self.assertEqual(12000, document.author_followers)
        self.assertEqual(100000, document.likes)
        self.assertTrue(document.is_complaint)

    def test_dashboard_remains_a_compatibility_facade(self) -> None:
        self.assertIs(dashboard.render_index, dashboard_views.render_index)
        self.assertIs(dashboard.render_dash, dashboard_views.render_dash)
        self.assertIs(dashboard.md_to_html, dashboard_views.md_to_html)
        self.assertIs(dashboard._run_state, dashboard_runtime._run_state)
        self.assertIs(
            dashboard._start_background_run,
            dashboard_runtime._start_background_run,
        )
        self.assertEqual("yuqing.dashboard_http", dashboard.make_handler(":memory:").__module__)


if __name__ == "__main__":
    unittest.main()
