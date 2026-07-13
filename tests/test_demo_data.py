# -*- coding: utf-8 -*-
"""Safe workbench demo database baseline tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from populate_demo_data import build_demo_database
from yuqing.store import Store


WATCH = {
    "platforms": ["weibo", "zhihu", "xiaohongshu", "douyin", "heimao"],
    "entities": [{"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]}],
}


class DemoDataTest(unittest.TestCase):
    def test_builds_independent_operable_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workbench-demo.db"
            with mock.patch("populate_demo_data.load_watch", return_value=WATCH):
                result = build_demo_database(path)
            self.assertEqual("youdoo", result["entity_id"])
            self.assertGreater(result["pending_reviews"], 0)

            store = Store(path)
            try:
                self.assertEqual(1, store.conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
                self.assertEqual(1, store.conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0])
                self.assertEqual(len(WATCH["platforms"]), store.conn.execute(
                    "SELECT COUNT(*) FROM run_log"
                ).fetchone()[0])
            finally:
                store.close()

    def test_refuses_existing_file_and_default_production_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "demo.db"
            existing.write_text("keep", encoding="utf-8")
            with self.assertRaises(SystemExit):
                build_demo_database(existing)
            self.assertEqual("keep", existing.read_text(encoding="utf-8"))

            production = Path(tmp) / "yuqing.db"
            with self.assertRaises(SystemExit):
                build_demo_database(production)
            self.assertFalse(os.path.exists(production))


if __name__ == "__main__":
    unittest.main()
