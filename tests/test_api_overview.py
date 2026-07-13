# -*- coding: utf-8 -*-
"""Phase-one overview API contract and workbench delivery tests."""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import tempfile
import unittest
import re
from email.message import Message
from unittest import mock

from yuqing import dashboard
from yuqing.api.overview import build_overview
from yuqing.api.responses import APIError, enum_value, json_body, success_payload
from yuqing.store import CleanDoc, Store


WATCH = {
    "platforms": ["weibo", "tieba"],
    "entities": [
        {"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]},
        {"id": "competitor", "type": "competitor", "aliases": ["Competitor"]},
    ],
}


def seed_store(path: str, *, with_health: bool = True) -> None:
    today = dt.date.today().isoformat()
    store = Store(path)
    try:
        positive = CleanDoc.build(
            platform="weibo", native_id="positive", entity_id="youdoo",
            text="体验不错", publish_ts=f"{today}T08:00:00+08:00",
            fetched_at=f"{today}T09:00:00+08:00", likes=10,
        )
        negative = CleanDoc.build(
            platform="tieba", native_id="negative", entity_id="youdoo",
            text="退款困难", publish_ts=f"{today}T08:30:00+08:00",
            fetched_at=f"{today}T09:00:00+08:00", comments=20,
        )
        store.add_clean(positive)
        store.add_feature(positive.doc_id, {
            "polarity": "pos", "confidence": 0.95, "risk": 2,
            "topic_label": "体验", "signals": {},
        })
        store.add_clean(negative)
        store.add_feature(negative.doc_id, {
            "polarity": "neg", "confidence": 0.4, "risk": 80,
            "topic_label": "售后", "signals": {"crisis": True},
        })
        store.create_incident(
            entity_id="youdoo", cluster_key="refund", level="P0",
            doc_id=negative.doc_id, summary="退款事件", ts=f"{today}T09:05:00+08:00",
        )
        store.save_report("run-report", f"{today}T09:10:00+08:00", "# report")
        if with_health:
            store.log_run("run-1", "weibo", "youdoo", 10, "ok", "ok", "", f"{today}T09:00:00+08:00")
            store.log_run("run-1", "tieba", "youdoo", 0, "error", "fail", "登录失效", f"{today}T09:00:00+08:00")
        store.commit()
    finally:
        store.close()


class ResponseHelpersTest(unittest.TestCase):
    def test_success_envelope_has_timezone_and_quality_metadata(self) -> None:
        payload = success_payload(
            {"value": 1}, entity_id="youdoo", data_quality="degraded",
            quality_notes=["tieba failed"],
        )

        self.assertTrue(payload["success"])
        generated = dt.datetime.fromisoformat(payload["meta"]["generated_at"])
        self.assertIsNotNone(generated.tzinfo)
        self.assertEqual("youdoo", payload["meta"]["entity_id"])
        self.assertEqual("degraded", payload["meta"]["data_quality"])
        self.assertEqual(["tieba failed"], payload["meta"]["quality_notes"])

    def test_enum_and_json_body_validation_are_stable(self) -> None:
        with self.assertRaises(APIError) as caught:
            enum_value({"range": ["1y"]}, "range", ("7d", "30d"), default="7d")
        self.assertEqual("INVALID_PARAMETER", caught.exception.code)

        handler = mock.Mock()
        handler.headers = {"Content-Length": "2"}
        handler.rfile = io.BytesIO(b"[]")
        with self.assertRaises(APIError) as body_error:
            json_body(handler)
        self.assertEqual("INVALID_BODY", body_error.exception.code)


class OverviewReadModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_overview_reuses_domain_metrics_and_marks_failed_collection(self) -> None:
        seed_store(self.db)
        store = Store(self.db)
        try:
            data, quality, notes = build_overview(store, WATCH)
        finally:
            store.close()

        self.assertEqual("youdoo", data["entity"]["id"])
        self.assertEqual(2, data["metrics"]["total_volume"])
        self.assertEqual(1, data["metrics"]["negative_count"])
        self.assertIsNotNone(data["metrics"]["bhi"])
        self.assertEqual(80, data["metrics"]["highest_risk"])
        self.assertEqual(1, data["pending_review_count"])
        self.assertEqual("P0", data["top_incident"]["level"])
        self.assertEqual("run-report", data["latest_report"]["run_id"])
        self.assertEqual("degraded", quality)
        self.assertTrue(any("tieba" in note for note in notes))

    def test_missing_collection_is_unknown_not_zero_risk(self) -> None:
        seed_store(self.db, with_health=False)
        store = Store(self.db)
        try:
            data, quality, notes = build_overview(store, WATCH)
        finally:
            store.close()

        self.assertEqual("unknown", quality)
        self.assertEqual(80, data["metrics"]["highest_risk"])
        self.assertTrue(notes)
        self.assertTrue(all(item["health"] == "unknown" for item in data["collection_health"]))


class OverviewHTTPTest(unittest.TestCase):
    PUBLIC_HOST = "cyber.youdoogo.com"
    PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
    INTERNAL_HOST = "cyber-intelligence:8080"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        seed_store(self.db)
        dashboard._SESSION_DB = self.db
        dashboard._session_init(self.db)
        dashboard._session_save("overview-session", {"open_id": "ou_test", "name": "测试用户"})
        self.env = mock.patch.dict(os.environ, {
            "YUQING_CONFIG": os.path.join(self.tmp.name, "missing-config.json"),
            "FEISHU_REDIRECT_URI": f"{self.PUBLIC_ORIGIN}/auth/callback",
        })
        self.env.start()
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def request(self, path: str, *, headers: dict | None = None) -> tuple[int, dict, bytes]:
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        handler.rfile = io.BytesIO()
        handler.wfile = io.BytesIO()
        response_headers = {}
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: response_headers.__setitem__(key, value)
        handler.end_headers = lambda: None
        handler.send_error = lambda code, message=None, explain=None: setattr(handler, "response_code", code)

        handler.do_GET()
        return handler.response_code, response_headers, handler.wfile.getvalue()

    def remote_headers(self, *, authenticated: bool) -> dict:
        headers = {
            "Host": self.INTERNAL_HOST,
            "X-Forwarded-Host": self.PUBLIC_HOST,
            "X-Forwarded-Proto": "https",
        }
        if authenticated:
            headers["Cookie"] = "yuqing_sid=overview-session"
        return headers

    def test_authenticated_overview_uses_common_envelope(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            status, headers, body = self.request(
                "/api/v1/overview?range=7d", headers=self.remote_headers(authenticated=True),
            )

        payload = json.loads(body)
        self.assertEqual(200, status)
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertTrue(payload["success"])
        self.assertEqual("youdoo", payload["meta"]["entity_id"])
        self.assertEqual("degraded", payload["meta"]["data_quality"])

    def test_unauthenticated_and_invalid_requests_return_json_errors(self) -> None:
        status, _, body = self.request(
            "/api/v1/overview", headers=self.remote_headers(authenticated=False),
        )
        self.assertEqual(401, status)
        self.assertEqual("UNAUTHORIZED", json.loads(body)["error"]["code"])

        with mock.patch("yuqing.load_watch", return_value=WATCH):
            status, _, body = self.request(
                "/api/v1/overview?range=1y", headers=self.remote_headers(authenticated=True),
            )
        self.assertEqual(400, status)
        self.assertEqual("INVALID_PARAMETER", json.loads(body)["error"]["code"])

    def test_v2_and_legacy_dashboards_remain_available_locally(self) -> None:
        local_headers = {"Host": "127.0.0.1:8000"}
        status, _, body = self.request("/v2", headers=local_headers)
        self.assertEqual(200, status)
        self.assertIn(b'id="view-overview"', body)
        self.assertIn(b'/v2/assets/styles.css', body)
        self.assertNotIn(b'id="view-knowledge"', body)
        self.assertNotIn(b'id="view-integrations"', body)
        self.assertNotIn("审核流程说明".encode("utf-8"), body)

        for asset, expected_type in (
            ("styles.css", "text/css; charset=utf-8"),
            ("api.js", "text/javascript; charset=utf-8"),
            ("app.js", "text/javascript; charset=utf-8"),
            ("views/overview.js", "text/javascript; charset=utf-8"),
        ):
            with self.subTest(asset=asset):
                status, headers, body = self.request(f"/v2/assets/{asset}", headers=local_headers)
                self.assertEqual(200, status)
                self.assertEqual(expected_type, headers["Content-Type"])
                self.assertTrue(body)

        status, _, app_body = self.request("/v2/assets/app.js", headers=local_headers)
        self.assertEqual(200, status)
        app_text = app_body.decode("utf-8")
        for view_id in ("review", "reports", "config"):
            self.assertRegex(app_text, rf"\{{id:'{re.escape(view_id)}'[^}}]*disabled:true")
        self.assertIn('disabled aria-disabled="true"', app_text)

        status, _, overview_body = self.request("/v2/assets/views/overview.js", headers=local_headers)
        self.assertEqual(200, status)
        self.assertIn('disabled aria-disabled="true"', overview_body.decode("utf-8"))

        for path in ("/v2/assets/../index.html", "/v2/assets/%2e%2e%2findex.html"):
            with self.subTest(path=path):
                status, _, _ = self.request(path, headers=local_headers)
                self.assertEqual(404, status)

        for path in ("/", "/dash", "/exec"):
            with self.subTest(path=path):
                status, _, body = self.request(path, headers=local_headers)
                self.assertEqual(200, status)
                self.assertIn(b"<!doctype html>", body.lower())


if __name__ == "__main__":
    unittest.main()
