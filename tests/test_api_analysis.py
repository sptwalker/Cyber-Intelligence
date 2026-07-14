# -*- coding: utf-8 -*-
"""Deterministic analysis API tests."""

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
from yuqing.api.analysis import build_analysis
from yuqing.store import CleanDoc, Store


WATCH = {
    "platforms": ["weibo"],
    "entities": [{"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]}],
}


def seed(path: str) -> None:
    day = dt.date.today().isoformat()
    store = Store(path)
    try:
        rows = [
            ("p1", "pos", "系统体验", {"aspects": [{"aspect": "系统", "polarity": "pos"}]}, day),
            ("n1", "neg", "售后", {"aspects": [{"aspect": "售后", "polarity": "neg"}]}, day),
            ("old", "neg", "历史问题", {"aspects": [{"aspect": "历史", "polarity": "neg"}]},
             (dt.date.today() - dt.timedelta(days=40)).isoformat()),
        ]
        for native_id, polarity, topic, signals, publish_day in rows:
            doc = CleanDoc.build(
                platform="weibo", native_id=native_id, entity_id="youdoo",
                text=topic, publish_ts=f"{publish_day}T08:00:00+08:00",
                fetched_at=f"{day}T09:00:00+08:00",
            )
            store.add_clean(doc)
            store.add_feature(doc.doc_id, {
                "polarity": polarity, "confidence": 0.9, "risk": 50 if polarity == "neg" else 1,
                "topic_label": topic, "signals": signals,
            })
        store.commit()
    finally:
        store.close()


class AnalysisReadModelTest(unittest.TestCase):
    def test_analysis_reuses_aspect_topic_and_bhi_functions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "yuqing.db")
            seed(path)
            store = Store(path)
            try:
                data, quality, notes = build_analysis(store, WATCH)
                long_range, _, _ = build_analysis(store, WATCH, range_name="90d")
            finally:
                store.close()

        self.assertEqual(2, data["sample"]["count"])
        self.assertEqual({"系统", "售后"}, {item["aspect"] for item in data["aspects"]})
        self.assertEqual("售后", data["topics"][0]["topic"])
        self.assertTrue(data["sentiment_trend"])
        self.assertTrue(data["bhi_trend"])
        self.assertEqual("unknown", quality)
        self.assertTrue(notes)

        self.assertEqual(3, long_range["sample"]["count"])
        self.assertIn("历史", {item["aspect"] for item in long_range["aspects"]})


class AnalysisHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        seed(self.db)
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def request(self, path: str) -> tuple[int, bytes]:
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.command = "GET"
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        handler.headers["Host"] = "127.0.0.1:8000"
        handler.rfile = io.BytesIO()
        handler.wfile = io.BytesIO()
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: None
        handler.end_headers = lambda: None
        handler.send_error = lambda code, message=None, explain=None: setattr(handler, "response_code", code)
        handler.do_GET()
        return handler.response_code, handler.wfile.getvalue()

    def test_analysis_success_and_invalid_range(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            status, body = self.request("/api/v1/analysis?range=7d")
            bad_status, bad_body = self.request("/api/v1/analysis?range=1y")

        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["success"])
        self.assertEqual(400, bad_status)
        self.assertEqual("INVALID_PARAMETER", json.loads(bad_body)["error"]["code"])


if __name__ == "__main__":
    unittest.main()
