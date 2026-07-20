# -*- coding: utf-8 -*-
"""报告流水线拆分后的兼容边界与投递 payload 测试。"""

from __future__ import annotations

import inspect
import json
import unittest
from unittest import mock

from yuqing import report
from yuqing.reporting import aggregation, delivery, rendering


PUBLIC = {
    "aggregate",
    "sov",
    "build_report",
    "validate_citations",
    "push_feishu",
    "push_feishu_card",
    "push_feishu_alert_card",
    "report_url",
    "push_report_notice",
}


class ReportBoundariesTest(unittest.TestCase):
    def test_report_is_a_compatible_facade(self) -> None:
        self.assertEqual(PUBLIC, set(report.__all__))
        self.assertEqual("yuqing.reporting.aggregation", aggregation.aggregate.__module__)
        self.assertEqual("yuqing.reporting.rendering", rendering.build_report.__module__)
        self.assertEqual("yuqing.reporting.delivery", delivery.push_feishu.__module__)
        for name in PUBLIC:
            with self.subTest(name=name):
                signature = inspect.signature(getattr(report, name))
                self.assertFalse(any(parameter.startswith("_") for parameter in signature.parameters))
                self.assertEqual("yuqing.report", getattr(report, name).__module__)

    def test_aggregation_facade_forwards_keyword_boundary(self) -> None:
        store = object()
        with mock.patch.object(report._aggregation, "aggregate", return_value={"n_total": 3}) as aggregate:
            result = report.aggregate(store, "brand", since_day="2026-07-14")
        self.assertEqual({"n_total": 3}, result)
        aggregate.assert_called_once_with(store, "brand", since_day="2026-07-14")

    def test_build_report_composes_through_facade_hooks(self) -> None:
        metrics = {
            "n_total": 1,
            "n_neg": 0,
            "neg_ratio": 0.0,
            "by_platform": {"weibo": {"total": 1, "neg": 0}},
            "top_neg": [],
            "top_topics": [],
            "n_degraded_neg": 0,
        }
        store = mock.Mock()
        watch = {
            "platforms": ["weibo"],
            "entities": [
                {"id": "brand", "aliases": ["品牌"], "type": "self"},
                {"id": "rival", "aliases": ["竞品"], "type": "competitor"},
            ],
        }
        sov_rows = [{"name": "品牌", "type": "self", "mentions": 1, "sov": 1.0, "nsr": 1.0}]
        with mock.patch.object(report, "aggregate", return_value=metrics) as aggregate, \
                mock.patch.object(report, "sov", return_value=sov_rows) as sov, \
                mock.patch.object(report, "_prose_stub", return_value="固定摘要") as prose, \
                mock.patch.object(report.health, "banner", return_value=""), \
                mock.patch.object(report.analytics, "negative_anomaly", return_value={"anomaly": False}), \
                mock.patch.object(report.analytics, "aspect_breakdown", return_value=[]), \
                mock.patch.object(report.analytics, "rising_topics", return_value=[]), \
                mock.patch.object(report.insights, "backlog", return_value=[]):
            markdown = report.build_report(
                store,
                watch,
                run_id="run-1",
                now="2026-07-20T10:00:00+08:00",
                health_by_platform={"weibo": "ok"},
                use_claude=False,
            )

        aggregate.assert_called_once_with(store, "brand", since_day="2026-07-14")
        sov.assert_called_once_with(store, watch, since_day="2026-07-14")
        prose.assert_called_once_with("品牌", metrics)
        store.save_report.assert_called_once_with("run-1", "2026-07-20T10:00:00+08:00", markdown)
        self.assertIn("固定摘要", markdown)
        self.assertIn("| 品牌 | 自有 | 1 | 100% | +1.00 |", markdown)

    def test_card_and_notice_keep_facade_monkeypatch_points(self) -> None:
        cards: list[tuple[dict, str | None]] = []

        def capture(card: dict, webhook: str | None) -> bool:
            cards.append((card, webhook))
            return True

        with mock.patch.object(report, "_feishu_send_card", side_effect=capture):
            self.assertTrue(report.push_feishu_card("标题", "摘要", "https://example/report", webhook="hook"))
            self.assertTrue(report.push_feishu_alert_card([
                {
                    "level": "P0",
                    "platform": "weibo",
                    "risk": 99,
                    "summary": "风险摘要",
                    "status": "pending_confirmation",
                    "incident_id": "inc-1",
                    "url": "https://example/post",
                },
            ], webhook="hook"))

        report_card, report_webhook = cards[0]
        self.assertEqual("hook", report_webhook)
        self.assertEqual("blue", report_card["header"]["template"])
        self.assertEqual("https://example/report", report_card["elements"][1]["actions"][0]["url"])
        alert_card, _ = cards[1]
        self.assertEqual("red", alert_card["header"]["template"])
        self.assertIn("待人工确认", alert_card["elements"][0]["content"])

        with mock.patch.object(report, "push_feishu_card", return_value=True) as push, \
                mock.patch.object(report, "report_url", return_value="https://example/report?run_id=r1") as url:
            self.assertTrue(report.push_report_notice("r1", title="通知"))
        url.assert_called_once_with("r1")
        push.assert_called_once_with(
            "通知",
            "舆情报告已更新，点击下方按钮查看完整报告。\n\n**run_id**：`r1`",
            "https://example/report?run_id=r1",
        )

    def test_text_delivery_payload_and_no_webhook_semantics(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        requests = []

        def urlopen(request, timeout):
            requests.append((request, timeout))
            return Response()

        with mock.patch("yuqing.reporting.delivery.urllib.request.urlopen", side_effect=urlopen):
            self.assertTrue(report.push_feishu("正文", "https://example/hook", title="标题"))
        request, timeout = requests[0]
        self.assertEqual(10, timeout)
        self.assertEqual("application/json", request.headers["Content-type"])
        self.assertEqual(
            {"msg_type": "text", "content": {"text": "【标题】\n正文"}},
            json.loads(request.data.decode()),
        )
        with mock.patch("yuqing.config.resolve", return_value=None):
            self.assertFalse(report.push_feishu("正文", webhook=""))


if __name__ == "__main__":
    unittest.main()
