# -*- coding: utf-8 -*-
"""Collector sidecar boundary and proxy behavior tests."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from yuqing import collect, collector_client, collector_service, login
from yuqing.collector_service import CollectorRequestError, fetch_items, health_payload


class CollectorServiceTest(unittest.TestCase):
    def test_fetch_is_platform_and_entry_whitelisted(self) -> None:
        with mock.patch(
            "yuqing.collector_service._fetch_opencli", return_value=[{"id": "1"}],
        ) as fetch:
            items = fetch_items({
                "platform": "weibo", "entry": "search",
                "keyword": "Youdoo Box", "limit": 20,
            })

        self.assertEqual([{"id": "1"}], items)
        fetch.assert_called_once_with("weibo", "Youdoo Box", 20)
        with self.assertRaisesRegex(CollectorRequestError, "不支持的平台"):
            fetch_items({"platform": "shell", "keyword": "id"})
        with self.assertRaisesRegex(CollectorRequestError, "必须提供 user"):
            fetch_items({"platform": "weibo", "entry": "user-posts"})

    def test_health_requires_opencli_and_connected_browser(self) -> None:
        completed = mock.Mock(stdout="[OK] Extension: connected (v1)", stderr="")
        with mock.patch("yuqing.collector_service.shutil.which", return_value="/usr/bin/opencli"), \
                mock.patch("yuqing.collector_service.subprocess.run", return_value=completed):
            payload = health_payload()

        self.assertTrue(payload["ready"])
        self.assertTrue(payload["opencli_available"])
        self.assertTrue(payload["browser_connected"])

    def test_selfcheck_contract_round_trips_through_client_normalization(self) -> None:
        payload = collector_service.selfcheck_payload()
        handler = object.__new__(collector_service.CollectorHandler)
        handler.path = "/v1/selfcheck"
        handler._json = mock.Mock()
        handler.do_GET()
        handler._json.assert_called_once_with(payload)

        with mock.patch.object(collector_client, "_request", return_value=payload):
            result = collector_client.selfcheck()

        self.assertTrue(result["ready"])
        self.assertEqual(1, result["contract_version"])
        self.assertEqual("weibo", result["platform"])
        self.assertEqual("yuqing-collector-selfcheck-v1", result["native_id"])
        self.assertEqual("a4088b49f41a0653", result["doc_id"])

        invalid = {**payload, "contract_version": 2}
        with mock.patch.object(collector_client, "_request", return_value=invalid):
            with self.assertRaisesRegex(RuntimeError, "version mismatch"):
                collector_client.selfcheck()


class CollectorProxyTest(unittest.TestCase):
    def test_collection_fetches_through_configured_sidecar(self) -> None:
        with mock.patch.dict(os.environ, {"YUQING_COLLECTOR_URL": "http://127.0.0.1:8788"}), \
                mock.patch.object(collector_client, "fetch", return_value=[{"id": "remote"}]) as fetch:
            search = collect._fetch_opencli("weibo", "Youdoo", 10)
            complaint = collect._fetch_heimao("Youdoo", 5)

        self.assertEqual([{"id": "remote"}], search)
        self.assertEqual([{"id": "remote"}], complaint)
        self.assertEqual(
            [
                mock.call("weibo", "Youdoo", 10),
                mock.call("heimao", "Youdoo", 5),
            ],
            fetch.call_args_list,
        )

    def test_login_open_is_proxied_to_sidecar(self) -> None:
        with mock.patch.dict(os.environ, {"YUQING_COLLECTOR_URL": "http://127.0.0.1:8788"}), \
                mock.patch.object(
                    collector_client, "open_login", return_value="opened",
                ) as open_login:
            message = login.open_login("weibo")

        self.assertEqual("opened", message)
        open_login.assert_called_once_with("weibo")


if __name__ == "__main__":
    unittest.main()
