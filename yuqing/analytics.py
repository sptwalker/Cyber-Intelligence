# -*- coding: utf-8 -*-
"""Compatibility facade for the analytics domain.

Implementations are split by responsibility while this module preserves the
historical ``from yuqing import analytics`` API.  Facade wrappers intentionally
pass public collaborators into implementation modules so existing monkeypatch
paths such as ``yuqing.analytics.daily_series`` continue to affect composed
calculations.
"""

from __future__ import annotations

import sys

from . import analytics_health as _health
from . import analytics_learning as _learning
from . import analytics_semantic as _semantic
from . import analytics_timeseries as _timeseries
from .watch_config import validate_watch, watch_path as _watch_path

MIN_ANOMALY_COUNT = _health.MIN_ANOMALY_COUNT
BHI_WEIGHTS = _health.BHI_WEIGHTS
STOPWORDS = _learning.STOPWORDS

# Private aliases are retained because a few local/debug integrations used the
# old single-module helpers directly.
_YMD = _timeseries._YMD
_MD = _timeseries._MD
_NDAYS = _timeseries._NDAYS
_rows = _timeseries._rows
_bhi_components = _health._bhi_components
_bhi_label = _health._bhi_label
_diverse_pick = _learning._diverse_pick
_ngrams = _learning._ngrams
_semantic_cluster_map = _semantic._semantic_cluster_map


def _facade(implementation):
    """Copy documentation without exposing implementation-only parameters."""
    def decorate(wrapper):
        wrapper.__doc__ = implementation.__doc__
        return wrapper
    return decorate


@_facade(_timeseries.normalize_day)
def normalize_day(publish_ts: str, fetched_at: str) -> str:
    return _timeseries.normalize_day(publish_ts, fetched_at)


@_facade(_health.robust_z)
def robust_z(history: list[float], x: float) -> float:
    return _health.robust_z(history, x)


@_facade(_timeseries.daily_series)
def daily_series(store, entity_id: str | None = None, weights=None) -> list[dict]:
    return _timeseries.daily_series(
        store, entity_id, weights, _rows_fn=_rows, _normalize_day_fn=normalize_day,
    )


@_facade(_timeseries.daily_negative_series)
def daily_negative_series(store, entity_id: str) -> list[tuple[str, int]]:
    return _timeseries.daily_negative_series(
        store, entity_id, _daily_series_fn=daily_series,
    )


@_facade(_health.negative_anomaly)
def negative_anomaly(
    store, entity_id: str, *, since_day: str | None = None,
) -> dict:
    return _health.negative_anomaly(
        store, entity_id, since_day=since_day,
        _daily_negative_series_fn=daily_negative_series,
        _robust_z_fn=robust_z,
        _min_anomaly_count=MIN_ANOMALY_COUNT,
    )


@_facade(_timeseries.aspect_breakdown)
def aspect_breakdown(
    store, entity_id: str, *, since_day: str | None = None,
) -> list[dict]:
    return _timeseries.aspect_breakdown(
        store, entity_id, since_day=since_day,
        _rows_fn=_rows, _normalize_day_fn=normalize_day,
    )


@_facade(_timeseries.rising_topics)
def rising_topics(
    store, entity_id: str, split_day: str, *, since_day: str | None = None,
) -> list[dict]:
    return _timeseries.rising_topics(
        store, entity_id, split_day, since_day=since_day,
        _rows_fn=_rows, _normalize_day_fn=normalize_day,
    )


@_facade(_semantic.semantic_topics)
def semantic_topics(
    store, entity_id: str, threshold: float = 0.8, min_size: int = 2,
    *, since_day: str | None = None,
) -> list[dict]:
    return _semantic.semantic_topics(
        store, entity_id, threshold, min_size, since_day=since_day,
        _rows_fn=_rows, _normalize_day_fn=normalize_day,
    )


@_facade(_learning.active_sample)
def active_sample(store, entity_id: str | None = None, limit: int = 20) -> list[dict]:
    return _learning.active_sample(
        store, entity_id, limit, _diverse_pick_fn=_diverse_pick,
    )


