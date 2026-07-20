# -*- coding: utf-8 -*-
"""Compatibility coverage for the split analytics facade."""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

from yuqing import analytics


class JoinedStore:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def joined(self, entity_id=None):
        return self.rows


class AnalyticsFacadeTest(unittest.TestCase):
    def test_public_surface_has_no_implementation_only_parameters(self) -> None:
        expected = {
            "BHI_WEIGHTS", "MIN_ANOMALY_COUNT", "STOPWORDS", "active_sample", "append_alias",
            "aspect_breakdown", "aspect_platform_cross", "aspect_trend", "bhi_trend",
            "brand_health", "daily_negative_series", "daily_series", "extract_seed_candidates",
            "kol_ranking", "mine_and_queue", "negative_anomaly", "normalize_day",
            "rising_topics", "robust_z", "semantic_topics", "suggest_targets",
            "suspicious_clusters",
        }
        self.assertEqual(expected, set(analytics.__all__))
        for name in expected - {"BHI_WEIGHTS", "MIN_ANOMALY_COUNT", "STOPWORDS"}:
            with self.subTest(name=name):
                parameters = inspect.signature(getattr(analytics, name)).parameters
                self.assertFalse(any(parameter.startswith("_") for parameter in parameters))

    def test_time_series_composition_honors_facade_monkeypatch(self) -> None:
        row = {
            "publish_ts": "ignored", "fetched_at": "2026-07-01", "polarity": "neg",
            "signals": "{}", "risk": 3, "likes": 0, "comments": 0, "shares": 0,
            "collects": 0, "plays": 0, "author_followers": 0, "platform": "weibo",
        }
        with mock.patch.object(analytics, "normalize_day", return_value="2030-01-02") as normalize:
            series = analytics.daily_series(JoinedStore([row]), "entity")

        self.assertEqual("2030-01-02", series[0]["day"])
        normalize.assert_called_once_with("ignored", "2026-07-01")

    def test_health_composition_honors_facade_monkeypatches_and_constants(self) -> None:
        with mock.patch.object(
            analytics, "daily_negative_series", return_value=[("2026-07-01", 1), ("2026-07-02", 4)],
        ), mock.patch.object(analytics, "robust_z", return_value=3.0), mock.patch.object(
            analytics, "MIN_ANOMALY_COUNT", 5,
        ):
            result = analytics.negative_anomaly(object(), "entity")
        self.assertFalse(result["anomaly"])

        with mock.patch.object(analytics, "MIN_ANOMALY_COUNT", 4):
            with mock.patch.object(
                analytics, "daily_negative_series",
                return_value=[("2026-07-01", 1), ("2026-07-02", 4)],
            ), mock.patch.object(analytics, "robust_z", return_value=3.0):
                result = analytics.negative_anomaly(object(), "entity")
        self.assertTrue(result["anomaly"])

        store = JoinedStore([{
            "polarity": "pos", "signals": "{}", "likes": 0, "comments": 0,
            "reposts": 0, "plays": 0, "author_followers": 0, "platform": "weibo",
        }])
        components = {"sentiment": 100.0, "volume": 100.0, "crisis": 100.0, "aspect": 100.0}
        with mock.patch.object(analytics, "_bhi_components", return_value=components) as build, \
                mock.patch.object(analytics, "_bhi_label", return_value="兼容标签") as label, \
                mock.patch.object(analytics, "aspect_breakdown", return_value=[]):
            health = analytics.brand_health(store, "entity")
        self.assertEqual("兼容标签", health["label"])
        build.assert_called_once()
        label.assert_called_once_with(100.0)

    def test_learning_composition_honors_facade_monkeypatches(self) -> None:
        manager = mock.Mock()
        with mock.patch.object(analytics, "suggest_targets", return_value=[]) as suggest:
            result = analytics.extract_seed_candidates(
                object(), "entity", ["alias"], km=manager,
            )
        self.assertEqual([], result)
        suggest.assert_called_once()

        candidate = {
            "word": "新别名", "suggested_tag": "seed_alias", "score": 0.9,
            "lift": 5.2, "df_clu": 2, "source_docs": ["doc"], "kind": "seed",
        }
        with mock.patch.object(
            analytics, "extract_seed_candidates", return_value=[candidate],
        ) as extract:
            counts = analytics.mine_and_queue(
                object(), {"entities": [{"id": "entity", "type": "self", "aliases": ["alias"]}]},
                km=manager,
            )
        self.assertEqual({"seed": 1, "feature": 0}, counts)
        extract.assert_called_once()
        manager.add_suggestion.assert_called_once()

        with mock.patch("yuqing.watch_path", return_value="/tmp/watch.yaml") as watch_path, \
                mock.patch.object(
                    analytics._learning, "append_alias", return_value=(True, "ok"),
                ) as append_alias:
            self.assertEqual((True, "ok"), analytics.append_alias("entity", "新别名"))
        injected = append_alias.call_args.kwargs["_watch_path_fn"]
        self.assertIs(injected, watch_path)


if __name__ == "__main__":
    unittest.main()
