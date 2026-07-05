# -*- coding: utf-8 -*-
"""yuqing —— 基于 Agent-Reach 的舆情监控与分析汇报系统（Phase 0 MVP）。

数据流：collect → store(SQLite) → analyze(Claude/规则) → score → report → 飞书。
离线自检：python -m yuqing.selfcheck
在线跑批：python -m yuqing.run  (需 opencli 登录态 / ANTHROPIC_API_KEY / FEISHU_WEBHOOK)
"""

__version__ = "0.1.0"


def load_watch(path: str = "watch.yaml") -> dict:
    """加载监控配置（single source of truth）。

    找不到指定路径时回退到包内自带的 watch.yaml，避免从任意 cwd 运行 CLI 即崩。
    """
    try:
        import yaml
    except ImportError as e:  # pragma: no cover
        raise SystemExit("需要 PyYAML：pip install pyyaml") from e
    import os
    if not os.path.exists(path):
        packaged = os.path.join(os.path.dirname(__file__), "watch.yaml")
        if os.path.exists(packaged):
            path = packaged
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
