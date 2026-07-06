# -*- coding: utf-8 -*-
"""全局成本配额熔断：防平台改版触发重试风暴/死循环把 LLM 账单打爆。

按天累计 LLM 调用数与 token，超过上限即抛 BudgetExceeded，让上层降级（跳过深抽/规则兜底）
而不是继续烧钱。上限走 env（YUQING_MAX_CALLS / YUQING_MAX_TOKENS）。
"""

from __future__ import annotations

import os

from .store import Store
from . import config


class BudgetExceeded(Exception):
    pass


def _limits() -> tuple[int, int]:
    return (int(config.resolve("YUQING_MAX_CALLS") or "5000"),
            int(config.resolve("YUQING_MAX_TOKENS") or "20000000"))


def guard(store: Store, day: str, *, add_calls: int = 1, add_tokens: int = 0) -> None:
    """预扣配额；若本次会超限则抛 BudgetExceeded（不计入），否则累加。"""
    max_calls, max_tokens = _limits()
    calls, tokens = store.usage_today(day)
    if calls + add_calls > max_calls or tokens + add_tokens > max_tokens:
        raise BudgetExceeded(
            f"当日配额已满：calls {calls}/{max_calls}, tokens {tokens}/{max_tokens}")
    store.add_usage(day, add_calls, add_tokens)


if __name__ == "__main__":
    os.environ["YUQING_MAX_CALLS"] = "2"
    s = Store(":memory:")
    guard(s, "2026-07-06"); guard(s, "2026-07-06")   # 2 次 OK
    try:
        guard(s, "2026-07-06")                        # 第 3 次超限
        raise AssertionError("应已熔断")
    except BudgetExceeded:
        pass
    assert s.usage_today("2026-07-06")[0] == 2        # 超限不计入
    print("OK budget: 配额熔断生效")
