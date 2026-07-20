# -*- coding: utf-8 -*-
"""Packaging regressions for the modularized ``yuqing`` application."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingTest(unittest.TestCase):
    def test_all_yuqing_subpackages_are_discovered(self) -> None:
        config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.setuptools.packages.find]", config)
        self.assertIn('include = ["yuqing*"]', config)

        expected = {
            path.relative_to(ROOT).as_posix().replace("/", ".")
            for path in (ROOT / "yuqing").rglob("*")
            if path.is_dir() and (path / "__init__.py").is_file()
        }
        self.assertTrue({
            "yuqing.api",
            "yuqing.collection",
            "yuqing.dashboard_http_parts",
            "yuqing.dashboard_legacy",
            "yuqing.dashboard_routes",
            "yuqing.reporting",
            "yuqing.storage",
        } <= expected)


if __name__ == "__main__":
    unittest.main()
