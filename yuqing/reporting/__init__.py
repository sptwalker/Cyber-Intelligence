# -*- coding: utf-8 -*-
"""报告流水线的内部边界。

外部调用继续使用 :mod:`yuqing.report`；本包仅承载聚合、成文和投递实现，避免把
网络 I/O 与确定性的报告计算混在同一个模块中。
"""

from .aggregation import aggregate, sov
from .delivery import (
    push_feishu,
    push_feishu_alert_card,
    push_feishu_card,
    push_report_notice,
    report_url,
    validate_citations,
)
from .rendering import build_report

__all__ = [
    "aggregate",
    "sov",
    "build_report",
    "validate_citations",
    "push_feishu",
    "push_feishu_card",
    "push_feishu_alert_card",
    "report_url",
    "push_report_notice",
]
