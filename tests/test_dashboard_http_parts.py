# -*- coding: utf-8 -*-
"""Regression coverage for the split legacy dashboard HTTP adapter."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

from yuqing import dashboard
from yuqing.store import Store


class DashboardHTTPPartsRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        store = Store(self.db)
        try:
            incident = store.create_incident(
                entity_id="youdoo",
                cluster_key="refund",
                level="P1",
                doc_id="doc-1",
                summary="退款投诉增加",
                ts="2026-07-20T10:00:00+08:00",
            )
            store.commit()
            self.incident_id = incident["incident_id"]
        finally:
            store.close()
        dashboard._run_state.update(
            {"running": False, "last": "2026-07-20T09:00:00+08:00", "current": "", "stop": False}
        )
        self.env = mock.patch.dict(
            os.environ,
            {"YUQING_CONFIG": os.path.join(self.tmp.name, "missing-config.json")},
            clear=False,
        )
        self.env.start()
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.env.stop()
        dashboard._run_state.update(
            {"running": False, "last": None, "current": "", "stop": False}
        )
        self.tmp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        host: str = "127.0.0.1:8000",
        payload: dict | None = None,
        keep_end_headers: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        handler.headers["Host"] = host
        if raw:
            handler.headers["Content-Length"] = str(len(raw))
            handler.headers["Content-Type"] = "application/json"
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler._headers_buffer = []
        response_headers: dict[str, str] = {}
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: response_headers.__setitem__(key, value)
        handler.send_error = (
            lambda code, message=None, explain=None: setattr(handler, "response_code", code)
        )
        if keep_end_headers:
            handler.flush_headers = lambda: None
        else:
            handler.end_headers = lambda: None

        getattr(handler, f"do_{method}")()
        return handler.response_code, response_headers, handler.wfile.getvalue()

    def test_status_routes_keep_auth_and_load_watch_facade_compatibility(self) -> None:
        status, _, body = self.request("GET", "/api/run/status")
        self.assertEqual(200, status)
        self.assertEqual(
            {
                "running": False,
                "last": "2026-07-20T09:00:00+08:00",
                "current": "",
            },
            json.loads(body),
        )

        status, _, _ = self.request(
            "GET", "/api/run/status", host="cyber-intelligence:8080"
        )
        self.assertEqual(403, status)

        watch = mock.Mock(return_value={"platforms": ["weibo"]})
        with mock.patch("yuqing.load_watch", watch), mock.patch(
            "yuqing.login.bridge_ok", return_value=(True, "已连接")
        ), mock.patch(
            "yuqing.login.status", return_value=[{"platform": "weibo", "logged_in": True}]
        ) as login_status:
            status, _, body = self.request("GET", "/api/login/status")

        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["bridge_ok"])
        watch.assert_called_once_with()
        login_status.assert_called_once_with(["weibo"])

    def test_watch_keywords_and_incidents_legacy_contracts(self) -> None:
        watch_file = Path(self.tmp.name) / "watch.yaml"
        content = "platforms:\n  - weibo\nentities: []\n"
        with mock.patch("yuqing.watch_path", return_value=str(watch_file)) as watch_path, mock.patch.object(
            dashboard, "_validate_watch", return_value=(True, "配置有效")
        ):
            status, _, body = self.request(
                "POST", "/api/watch", payload={"content": content}
            )
        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["success"])
        self.assertEqual(content, watch_file.read_text(encoding="utf-8"))
        watch_path.assert_called_once_with()

        status, _, body = self.request(
            "POST",
            "/api/keywords",
            payload={
                "action": "add",
                "word": "卡顿",
                "tag": "complaint",
                "entity_id": "youdoo",
            },
        )
        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["success"])

        status, _, body = self.request("GET", "/api/keywords?entity=youdoo")
        self.assertEqual(200, status)
        self.assertEqual("卡顿", json.loads(body)["keywords"][0]["word"])

        status, _, body = self.request(
            "POST",
            "/api/incidents",
            payload={"incident_id": self.incident_id, "action": "confirm", "note": "已核实"},
        )
        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["success"])

        status, _, body = self.request("GET", "/api/incidents?status=confirmed")
        self.assertEqual(200, status)
        incidents = json.loads(body)["incidents"]
        self.assertEqual([self.incident_id], [item["incident_id"] for item in incidents])

    def test_auth_403_asset_traversal_and_deprecation_headers(self) -> None:
        remote_host = "cyber-intelligence:8080"
        status, headers, _ = self.request("GET", "/legacy", host=remote_host)
        self.assertEqual(302, status)
        self.assertEqual("/auth/login?next=%2Flegacy", headers["Location"])

        status, _, _ = self.request("GET", "/assets/app.js", host=remote_host)
        self.assertEqual(401, status)
        status, _, _ = self.request("GET", "/api/login/status", host=remote_host)
        self.assertEqual(403, status)

        for path in ("/assets/../index.html", "/v2/assets/%2e%2e%2findex.html"):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path)
                self.assertEqual(404, status)

        status, headers, body = self.request(
            "GET", "/api/run/status", keep_end_headers=True
        )
        self.assertEqual(200, status)
        self.assertTrue(body)
        self.assertEqual("true", headers["Deprecation"])
        self.assertEqual('</api/v1>; rel="successor-version"', headers["Link"])
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertNotIn("Cache-Control", headers)
        self.assertNotIn("X-Content-Type-Options", headers)


if __name__ == "__main__":
    unittest.main()
