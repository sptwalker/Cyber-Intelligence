# -*- coding: utf-8 -*-
"""Report API contracts, source safety, and deterministic generation tests."""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import tempfile
import unittest
from email.message import Message
from unittest import mock

from yuqing import dashboard
from yuqing.api import reports
from yuqing.api.reports import (
    build_report_detail,
    build_report_list,
    build_source_document,
    generate_report,
)
from yuqing.store import CleanDoc, Store


WATCH = {
    "platforms": ["weibo"],
    "entities": [
        {"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]},
        {"id": "competitor", "type": "competitor", "aliases": ["Competitor"]},
    ],
}


def seed(path: str) -> str:
    store = Store(path)
    try:
        fixture_date = dt.date.today().isoformat()
        doc = CleanDoc.build(
            platform="weibo", native_id="report-source", entity_id="youdoo",
            text="售后退款仍未解决", author="测试用户", publish_ts="2026-07-14T08:00:00+08:00",
            fetched_at="2026-07-14T09:00:00+08:00", url="javascript:alert(1)",
        )
        store.add_clean(doc)
        store.add_feature(doc.doc_id, {
            "polarity": "neg", "confidence": 0.8, "risk": 70,
            "topic_label": "售后", "summary": "退款问题", "evidence": "退款仍未解决",
            "signals": {"crisis": True},
        })
        old_doc = CleanDoc.build(
            platform="weibo", native_id="report-source-old", entity_id="youdoo",
            text="窗口外历史高风险内容", author="历史用户",
            publish_ts="2026-07-01T08:00:00+08:00",
            fetched_at="2026-07-01T09:00:00+08:00", url="https://example.com/old",
        )
        store.add_clean(old_doc)
        store.add_feature(old_doc.doc_id, {
            "polarity": "neg", "confidence": 0.9, "risk": 99,
            "topic_label": "历史问题", "summary": "窗口外内容",
            "evidence": "历史高风险", "signals": {"crisis": True},
        })
        store.log_run(
            "run-1", "weibo", "youdoo", 1, "ok", "ok", "",
            f"{fixture_date}T09:00:00+08:00",
        )
        store.save_report(
            "run-report", "2026-07-14T09:10:00+08:00",
            f"# Youdoo Box 周报\n\n退款问题 [来源:{doc.doc_id}]",
        )
        store.commit()
        return doc.doc_id
    finally:
        store.close()


class ReportReadModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        self.doc_id = seed(self.db)
        store = Store(self.db)
        try:
            latest_run = store.conn.execute(
                "SELECT ts FROM run_log ORDER BY ts DESC LIMIT 1",
            ).fetchone()
        finally:
            store.close()
        self.assertEqual(dt.date.today().isoformat(), latest_run["ts"][:10])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_detail_and_safe_source_document(self) -> None:
        store = Store(self.db)
        try:
            listing, quality, _ = build_report_list(store, WATCH)
            detail, _, _ = build_report_detail(store, WATCH, "run-report")
            source, _, _ = build_source_document(store, WATCH, self.doc_id)
        finally:
            store.close()

        self.assertEqual("ok", quality)
        self.assertEqual(1, listing["count"])
        self.assertEqual("Youdoo Box 周报", listing["items"][0]["title"])
        self.assertEqual([self.doc_id], detail["report"]["citations"])
        self.assertIsNone(source["document"]["url"])

    def test_generation_persists_and_rejects_concurrent_request(self) -> None:
        store = Store(self.db)
        try:
            data, quality, _ = generate_report(
                store, WATCH, now="2026-07-14T10:00:00+08:00",
            )
            persisted = store.conn.execute(
                "SELECT markdown FROM reports WHERE run_id=?",
                (data["report"]["run_id"],),
            ).fetchone()
            self.assertIsNotNone(persisted)
            self.assertEqual("ok", quality)
            markdown = persisted["markdown"]
            self.assertIn("统计周期：2026-07-08 至 2026-07-14", markdown)
            self.assertIn("| 总声量 | 1 |", markdown)
            self.assertNotIn("窗口外内容", markdown)

            reports._generation_lock.acquire()
            try:
                with self.assertRaisesRegex(Exception, "已有报告正在生成"):
                    generate_report(store, WATCH)
            finally:
                reports._generation_lock.release()
        finally:
            store.close()


class ReportHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        self.doc_id = seed(self.db)
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(
        self, method: str, path: str, body: dict | None = None,
        *, host: str = "127.0.0.1:8000",
    ) -> tuple[int, dict, bytes]:
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        handler.headers["Host"] = host
        raw = json.dumps(body or {}).encode("utf-8") if method == "POST" else b""
        if raw:
            handler.headers["Content-Length"] = str(len(raw))
            handler.headers["Content-Type"] = "application/json"
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        response_headers = {}
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: response_headers.__setitem__(key, value)
        handler.end_headers = lambda: None
        handler.send_error = lambda code, message=None, explain=None: setattr(handler, "response_code", code)
        if method == "POST":
            handler.do_POST()
        else:
            handler.do_GET()
        return handler.response_code, response_headers, handler.wfile.getvalue()

    def test_read_endpoints_and_missing_report(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            list_status, _, list_body = self.request("GET", "/api/v1/reports")
            detail_status, _, detail_body = self.request("GET", "/api/v1/reports/run-report")
            source_status, _, source_body = self.request("GET", f"/api/v1/docs/{self.doc_id}")
            missing_status, _, missing_body = self.request("GET", "/api/v1/reports/missing")

        self.assertEqual(200, list_status)
        self.assertEqual(1, json.loads(list_body)["data"]["count"])
        self.assertEqual(200, detail_status)
        self.assertEqual("run-report", json.loads(detail_body)["data"]["report"]["run_id"])
        self.assertEqual(200, source_status)
        self.assertIsNone(json.loads(source_body)["data"]["document"]["url"])
        self.assertEqual(404, missing_status)
        self.assertEqual("NOT_FOUND", json.loads(missing_body)["error"]["code"])

    def test_generate_endpoint_persists(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            status, _, body = self.request(
                "POST", "/api/v1/reports/generate", {"entity_id": "youdoo"},
            )

        payload = json.loads(body)
        self.assertEqual(201, status)
        self.assertTrue(payload["data"]["generated"])
        store = Store(self.db)
        try:
            self.assertEqual(2, store.conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
        finally:
            store.close()

    def test_remote_generate_without_session_is_forbidden(self) -> None:
        status, _, body = self.request(
            "POST", "/api/v1/reports/generate", {"entity_id": "youdoo"},
            host="cyber-intelligence:8080",
        )
        self.assertEqual(403, status)
        self.assertEqual("FORBIDDEN", json.loads(body)["error"]["code"])


if __name__ == "__main__":
    unittest.main()
