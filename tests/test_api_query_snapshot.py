# -*- coding: utf-8 -*-
"""Regression tests for request-scoped API query snapshots."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from yuqing.api.analysis import build_analysis
from yuqing.api.overview import build_overview
from yuqing.api.query_snapshot import request_query_snapshot
from yuqing.store import CleanDoc, Store


WATCH = {
    "platforms": ["weibo"],
    "entities": [{"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]}],
}


class CountingStore:
    """Small test decorator that counts the expensive joined reads."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.joined_calls = 0
        self.joined_with_entities_calls = 0

    def joined(self, entity_id: str | None = None):
        self.joined_calls += 1
        return self.store.joined(entity_id)

    def joined_with_entities(self):
        self.joined_with_entities_calls += 1
        return self.store.joined_with_entities()

    def __getattr__(self, name: str):
        return getattr(self.store, name)


def seed_store(path: str) -> None:
    today = dt.date.today().isoformat()
    store = Store(path)
    try:
        rows = [
            ("positive", "pos", "系统体验", {"aspects": [{"aspect": "系统", "polarity": "pos"}]}),
            ("negative", "neg", "售后", {"aspects": [{"aspect": "售后", "polarity": "neg"}]}),
        ]
        for native_id, polarity, topic, signals in rows:
            document = CleanDoc.build(
                platform="weibo", native_id=native_id, entity_id="youdoo",
                text=topic, publish_ts=f"{today}T08:00:00+08:00",
                fetched_at=f"{today}T09:00:00+08:00",
            )
            store.add_clean(document)
            store.add_feature(document.doc_id, {
                "polarity": polarity,
                "confidence": 0.9,
                "risk": 60 if polarity == "neg" else 1,
                "topic_label": topic,
                "signals": signals,
            })
        store.log_run(
            "run-1", "weibo", "youdoo", 2, "ok", "ok", "",
            f"{today}T09:00:00+08:00",
        )
        store.commit()
    finally:
        store.close()


class APIQuerySnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        seed_store(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _assert_builder_uses_one_join(self, module: str, builder) -> None:
        store = Store(self.db)
        counting = CountingStore(store)
        try:
            with mock.patch(f"{module}.request_query_snapshot", side_effect=lambda value: value), \
                    mock.patch("yuqing.embed.available", return_value=False):
                uncached = builder(counting, WATCH)
            self.assertEqual(5, counting.joined_calls)

            counting.joined_calls = 0
            with mock.patch("yuqing.embed.available", return_value=False):
                cached = builder(counting, WATCH)
            self.assertEqual(1, counting.joined_calls)
        finally:
            store.close()

        self.assertEqual(uncached, cached)

    def test_overview_collapses_five_joined_reads_to_one(self) -> None:
        self._assert_builder_uses_one_join("yuqing.api.overview", build_overview)

    def test_analysis_collapses_five_joined_reads_to_one(self) -> None:
        self._assert_builder_uses_one_join("yuqing.api.analysis", build_analysis)

    def test_cached_results_are_returned_as_independent_lists(self) -> None:
        store = Store(self.db)
        counting = CountingStore(store)
        try:
            snapshot = request_query_snapshot(counting)
            first = snapshot.joined("youdoo")
            first.clear()
            second = snapshot.joined("youdoo")

            entities_first = snapshot.joined_with_entities()
            entities_first.clear()
            entities_second = snapshot.joined_with_entities()
        finally:
            store.close()

        self.assertEqual(1, counting.joined_calls)
        self.assertEqual(2, len(second))
        self.assertEqual(1, counting.joined_with_entities_calls)
        self.assertEqual(2, len(entities_second))


if __name__ == "__main__":
    unittest.main()
