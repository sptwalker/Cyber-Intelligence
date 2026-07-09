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
from .report import build_report, push_report_notice, report_url, validate_citations
from .store import Store


def main(watch_path: str = "watch.yaml", db: str = "yuqing.db",
         on_progress=None, should_stop=None) -> int:
    watch = load_watch(watch_path)
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    run_id = "run-" + now
    store = Store(db)
    try:
        health_by_platform = collect_all(store, watch, run_id=run_id, now=now,
                                          on_progress=on_progress, should_stop=should_stop)
        if on_progress:
            on_progress(None, "_analyze")
        n = analyze_pending(store, now=now)
        from . import embed
        if on_progress:
            on_progress(None, "_embed")
        n_vec = embed.ensure_embeddings(store, now=now)   # 语义向量化(有 embed key 才算,缓存,降级返回0)
        self_ids = {e["id"] for e in watch["entities"] if e.get("type", "self") == "self"}
        alerts = dispatch_alerts(store, now=now, health_by_platform=health_by_platform,
                                 self_entities=self_ids)
        if on_progress:
            on_progress(None, "_report")
        md = build_report(store, watch, run_id=run_id, now=now,
                          health_by_platform=health_by_platform)
        bad = validate_citations(md, store)
        if bad:
            print(f"[!] 引用校验失败，存在不存在的 doc_id：{bad}", file=sys.stderr)
            return 2
        # 运行模式：daily 推"报告已更新+链接"到飞书；training 安静迭代不推（避免调参期刷屏）
        from . import config
        _mode = config.mode()
        pushed = push_report_notice(run_id) if _mode == "daily" else "训练模式跳过"
        print(f"[{_mode}] 采集健康：{health_by_platform}｜新分析 {n} 条｜向量化 {n_vec} 条｜实时预警 {len(alerts)} 条｜飞书通知：{pushed}")
        print(f"报告已存库 run_id={run_id}｜查看：{report_url(run_id)}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
