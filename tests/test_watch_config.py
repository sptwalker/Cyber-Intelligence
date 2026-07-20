# -*- coding: utf-8 -*-
"""Monitoring configuration contract tests."""

from __future__ import annotations

import unittest

from yuqing import dashboard, load_watch as legacy_load_watch, watch_path as legacy_watch_path
from yuqing.watch_config import load_watch, validate_watch, watch_path


class WatchConfigValidationTest(unittest.TestCase):
    def test_package_root_reexports_loading_contracts(self) -> None:
        self.assertIs(legacy_load_watch, load_watch)
        self.assertIs(legacy_watch_path, watch_path)

    def test_valid_configuration_and_dashboard_compatibility_alias(self) -> None:
        text = """
platforms: [weibo, zhihu]
entities:
  - id: product
    aliases: [Product, 产品]
"""
        expected = (True, "✓ 合法：2 个平台 / 1 个实体")
        self.assertEqual(expected, validate_watch(text))
        self.assertEqual(expected, dashboard._validate_watch(text))

    def test_invalid_shapes_keep_existing_messages(self) -> None:
        cases = (
            ("[]", "顶层必须是映射"),
            ("entities: [{id: product}]", "缺少 platforms 列表"),
            ("platforms: [weibo]", "缺少 entities 列表"),
            ("platforms: [weibo]\nentities: [{}]", "第 1 个 entity 缺少 id"),
            (
                "platforms: [weibo]\nentities: [{id: product, aliases: Product}]",
                "entity product 的 aliases 必须是列表",
            ),
        )
        for text, message in cases:
            with self.subTest(message=message):
                valid, actual = validate_watch(text)
                self.assertFalse(valid)
                self.assertIn(message, actual)


if __name__ == "__main__":
    unittest.main()
