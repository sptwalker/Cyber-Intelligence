# -*- coding: utf-8 -*-
"""yuqing —— 基于 Agent-Reach 的舆情监控与分析汇报系统（Phase 0 MVP）。

数据流：collect → store(SQLite) → analyze(Claude/规则) → score → report → 飞书。
离线自检：python -m yuqing.selfcheck
在线跑批：python -m yuqing.run  (需 opencli 登录态 / ANTHROPIC_API_KEY / FEISHU_WEBHOOK)
"""

__version__ = "0.1.0"

from .watch_config import load_watch, watch_path

# Windows 控制台默认 GBK，编不了 ✓/⚠/🚨/█ 及真实帖子里的 emoji，会让 print 崩溃。
# 导入本包即把 stdout/stderr 切到 UTF-8（errors=replace 兜底），所有入口无需再手动 -X utf8。
import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
