# -*- coding: utf-8 -*-
"""Review queue list, pagination, mutation, persistence, and auth tests."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from email.message import Message
from unittest import mock

from yuqing import dashboard
from yuqing.api.reviews import build_reviews, save_review
from yuqing.store import CleanDoc, Store


WATCH = {
    "platforms": ["weibo", "zhihu"],
    "entities": [{"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]}],
}


def seed_reviews(path: str) -> list[str]:
    store = Store(path)
    try:
        fixtures = [
            ("one", "weibo", "高风险低置信负面", "甲", "neg", 0.40, 90, False, {}),
            ("two", "zhihu", "模型判断分歧", "乙", "neu", 0.70, 55, False, {"cross_disagree": True}),
            ("three", "weibo", "反讽表达", "丙", "pos", 0.92, 5, True, {}),
        ]
        doc_ids = []
        for native_id, platform, text, author, polarity, confidence, risk, ironic, signals in fixtures:
            doc = CleanDoc.build(
                platform=platform, native_id=native_id, entity_id="youdoo", text=text,
                author=author, publish_ts="2026-07-13T10:00:00+08:00",
                fetched_at="2026-07-13T11:00:00+08:00", url=f"https://example.com/{native_id}",
            )
            store.add_clean(doc)
            store.add_feature(doc.doc_id, {
                "polarity": polarity, "confidence": confidence, "risk": risk,
                "is_ironic": ironic, "signals": signals, "topic_label": "体验",
            })
            doc_ids.append(doc.doc_id)
        store.commit()
        return doc_ids
    finally:
        store.close()


class ReviewReadModelTest(unittest.TestCase):
    def test_filters_cursor_and_latest_verdict(self) -> None:
        store = Store(":memory:")
        try:
            doc_ids = seed_reviews_in_store(store)
            first, quality, notes = build_reviews(store, WATCH, limit=2)
            self.assertEqual(3, first["total"])
            self.assertEqual(2, first["count"])
            self.assertIsNotNone(first["next_cursor"])
            self.assertEqual("ok", quality)
            self.assertEqual([], notes)

            second, _, _ = build_reviews(store, WATCH, limit=2, cursor=first["next_cursor"])
            self.assertEqual(1, second["count"])
            self.assertNotEqual(first["items"][0]["doc_id"], second["items"][0]["doc_id"])

            save_review(
                store, WATCH, doc_ids[0], verdict="ok", actor="分析师",
                now="2026-07-13T12:00:00+08:00",
            )
            pending, _, _ = build_reviews(store, WATCH, status="pending")
            approved, _, _ = build_reviews(store, WATCH, status="approved")
            self.assertEqual(2, pending["total"])
            self.assertEqual(doc_ids[0], approved["items"][0]["doc_id"])
            self.assertEqual("分析师", approved["items"][0]["actor"])
        finally:
            store.close()

    def test_old_review_table_is_upgraded_with_actor_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE review(doc_id TEXT,kind TEXT,verdict TEXT,note TEXT,ts TEXT)")
            conn.commit()
            conn.close()

            store = Store(path)
            try:
                columns = {row[1] for row in store.conn.execute("PRAGMA table_info(review)")}
            finally:
                store.close()
            self.assertIn("actor", columns)


def seed_reviews_in_store(store: Store) -> list[str]:
    fixtures = [
        ("one", "weibo", "高风险低置信负面", "甲", "neg", 0.40, 90, False, {}),
        ("two", "zhihu", "模型判断分歧", "乙", "neu", 0.70, 55, False, {"cross_disagree": True}),
        ("three", "weibo", "反讽表达", "丙", "pos", 0.92, 5, True, {}),
    ]
    doc_ids = []
    for native_id, platform, text, author, polarity, confidence, risk, ironic, signals in fixtures:
        doc = CleanDoc.build(
            platform=platform, native_id=native_id, entity_id="youdoo", text=text,
            author=author, publish_ts="2026-07-13T10:00:00+08:00",
            fetched_at="2026-07-13T11:00:00+08:00", url=f"https://example.com/{native_id}",
        )
        store.add_clean(doc)
        store.add_feature(doc.doc_id, {
            "polarity": polarity, "confidence": confidence, "risk": risk,
            "is_ironic": ironic, "signals": signals, "topic_label": "体验",
        })
        doc_ids.append(doc.doc_id)
    store.commit()
    return doc_ids


class ReviewHTTPTest(unittest.TestCase):
    PUBLIC_HOST = "cyber.youdoogo.com"
    PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
    INTERNAL_HOST = "cyber-intelligence:8080"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        self.doc_ids = seed_reviews(self.db)
        dashboard._SESSION_DB = self.db
        dashboard._session_init(self.db)
        dashboard._session_save("review-session", {"open_id": "ou_test", "name": "值班分析师"})
        self.env = mock.patch.dict(os.environ, {
            "YUQING_CONFIG": os.path.join(self.tmp.name, "missing-config.json"),
            "FEISHU_REDIRECT_URI": f"{self.PUBLIC_ORIGIN}/auth/callback",
        })
        self.env.start()
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def request(self, method: str, path: str, *, headers: dict | None = None,
                payload: dict | None = None) -> tuple[int, bytes]:
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
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
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: None
        handler.end_headers = lambda: None
        handler.send_error = lambda code, message=None, explain=None: setattr(handler, "response_code", code)
        getattr(handler, f"do_{method}")()
        return handler.response_code, handler.wfile.getvalue()

    def local_headers(self) -> dict:
        return {"Host": "127.0.0.1:8000"}

    def mutation_headers(self, origin: str | None = None) -> dict:
        headers = {
            "Host": self.INTERNAL_HOST,
            "X-Forwarded-Host": self.PUBLIC_HOST,
            "X-Forwarded-Proto": "https",
            "Sec-Fetch-Site": "same-origin",
            "Cookie": "yuqing_sid=review-session",
            "Content-Type": "application/json",
        }
        if origin is not None:
            headers["Origin"] = origin
        return headers

    def test_list_filter_pagination_and_invalid_parameter(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            status, body = self.request(
                "GET", "/api/v1/reviews?platform=weibo&limit=1", headers=self.local_headers(),
            )
            bad_status, bad_body = self.request(
                "GET", "/api/v1/reviews?confidence=unknown", headers=self.local_headers(),
            )
            cursor_status, cursor_body = self.request(
                "GET", "/api/v1/reviews?cursor=not-a-valid-cursor", headers=self.local_headers(),
            )
        payload = json.loads(body)
        self.assertEqual(200, status)
        self.assertTrue(payload["success"])
        self.assertEqual(1, payload["data"]["count"])
        self.assertEqual(2, payload["data"]["total"])
        self.assertIsNotNone(payload["data"]["next_cursor"])
        self.assertEqual(400, bad_status)
        self.assertEqual("INVALID_PARAMETER", json.loads(bad_body)["error"]["code"])
        self.assertEqual(400, cursor_status)
        self.assertEqual("INVALID_CURSOR", json.loads(cursor_body)["error"]["code"])

    def test_single_and_batch_reviews_persist_with_actor(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            single_status, single_body = self.request(
                "POST", f"/api/v1/reviews/{self.doc_ids[0]}",
                headers=self.mutation_headers(self.PUBLIC_ORIGIN),
                payload={"entity_id": "youdoo", "verdict": "ok", "note": "确认准确"},
            )
            batch_status, batch_body = self.request(
                "POST", "/api/v1/reviews/batch",
                headers=self.mutation_headers(self.PUBLIC_ORIGIN),
                payload={
                    "entity_id": "youdoo",
                    "items": [
                        {"doc_id": self.doc_ids[1], "verdict": "correct_negative"},
                        {"doc_id": "missing", "verdict": "ok"},
                    ],
                },
            )

        self.assertEqual(200, single_status, single_body.decode())
        self.assertEqual("approved", json.loads(single_body)["data"]["review"]["status"])
        self.assertEqual(200, batch_status, batch_body.decode())
        batch = json.loads(batch_body)["data"]
        self.assertEqual(1, batch["succeeded"])
        self.assertEqual(1, batch["failed"])

        store = Store(self.db)
        try:
            rows = store.conn.execute(
                "SELECT doc_id,verdict,actor FROM review ORDER BY rowid"
            ).fetchall()
        finally:
            store.close()
        self.assertEqual(2, len(rows))
        self.assertEqual("值班分析师", rows[0]["actor"])
        self.assertEqual("correct_negative", rows[1]["verdict"])

    def test_batch_limit_and_csrf_rejection_do_not_write(self) -> None:
        oversized = [{"doc_id": self.doc_ids[0], "verdict": "ok"} for _ in range(101)]
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            too_large_status, too_large_body = self.request(
                "POST", "/api/v1/reviews/batch",
                headers=self.mutation_headers(self.PUBLIC_ORIGIN), payload={"items": oversized},
            )
            csrf_status, csrf_body = self.request(
                "POST", f"/api/v1/reviews/{self.doc_ids[0]}",
                headers=self.mutation_headers("https://evil.example.com"),
                payload={"verdict": "ok"},
            )

        self.assertEqual(413, too_large_status)
        self.assertEqual("BATCH_TOO_LARGE", json.loads(too_large_body)["error"]["code"])
        self.assertEqual(403, csrf_status)
        self.assertEqual("FORBIDDEN", json.loads(csrf_body)["error"]["code"])
        store = Store(self.db)
        try:
            self.assertEqual(0, store.conn.execute("SELECT COUNT(*) FROM review").fetchone()[0])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