@_facade(_semantic.suggest_targets)
def suggest_targets(
    store, entity_id: str, aliases: list[str], must_not: list[str] | None = None,
    top: int = 10, min_sim: float = 0.5,
) -> list[dict]:
    return _semantic.suggest_targets(
        store, entity_id, aliases, must_not, top, min_sim, _rows_fn=_rows,
    )


@_facade(_learning.extract_seed_candidates)
def extract_seed_candidates(
    store, entity_id: str, aliases: list[str], must_not=None,
    min_sim: float = 0.5, min_df: int = 2, min_lift: float = 3.0,
    min_score: float = 0.5, km=None,
) -> list[dict]:
    return _learning.extract_seed_candidates(
        store, entity_id, aliases, must_not, min_sim, min_df, min_lift, min_score, km,
        _suggest_targets_fn=suggest_targets, _stopwords=STOPWORDS, _ngrams_fn=_ngrams,
    )


@_facade(_learning.mine_and_queue)
def mine_and_queue(store, watch: dict, km=None) -> dict:
    return _learning.mine_and_queue(
        store, watch, km, _extract_seed_candidates_fn=extract_seed_candidates,
    )


@_facade(_learning.append_alias)
def append_alias(entity_id: str, word: str) -> tuple[bool, str]:
    package = sys.modules.get(__package__)
    watch_path_fn = getattr(package, "watch_path", _watch_path) if package else _watch_path
    return _learning.append_alias(
        entity_id,
        word,
        _validate_watch_fn=validate_watch,
        _watch_path_fn=watch_path_fn,
    )


@_facade(_health.brand_health)
def brand_health(
    store, entity_id: str, weights=None, bhi_weights=None, *, since_day: str | None = None,
) -> dict:
    effective_weights = bhi_weights or BHI_WEIGHTS
    return _health.brand_health(
        store, entity_id, weights, effective_weights, since_day=since_day,
        _rows_fn=_rows, _normalize_day_fn=normalize_day,
        _aspect_breakdown_fn=aspect_breakdown,
        _bhi_components_fn=_bhi_components, _bhi_label_fn=_bhi_label,
    )


@_facade(_health.bhi_trend)
def bhi_trend(store, entity_id: str, weights=None, bhi_weights=None) -> list[dict]:
    effective_weights = bhi_weights or BHI_WEIGHTS
    return _health.bhi_trend(
        store, entity_id, weights, effective_weights, _daily_series_fn=daily_series,
        _bhi_components_fn=_bhi_components,
    )


@_facade(_timeseries.kol_ranking)
def kol_ranking(store, entity_id: str, limit: int = 15, weights=None) -> list[dict]:
    return _timeseries.kol_ranking(store, entity_id, limit, weights, _rows_fn=_rows)


@_facade(_timeseries.aspect_trend)
def aspect_trend(store, entity_id: str, split_day: str) -> list[dict]:
    return _timeseries.aspect_trend(
        store, entity_id, split_day, _rows_fn=_rows, _normalize_day_fn=normalize_day,
    )


@_facade(_timeseries.aspect_platform_cross)
def aspect_platform_cross(store, watch: dict) -> dict:
    return _timeseries.aspect_platform_cross(store, watch, _rows_fn=_rows)


@_facade(_semantic.suspicious_clusters)
def suspicious_clusters(store, entity_id: str, min_size: int = 3) -> list[dict]:
    return _semantic.suspicious_clusters(
        store, entity_id, min_size,
        _semantic_cluster_map_fn=_semantic_cluster_map, _rows_fn=_rows,
    )


__all__ = [
    "BHI_WEIGHTS", "MIN_ANOMALY_COUNT", "STOPWORDS", "active_sample", "append_alias",
    "aspect_breakdown", "aspect_platform_cross", "aspect_trend", "bhi_trend", "brand_health",
    "daily_negative_series", "daily_series", "extract_seed_candidates", "kol_ranking",
    "mine_and_queue", "negative_anomaly", "normalize_day", "rising_topics", "robust_z",
    "semantic_topics", "suggest_targets", "suspicious_clusters",
]
