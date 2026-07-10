# -*- coding: utf-8 -*-
"""Dashboard OAuth/CSRF regression tests using in-memory stdlib handler requests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from email.message import Message
from unittest import mock

from yuqing import dashboard
from yuqing.store import Store


class Dashboard403RegressionTest(unittest.TestCase):
    PUBLIC_HOST = "cyber.youdoogo.com"
    PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
    INTERNAL_HOST = "cyber-intelligence:8080"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "yuqing.db")
        Store(self.db).close()
        dashboard._SESSION_DB = self.db
        dashboard._session_init(self.db)
        dashboard._oauth_states.clear()
        self.env = mock.patch.dict(os.environ, {
            "YUQING_CONFIG": os.path.join(self.tmp.name, "missing-config.json"),
            "FEISHU_REDIRECT_URI": f"{self.PUBLIC_ORIGIN}/auth/callback",
        })
        self.env.start()
        self.handler_class = dashboard.make_handler(self.db)

    def tearDown(self) -> None:
        self.env.stop()
        dashboard._oauth_states.clear()
        self.tmp.cleanup()

    def request(self, method: str, path: str, *, headers: dict | None = None,
                body: bytes | None = None) -> tuple[int, dict, bytes]:
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.headers = Message()
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        handler.rfile = io.BytesIO(body or b"")
        handler.wfile = io.BytesIO()
        response_headers = {}
        handler.send_response = lambda code, message=None: setattr(handler, "response_code", code)
        handler.send_header = lambda key, value: response_headers.__setitem__(key, value)
        handler.end_headers = lambda: None
        handler.send_error = lambda code, message=None, explain=None: setattr(handler, "response_code", code)

        getattr(handler, f"do_{method}")()
        return handler.response_code, response_headers, handler.wfile.getvalue()

    def oauth_cookie(self) -> str:
        state = dashboard._new_state("/")
        with mock.patch.object(dashboard, "_feishu_user_access_token", return_value="token"), \
                mock.patch.object(dashboard, "_feishu_user_info",
                                  return_value={"open_id": "ou_test", "name": "测试用户"}):
            status, headers, _ = self.request(
                "GET", f"/auth/callback?code=test-code&state={state}",
                headers={"Host": self.INTERNAL_HOST},
            )
        self.assertEqual(302, status)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)
        return cookie.split(";", 1)[0]

    def proxy_headers(self, cookie: str, *, origin: str | None = None) -> dict:
        headers = {
            "Host": self.INTERNAL_HOST,
            "X-Forwarded-Host": self.PUBLIC_HOST,
            "X-Forwarded-Proto": "https",
            "Sec-Fetch-Site": "same-origin",
            "Cookie": cookie,
            "Content-Type": "application/json",
        }
        if origin is not None:
            headers["Origin"] = origin
        return headers

    def test_oauth_session_can_write_through_https_reverse_proxy(self) -> None:
        cookie = self.oauth_cookie()
        body = json.dumps({
            "action": "add", "author": "官方账号", "subject_type": "官方", "platform": "weibo",
        }).encode("utf-8")
        headers = self.proxy_headers(cookie, origin=self.PUBLIC_ORIGIN)
        headers["Content-Length"] = str(len(body))

        status, _, payload = self.request("POST", "/api/accounts", headers=headers, body=body)

        self.assertEqual(200, status, payload.decode("utf-8", "replace"))
        self.assertTrue(json.loads(payload)["success"])
        store = Store(self.db)
        try:
            self.assertEqual("官方账号", store.list_accounts()[0]["author"])
        finally:
            store.close()

    def test_remote_write_still_rejects_csrf_and_missing_session(self) -> None:
        cookie = self.oauth_cookie()
        body = b'{"action":"delete","id":1}'

        cases = [
            self.proxy_headers(cookie, origin="https://evil.example.com"),
            self.proxy_headers(cookie, origin="http://cyber.youdoogo.com"),
            self.proxy_headers(cookie, origin=None),
            self.proxy_headers("yuqing_sid=missing", origin=self.PUBLIC_ORIGIN),
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                headers["Content-Length"] = str(len(body))
                status, _, _ = self.request("POST", "/api/accounts", headers=headers, body=body)
                self.assertEqual(403, status)

    def test_forwarded_public_host_must_match_configured_oauth_origin(self) -> None:
        cookie = self.oauth_cookie()
        body = b'{"action":"delete","id":1}'
        headers = self.proxy_headers(cookie, origin=self.PUBLIC_ORIGIN)
        headers["X-Forwarded-Host"] = "attacker.example.com"
        headers["Content-Length"] = str(len(body))

        status, _, _ = self.request("POST", "/api/accounts", headers=headers, body=body)

        self.assertEqual(403, status)


if __name__ == "__main__":
    unittest.main()
