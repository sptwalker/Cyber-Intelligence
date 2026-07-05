# -*- coding: utf-8 -*-
"""yuqing —— 基于 Agent-Reach 的舆情监控与分析汇报系统（Phase 0 MVP）。

数据流：collect → store(SQLite) → analyze(Claude/规则) → score → report → 飞书。
离线自检：python -m yuqing.selfcheck
在线跑批：python -m yuqing.run  (需 opencli 登录态 / ANTHROPIC_API_KEY / FEISHU_WEBHOOK)
"""

__version__ = "0.1.0"


def load_watch(path: str = "watch.yaml") -> dict:
    """加载监控配置（single source of truth）。"""
    try:
        import yaml
    except ImportError as e:  # pragma: no cover
        raise SystemExit("需要 PyYAML：pip install pyyaml") from e
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
