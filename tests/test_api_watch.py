# -*- coding: utf-8 -*-
"""Monitoring configuration, keyword, and seed API tests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock

import yaml

from yuqing import dashboard, load_watch
from yuqing.api.responses import APIError
from yuqing.api.watch import (
    build_keywords,
    build_seeds,
    build_watch_config,
    mutate_keyword,
    mutate_seed,
    update_watch_config,
)
from yuqing.keywords import KeywordManager
from yuqing.store import Store


WATCH = {
    "platforms": ["weibo", "zhihu"],
    "entities": [
        {
            "id": "youdoo", "type": "self", "aliases": ["Youdoo Box"],
            "must_not": ["Doo Prime"], "crisis_boost": ["退款"],
        },
        {"id": "competitor", "type": "competitor", "aliases": ["Competitor"]},
    ],
}


def write_watch(path: Path) -> None:
    path.write_text(yaml.safe_dump(WATCH, allow_unicode=True, sort_keys=False), encoding="utf-8")


class WatchModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.watch_path = Path(self.tmp.name) / "watch.yaml"
        write_watch(self.watch_path)
        self.store = Store(Path(self.tmp.name) / "yuqing.db")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_watch_save_is_validated_backed_up_and_atomic(self) -> None:
        payload = {
            "platforms": ["weibo"],
            "entities": [
                {
                    "id": "youdoo", "aliases": ["Youdoo Box", "有度盒子"],
                    "must_not": ["Doo Prime"], "crisis_boost": ["退款", "召回"],
                    "track_users": [],
                },
                {
                    "id": "competitor", "aliases": ["Competitor"], "must_not": [],
                    "crisis_boost": [], "track_users": [],
                },
            ],
        }
        updated = update_watch_config(WATCH, payload, path=self.watch_path)

        self.assertEqual(["weibo"], updated["platforms"])
        self.assertEqual(["Youdoo Box", "有度盒子"], updated["entities"][0]["aliases"])
        self.assertTrue(Path(str(self.watch_path) + ".bak").exists())
        persisted = yaml.safe_load(self.watch_path.read_text(encoding="utf-8"))
        self.assertEqual(updated, persisted)

        before = self.watch_path.read_text(encoding="utf-8")
        with self.assertRaises(APIError):
            update_watch_config(updated, {**payload, "platforms": ["unknown"]}, path=self.watch_path)
        self.assertEqual(before, self.watch_path.read_text(encoding="utf-8"))

    def test_keyword_and_seed_mutations_persist(self) -> None:
        result = mutate_keyword(
            self.store, WATCH,
            {"action": "add", "word": "发热", "tag": "complaint", "weight": 0.8},
            entity_id="youdoo",
        )
        self.assertGreater(result["keyword_id"], 0)
        self.assertEqual("发热", build_keywords(self.store, WATCH, entity_id="youdoo")["items"][0]["word"])

        manager = KeywordManager(self.store)
        seed_id = manager.add_suggestion(
            "有度盒子", "seed_alias", "youdoo", score=0.9, reason="测试建议",
        )
        seed_result, updated = mutate_seed(
            self.store, WATCH, {"action": "approve", "id": seed_id},
            entity_id="youdoo", path=self.watch_path,
        )
        self.assertEqual("有度盒子", seed_result["word"])
        self.assertIn("有度盒子", updated["entities"][0]["aliases"])
        self.assertEqual([], build_seeds(self.store, updated, entity_id="youdoo")["items"])


class WatchHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        self.watch_path = Path(self.tmp.name) / "watch.yaml"
        write_watch(self.watch_path)
        Store(self.db).close()
        self.env = mock.patch.dict(os.environ, {"YUQING_WATCH_PATH": str(self.watch_path)})
        self.env.start()
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def request(
        self, method: str, path: str, body: dict | None = None, *, host: str = "127.0.0.1:8000",
    ) -> tuple[int, bytes]:
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        handler.headers["Host"] = host
        raw = json.dumps(body or {}).encode("utf-8") if method in {"POST", "PUT"} else b""
        if raw:
            handler.headers["Content-Length"] = str(len(raw))
            handler.headers["Content-Type"] = "application/json"
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: None
        handler.end_headers = lambda: None
        handler.send_error = lambda code, message=None, explain=None: setattr(handler, "response_code", code)
        getattr(handler, f"do_{method}")()
        return handler.response_code, handler.wfile.getvalue()

    def test_read_save_and_keyword_endpoints(self) -> None:
        status, body = self.request("GET", "/api/v1/watch?entity_id=youdoo")
        self.assertEqual(200, status)
        watch_payload = json.loads(body)
        self.assertEqual(2, len(watch_payload["data"]["entities"]))

        config = watch_payload["data"]
        config["platforms"] = ["weibo"]
        status, body = self.request("PUT", "/api/v1/watch", {
            "entity_id": "youdoo",
            "platforms": [item["id"] for item in config["platforms"]]
            if config["platforms"] and isinstance(config["platforms"][0], dict) else config["platforms"],
            "entities": config["entities"],
        })
        self.assertEqual(200, status)
        self.assertEqual(["weibo"], load_watch()["platforms"])

        status, body = self.request("POST", "/api/v1/keywords", {
            "entity_id": "youdoo", "action": "add", "word": "卡顿", "tag": "complaint",
        })
        self.assertEqual(200, status)
        self.assertTrue(json.loads(body)["success"])
        status, body = self.request("GET", "/api/v1/keywords?entity_id=youdoo")
        self.assertEqual("卡顿", json.loads(body)["data"]["items"][0]["word"])

    def test_remote_mutation_without_session_is_forbidden(self) -> None:
        for method, path, payload in (
            ("PUT", "/api/v1/watch", {"platforms": ["weibo"], "entities": []}),
            ("POST", "/api/v1/keywords", {"action": "add", "word": "测试", "tag": "related"}),
            ("POST", "/api/v1/seeds", {"action": "mine"}),
        ):
            with self.subTest(path=path):
                status, body = self.request(
                    method, path, payload, host="cyber-intelligence:8080",
                )
                self.assertEqual(403, status)
                self.assertEqual("FORBIDDEN", json.loads(body)["error"]["code"])


if __name__ == "__main__":
    unittest.main()
