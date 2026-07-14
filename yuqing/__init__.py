# -*- coding: utf-8 -*-
"""yuqing —— 基于 Agent-Reach 的舆情监控与分析汇报系统（Phase 0 MVP）。

数据流：collect → store(SQLite) → analyze(Claude/规则) → score → report → 飞书。
离线自检：python -m yuqing.selfcheck
在线跑批：python -m yuqing.run  (需 opencli 登录态 / ANTHROPIC_API_KEY / FEISHU_WEBHOOK)
"""

__version__ = "0.1.0"

# Windows 控制台默认 GBK，编不了 ✓/⚠/🚨/█ 及真实帖子里的 emoji，会让 print 崩溃。
# 导入本包即把 stdout/stderr 切到 UTF-8（errors=replace 兜底），所有入口无需再手动 -X utf8。
import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def watch_path(path: str = "watch.yaml") -> str:
    """解析实际生效的 watch.yaml 路径：优先 cwd，缺则回退包内自带（与 load_watch 同源）。"""
    import os
    configured = os.getenv("YUQING_WATCH_PATH", "").strip()
    if configured:
        return configured
    if os.path.exists(path):
        return path
    packaged = os.path.join(os.path.dirname(__file__), "watch.yaml")
    return packaged if os.path.exists(packaged) else path


def load_watch(path: str = "watch.yaml") -> dict:
    """加载监控配置（single source of truth）。

    找不到指定路径时回退到包内自带的 watch.yaml，避免从任意 cwd 运行 CLI 即崩。
    """
    try:
        import yaml
    except ImportError as e:  # pragma: no cover
        raise SystemExit("需要 PyYAML：pip install pyyaml") from e
    import os
    resolved = watch_path(path)
    if not os.path.exists(resolved) and os.getenv("YUQING_WATCH_PATH"):
        resolved = os.path.join(os.path.dirname(__file__), "watch.yaml")
    with open(resolved, encoding="utf-8") as f:
        return yaml.safe_load(f)
