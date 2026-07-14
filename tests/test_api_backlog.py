# -*- coding: utf-8 -*-
"""Read-only backlog JSON and CSV tests."""

from __future__ import annotations

import io
import datetime as dt
import json
import os
import tempfile
import unittest
from email.message import Message
from unittest import mock

from yuqing import dashboard
from yuqing.api.backlog import build_backlog
from yuqing.store import CleanDoc, Store


WATCH = {
    "platforms": ["weibo"],
    "entities": [{"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]}],
}


def seed(path: str) -> None:
    store = Store(path)
    try:
        today = dt.date.today()
        for native_id, topic, day in (
            ("bug-1", "系统稳定性", today),
            ("bug-old", "历史兼容性", today - dt.timedelta(days=40)),
        ):
            doc = CleanDoc.build(
                platform="weibo", native_id=native_id, entity_id="youdoo", text=topic,
                publish_ts=f"{day.isoformat()}T09:00:00+08:00",
                fetched_at=f"{today.isoformat()}T10:00:00+08:00", likes=10, comments=5,
            )
            doc.is_complaint = True
            store.add_clean(doc)
            store.add_feature(doc.doc_id, {
                "polarity": "neg", "topic_label": topic, "risk": 50,
                "signals": {"bug": True},
            })
        store.commit()
    finally:
        store.close()


class BacklogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        seed(self.db)
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(self, path: str) -> tuple[int, dict, bytes]:
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        handler.headers["Host"] = "127.0.0.1:8000"
        handler.rfile = io.BytesIO()
        handler.wfile = io.BytesIO()
        response_headers = {}
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: response_headers.__setitem__(key, value)
        handler.end_headers = lambda: None
        handler.send_error = lambda code, message=None, explain=None: setattr(handler, "response_code", code)
        handler.do_GET()
        return handler.response_code, response_headers, handler.wfile.getvalue()

    def test_backlog_reuses_insights_aggregation(self) -> None:
        store = Store(self.db)
        try:
            data, quality, notes = build_backlog(store, WATCH)
        finally:
            store.close()

        self.assertEqual(1, data["count"])
        self.assertEqual("Bug", data["items"][0]["kind"])
        self.assertEqual("系统稳定性", data["items"][0]["topic"])
        self.assertEqual(15, data["items"][0]["heat"])
        self.assertEqual("unknown", quality)
        self.assertTrue(notes)

        store = Store(self.db)
        try:
            long_range, _, _ = build_backlog(store, WATCH, range_name="90d")
        finally:
            store.close()
        self.assertEqual(2, long_range["count"])

    def test_json_and_csv_endpoints(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            status, _, body = self.request("/api/v1/backlog?range=30d")
            csv_status, headers, csv_body = self.request("/api/v1/backlog.csv")

        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["success"])
        self.assertEqual(200, csv_status)
        self.assertEqual("text/csv; charset=utf-8", headers["Content-Type"])
        decoded = csv_body.decode("utf-8-sig")
        self.assertIn("类型,话题,声量,热度,代表帖,链接", decoded)
        self.assertIn("Bug,系统稳定性,1,15", decoded)


if __name__ == "__main__":
    unittest.main()
