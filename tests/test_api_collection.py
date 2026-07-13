# -*- coding: utf-8 -*-
"""Collection status and versioned run-control API tests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from email.message import Message
from unittest import mock

from yuqing import dashboard
from yuqing.api.collection import build_collection_status, latest_platform_runs
from yuqing.store import Store


WATCH = {
    "platforms": ["weibo", "tieba"],
    "entities": [{"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]}],
}


class CollectionReadModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.store.log_run("run-2", "weibo", "youdoo", 3, "ok", "ok", "", "2026-07-13T10:00:00+08:00")
        self.store.log_run("run-2", "weibo", "youdoo", 4, "ok", "suspect", "量级下降", "2026-07-13T10:00:00+08:00")
        self.store.log_run("run-1", "weibo", "youdoo", 99, "ok", "ok", "旧记录", "2026-07-12T10:00:00+08:00")
        self.store.commit()

    def tearDown(self) -> None:
        self.store.close()

    def test_latest_platform_runs_aggregates_one_run_and_marks_missing(self) -> None:
        rows, quality, notes = latest_platform_runs(self.store, "youdoo", ["weibo", "tieba"])

        self.assertEqual("run-2", rows[0]["run_id"])
        self.assertEqual(7, rows[0]["n_fetched"])
        self.assertEqual("suspect", rows[0]["health"])
        self.assertEqual("unknown", rows[1]["health"])
        self.assertEqual("degraded", quality)
        self.assertTrue(any("tieba" in note for note in notes))

    def test_collection_status_combines_login_run_and_environment(self) -> None:
        def login_provider(platforms):
            self.assertEqual(["weibo", "tieba"], platforms)
            return (True, "浏览器桥已连接"), [
                {"platform": "weibo", "logged_in": True, "identity": "analyst",
                 "method": "auth", "error": ""},
            ]

        with mock.patch("yuqing.api.collection.shutil.which", return_value="/usr/bin/opencli"):
            data, quality, _ = build_collection_status(
                self.store, WATCH, {"running": True, "current": "正在采集微博", "stop": False},
                login_provider=login_provider,
            )

        self.assertTrue(data["run"]["running"])
        self.assertTrue(data["execution"]["can_run"])
        self.assertTrue(data["bridge"]["ok"])
        self.assertTrue(data["platforms"][0]["login"]["logged_in"])
        self.assertFalse(data["platforms"][1]["login_required"])
        self.assertEqual("degraded", quality)


class CollectionHTTPTest(unittest.TestCase):
    PUBLIC_HOST = "cyber.youdoogo.com"
    PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
    INTERNAL_HOST = "cyber-intelligence:8080"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        Store(self.db).close()
        dashboard._SESSION_DB = self.db
        dashboard._session_init(self.db)
        dashboard._session_save("collection-session", {"open_id": "ou_test", "name": "测试用户"})
        dashboard._run_state.update({"running": False, "last": None, "current": "", "stop": False})
        self.env = mock.patch.dict(os.environ, {
            "YUQING_CONFIG": os.path.join(self.tmp.name, "missing-config.json"),
            "FEISHU_REDIRECT_URI": f"{self.PUBLIC_ORIGIN}/auth/callback",
            "YUQING_ENABLE_COLLECTION": "true",
        })
        self.env.start()
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.env.stop()
        dashboard._run_state.update({"running": False, "last": None, "current": "", "stop": False})
        self.tmp.cleanup()

    def request(self, method: str, path: str, *, headers: dict | None = None,
                body: bytes = b"") -> tuple[int, dict, bytes]:
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        if body:
            handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        response_headers = {}
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: response_headers.__setitem__(key, value)
        handler.end_headers = lambda: None
        handler.send_error = lambda code, message=None, explain=None: setattr(handler, "response_code", code)

        getattr(handler, f"do_{method}")()
        return handler.response_code, response_headers, handler.wfile.getvalue()

    def headers(self, *, origin: str | None = None, authenticated: bool = True) -> dict:
        result = {
            "Host": self.INTERNAL_HOST,
            "X-Forwarded-Host": self.PUBLIC_HOST,
            "X-Forwarded-Proto": "https",
            "Sec-Fetch-Site": "same-origin",
        }
        if authenticated:
            result["Cookie"] = "yuqing_sid=collection-session"
        if origin is not None:
            result["Origin"] = origin
        return result

    def test_collection_status_uses_versioned_envelope(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH), \
                mock.patch("yuqing.login.bridge_ok", return_value=(True, "已连接")), \
                mock.patch("yuqing.login.status", return_value=[]), \
                mock.patch("yuqing.api.collection.shutil.which", return_value="/usr/bin/opencli"):
            status, _, body = self.request(
                "GET", "/api/v1/collection/status", headers=self.headers(),
            )

        payload = json.loads(body)
        self.assertEqual(200, status)
        self.assertTrue(payload["success"])
        self.assertEqual("youdoo", payload["meta"]["entity_id"])
        self.assertEqual("unknown", payload["meta"]["data_quality"])
        self.assertTrue(payload["data"]["execution"]["can_run"])

    def test_run_is_singleton_and_stop_is_cooperative(self) -> None:
        headers = self.headers(origin=self.PUBLIC_ORIGIN)
        fake_thread = mock.Mock()
        with mock.patch("yuqing.load_watch", return_value=WATCH), \
                mock.patch("yuqing.api.collection.shutil.which", return_value="/usr/bin/opencli"), \
                mock.patch.object(dashboard.threading, "Thread", return_value=fake_thread):
            first_status, _, first_body = self.request("POST", "/api/v1/collection/run", headers=headers)
            second_status, _, second_body = self.request("POST", "/api/v1/collection/run", headers=headers)
            stop_status, _, stop_body = self.request("POST", "/api/v1/collection/stop", headers=headers)

        self.assertEqual(200, first_status)
        self.assertTrue(json.loads(first_body)["data"]["started"])
        self.assertEqual(200, second_status)
        self.assertFalse(json.loads(second_body)["data"]["started"])
        self.assertEqual(200, stop_status)
        self.assertTrue(json.loads(stop_body)["data"]["stop_requested"])
        fake_thread.start.assert_called_once()

    def test_collection_mutation_rejects_csrf_and_unavailable_environment(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH), \
                mock.patch("yuqing.api.collection.shutil.which", return_value="/usr/bin/opencli"):
            status, _, body = self.request(
                "POST", "/api/v1/collection/run",
                headers=self.headers(origin="https://evil.example.com"),
            )
        self.assertEqual(403, status)
        self.assertEqual("FORBIDDEN", json.loads(body)["error"]["code"])
        self.assertFalse(dashboard._run_state["running"])

        with mock.patch("yuqing.load_watch", return_value=WATCH), \
                mock.patch("yuqing.api.collection.shutil.which", return_value=None):
            status, _, body = self.request(
                "POST", "/api/v1/collection/run",
                headers=self.headers(origin=self.PUBLIC_ORIGIN),
            )
        self.assertEqual(409, status)
        self.assertEqual("COLLECTION_UNAVAILABLE", json.loads(body)["error"]["code"])


if __name__ == "__main__":
    unittest.main()
