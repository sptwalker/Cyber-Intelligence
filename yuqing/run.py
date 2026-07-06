# -*- coding: utf-8 -*-
"""在线跑批驱动：collect → analyze → score → report → 飞书。

需要：opencli 已登录 Chrome。分析引擎优先级：deepseek+MiniMax 交叉分析（DEEPSEEK_API_KEY/
MINIMAX_API_KEY）> Claude（ANTHROPIC_API_KEY）> 规则兜底。FEISHU_WEBHOOK 无则只落库。
无人值守常驻见 yuqing.scheduler（serve 循环 + deadman 心跳 + 登录态失效告警）。
"""

from __future__ import annotations

import datetime as _dt
import sys

from . import load_watch
from .alerts import dispatch as dispatch_alerts
from .analyze import analyze_pending
from .collect import collect_all
from .insights import oneliner
from .report import build_report, push_feishu, validate_citations
from .store import Store


def main(watch_path: str = "watch.yaml", db: str = "yuqing.db") -> int:
    watch = load_watch(watch_path)
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    run_id = "run-" + now
    store = Store(db)
    try:
        health_by_platform = collect_all(store, watch, run_id=run_id, now=now)
        n = analyze_pending(store, now=now)
        self_ids = {e["id"] for e in watch["entities"] if e.get("type", "self") == "self"}
        alerts = dispatch_alerts(store, now=now, health_by_platform=health_by_platform,
                                 self_entities=self_ids)
        md = build_report(store, watch, run_id=run_id, now=now,
                          health_by_platform=health_by_platform)
        bad = validate_citations(md, store)
        if bad:
            print(f"[!] 引用校验失败，存在不存在的 doc_id：{bad}", file=sys.stderr)
            return 2
        pushed = push_feishu(md)
        push_feishu(oneliner(store, watch), title="老板一句话日报")   # IM 群每日简讯
        print(f"采集健康：{health_by_platform}｜新分析 {n} 条｜实时预警 {len(alerts)} 条｜飞书推送：{pushed}")
        print(f"报告已存库 run_id={run_id}，引用校验通过。")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
