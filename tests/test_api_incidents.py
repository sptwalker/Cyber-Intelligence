# -*- coding: utf-8 -*-
"""Incident list/detail and state-machine API tests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from email.message import Message
from unittest import mock

from yuqing import dashboard
from yuqing.api.incidents import build_incident_list
from yuqing.store import Store


WATCH = {
    "platforms": ["weibo"],
    "entities": [{"id": "youdoo", "type": "self", "aliases": ["Youdoo Box"]}],
}


def seed_incident(path: str) -> str:
    store = Store(path)
    try:
        item = store.create_incident(
            entity_id="youdoo", cluster_key="refund", level="P1", doc_id="doc-1",
            summary="退款投诉增加", ts="2026-07-13T10:00:00+08:00",
        )
        store.commit()
        return item["incident_id"]
    finally:
        store.close()


class IncidentReadModelTest(unittest.TestCase):
    def test_allowed_actions_are_derived_from_server_state(self) -> None:
        store = Store(":memory:")
        try:
            store.create_incident(
                entity_id="youdoo", cluster_key="x", level="P1", doc_id="doc",
                summary="待确认", ts="2026-07-13T10:00:00+08:00",
            )
            data, quality, _ = build_incident_list(store, WATCH)
        finally:
            store.close()

        self.assertEqual("unknown", quality)
        self.assertEqual({"confirm", "suppress"}, {
            item["action"] for item in data["items"][0]["allowed_actions"]
        })


class IncidentHTTPTest(unittest.TestCase):
    PUBLIC_HOST = "cyber.youdoogo.com"
    PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
    INTERNAL_HOST = "cyber-intelligence:8080"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        self.incident_id = seed_incident(self.db)
        dashboard._SESSION_DB = self.db
        dashboard._session_init(self.db)
        dashboard._session_save("incident-session", {"open_id": "ou_test", "name": "值班分析师"})
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
            "Cookie": "yuqing_sid=incident-session",
            "Content-Type": "application/json",
        }
        if origin is not None:
            headers["Origin"] = origin
        return headers

    def test_list_detail_and_valid_transition(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            list_status, list_body = self.request("GET", "/api/v1/incidents", headers=self.local_headers())
            detail_status, detail_body = self.request(
                "GET", f"/api/v1/incidents/{self.incident_id}", headers=self.local_headers(),
            )
            transition_status, transition_body = self.request(
                "POST", f"/api/v1/incidents/{self.incident_id}/transition",
                headers=self.mutation_headers(self.PUBLIC_ORIGIN),
                payload={"action": "confirm", "note": "已核实"},
            )

        self.assertEqual(200, list_status)
        self.assertEqual(1, json.loads(list_body)["data"]["count"])
        self.assertEqual(200, detail_status)
        self.assertEqual("pending_confirmation", json.loads(detail_body)["data"]["incident"]["status"])
        self.assertEqual(200, transition_status)
        transitioned = json.loads(transition_body)["data"]["incident"]
        self.assertEqual("confirmed", transitioned["status"])
        self.assertEqual({"escalate", "resolve"}, {item["action"] for item in transitioned["allowed_actions"]})

        store = Store(self.db)
        try:
            saved = store.get_incident(self.incident_id)
        finally:
            store.close()
        self.assertEqual("值班分析师", saved["actor"])
        self.assertEqual("已核实", saved["note"])

    def test_invalid_transition_and_csrf_are_rejected_without_write(self) -> None:
        with mock.patch("yuqing.load_watch", return_value=WATCH):
            invalid_status, invalid_body = self.request(
                "POST", f"/api/v1/incidents/{self.incident_id}/transition",
                headers=self.mutation_headers(self.PUBLIC_ORIGIN), payload={"action": "resolve"},
            )
            csrf_status, csrf_body = self.request(
                "POST", f"/api/v1/incidents/{self.incident_id}/transition",
                headers=self.mutation_headers("https://evil.example.com"), payload={"action": "confirm"},
            )

        self.assertEqual(409, invalid_status)
        self.assertEqual("INVALID_TRANSITION", json.loads(invalid_body)["error"]["code"])
        self.assertEqual(403, csrf_status)
        self.assertEqual("FORBIDDEN", json.loads(csrf_body)["error"]["code"])
        store = Store(self.db)
        try:
            self.assertEqual("pending_confirmation", store.get_incident(self.incident_id)["status"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
