# -*- coding: utf-8 -*-
"""Collection pipeline boundaries and compatibility regressions."""

from __future__ import annotations

import unittest
from unittest import mock

from yuqing import collect
from yuqing.collection import fetchers, orchestration, pipeline, semantic
from yuqing.store import Store


class CountingStore(Store):
    def __init__(self) -> None:
        super().__init__(":memory:")
        self.commit_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()


class CollectionBoundaryTest(unittest.TestCase):
    def test_facade_keeps_extracted_contracts_and_legacy_symbols(self) -> None:
        self.assertIs(collect.OPENCLI_SITE, fetchers.OPENCLI_SITE)
        self.assertIs(collect._HEIMAO_LINK, fetchers.HEIMAO_LINK)
        self.assertIs(collect._ISO_TS, pipeline.ISO_TIMESTAMP)
        self.assertEqual(semantic.DEFAULT_THRESHOLD, collect._SEM_THRESHOLD)
        self.assertTrue(callable(orchestration.collect_all))

    def test_patched_facade_fetcher_flows_through_platform_pipeline(self) -> None:
        store = CountingStore()
        with mock.patch.object(
            collect,
            "_fetch_opencli",
            return_value=[{
                "id": "post-1",
                "text": "Alpha 申请退款",
                "created_at": "2026-07-20T08:00:00+08:00",
            }],
        ) as fetch:
            inserted, state = collect.collect_platform(
                store,
                run_id="run-1",
                entity_id="alpha",
                platform="weibo",
                keyword="Alpha",
                now="2026-07-20T09:00:00+08:00",
                entry="search:Alpha",
                aliases=["Alpha"],
                must_not=[],
            )

        self.assertEqual((1, "ok"), (inserted, state))
        fetch.assert_called_once_with("weibo", "Alpha", 50)
        self.assertEqual(1, store.commit_calls)
        self.assertEqual(1, store.conn.execute("SELECT COUNT(*) FROM clean").fetchone()[0])
        self.assertEqual(1, store.conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0])

    def test_fetch_failure_is_logged_as_fail_and_committed_once(self) -> None:
        store = CountingStore()
        with mock.patch.object(collect, "_fetch_opencli", side_effect=RuntimeError("login expired")):
            inserted, state = collect.collect_platform(
                store,
                run_id="run-fail",
                entity_id="alpha",
                platform="weibo",
                keyword="Alpha",
                now="2026-07-20T09:00:00+08:00",
            )

        row = store.conn.execute(
            "SELECT status,health,note,n_fetched FROM run_log"
        ).fetchone()
        self.assertEqual((0, "fail"), (inserted, state))
        self.assertEqual(("error", "fail", "login expired", 0), tuple(row))
        self.assertEqual(1, store.commit_calls)

    def test_processing_exception_still_propagates_without_committing(self) -> None:
        store = CountingStore()
        with mock.patch.object(collect, "normalize", side_effect=ValueError("bad payload")):
            with self.assertRaisesRegex(ValueError, "bad payload"):
                collect.collect_platform(
                    store,
                    run_id="run-bad",
                    entity_id="alpha",
                    platform="weibo",
                    keyword="Alpha",
                    now="2026-07-20T09:00:00+08:00",
                    fixture=[{"id": "broken"}],
                )

        self.assertEqual(0, store.commit_calls)
        self.assertEqual(0, store.conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0])

    def test_collect_all_uses_patched_collect_platform_and_preserves_query_order(self) -> None:
        calls: list[tuple[str, str, str]] = []

        def collect_one(_store, **kwargs):
            calls.append((kwargs["entity_id"], kwargs["platform"], kwargs["keyword"]))
            return 0, "suspect" if kwargs["keyword"] == "Alpha Box" else "ok"

        watch = {
            "platforms": ["weibo"],
            "entities": [{
                "id": "alpha",
                "aliases": ["Alpha", "alpha", "Alpha Box"],
            }],
        }
        with mock.patch.object(collect, "collect_platform", side_effect=collect_one):
            result = collect.collect_all(
                object(), watch, run_id="run-all", now="2026-07-20T09:00:00+08:00"
            )

        self.assertEqual(
            [("alpha", "weibo", "Alpha"), ("alpha", "weibo", "Alpha Box")],
            calls,
        )
        self.assertEqual({"weibo": "suspect"}, result)

    def test_heimao_fetch_keeps_facade_browser_monkeypatch_point(self) -> None:
        markdown = "](" + "//tousu.sina.com.cn/complaint/view/17359912345/)\n退出"
        self.assertEqual("17359912345", collect.parse_heimao_markdown(md=markdown)[0]["id"])
        with mock.patch.object(
            collect, "_opencli_browser", side_effect=["", markdown]
        ) as browser, mock.patch.object(collect.time, "sleep"):
            items = collect._fetch_heimao("Alpha", 5)

        self.assertEqual("17359912345", items[0]["id"])
        self.assertEqual(2, browser.call_count)


if __name__ == "__main__":
    unittest.main()
